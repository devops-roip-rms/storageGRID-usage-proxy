#!/usr/bin/env python3
"""StorageGRID Tenant API usage proxy for HTTP-SNIFFER.

Python 3.6+ and standard-library only.

The service owns the StorageGRID bearer token in memory. It refreshes and
validates a new token every 10 hours by default, then exposes a stable local
GET endpoint that HTTP-SNIFFER can call without storing the StorageGRID token.
"""

import argparse
import hmac
import ipaddress
import json
import logging
import os
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

LOG = logging.getLogger("storagegrid-usage-proxy")
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_ROOT / "config" / "proxy.env"


class ProxyError(RuntimeError):
    """Base error for safe, user-visible operational failures."""


class UpstreamHTTPError(ProxyError):
    """StorageGRID returned a non-2xx HTTP response."""

    def __init__(self, status, url):
        self.status = status
        self.url = url
        ProxyError.__init__(self, "StorageGRID returned HTTP {0} for {1}".format(status, url))


class UpstreamResponse(object):
    def __init__(self, status, body, content_type):
        self.status = status
        self.body = body
        self.content_type = content_type


class Config(object):
    def __init__(
        self,
        base_url,
        username,
        account_id,
        password,
        auth_path,
        usage_path,
        http_timeout,
        max_response_bytes,
        tls_verify,
        ca_bundle,
        refresh_interval_seconds,
        refresh_retry_seconds,
        bind_host,
        bind_port,
        proxy_api_key,
        allow_unauthenticated_nonloopback,
        log_level,
        stale_token_warning_seconds=None,
    ):
        self.base_url = base_url
        self.username = username
        self.account_id = account_id
        self.password = password
        self.auth_path = auth_path
        self.usage_path = usage_path
        self.http_timeout = http_timeout
        self.max_response_bytes = max_response_bytes
        self.tls_verify = tls_verify
        self.ca_bundle = ca_bundle
        self.refresh_interval_seconds = refresh_interval_seconds
        self.refresh_retry_seconds = refresh_retry_seconds
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.proxy_api_key = proxy_api_key
        self.allow_unauthenticated_nonloopback = allow_unauthenticated_nonloopback
        self.log_level = log_level
        self.stale_token_warning_seconds = (
            stale_token_warning_seconds
            if stale_token_warning_seconds is not None
            else max(3.0 * refresh_retry_seconds, 900.0)
        )

    @classmethod
    def from_env(cls):
        base_url = validate_base_url(required_env("STORAGEGRID_BASE_URL"))

        refresh_hours = env_float("TOKEN_REFRESH_HOURS", 10.0)
        if refresh_hours <= 0:
            raise ProxyError("TOKEN_REFRESH_HOURS must be greater than 0")
        if refresh_hours >= 16:
            LOG.warning(
                "TOKEN_REFRESH_HOURS is %.2f; configure a safety margin below the token lifetime",
                refresh_hours,
            )

        retry_seconds = env_float("REFRESH_RETRY_SECONDS", 300.0)
        if retry_seconds <= 0:
            raise ProxyError("REFRESH_RETRY_SECONDS must be greater than 0")

        stale_token_warning_seconds = env_float(
            "STALE_TOKEN_WARNING_SECONDS", max(3.0 * retry_seconds, 900.0)
        )
        if stale_token_warning_seconds <= 0:
            raise ProxyError("STALE_TOKEN_WARNING_SECONDS must be greater than 0")

        timeout = env_float("HTTP_TIMEOUT_SECONDS", 30.0)
        if timeout <= 0:
            raise ProxyError("HTTP_TIMEOUT_SECONDS must be greater than 0")

        port = env_int("PROXY_PORT", 8787)
        if port < 1 or port > 65535:
            raise ProxyError("PROXY_PORT must be between 1 and 65535")

        max_response_bytes = env_int("MAX_RESPONSE_BYTES", 10 * 1024 * 1024)
        if max_response_bytes < 1024:
            raise ProxyError("MAX_RESPONSE_BYTES must be at least 1024")

        ca_bundle_raw = os.getenv("CA_BUNDLE", "").strip()
        proxy_api_key = os.getenv("PROXY_API_KEY", "").strip() or None

        return cls(
            base_url=base_url,
            username=required_env("STORAGEGRID_USERNAME"),
            account_id=required_env("STORAGEGRID_ACCOUNT_ID"),
            password=required_env("STORAGEGRID_PASSWORD"),
            auth_path=normalize_path(os.getenv("AUTH_PATH", "/api/v4/authorize")),
            usage_path=normalize_path(os.getenv("USAGE_PATH", "/api/v4/org/usage")),
            http_timeout=timeout,
            max_response_bytes=max_response_bytes,
            tls_verify=env_bool("TLS_VERIFY", True),
            ca_bundle=(str(resolve_project_path(ca_bundle_raw)) if ca_bundle_raw else None),
            refresh_interval_seconds=refresh_hours * 3600.0,
            refresh_retry_seconds=retry_seconds,
            bind_host=os.getenv("PROXY_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1",
            bind_port=port,
            proxy_api_key=proxy_api_key,
            allow_unauthenticated_nonloopback=env_bool(
                "ALLOW_UNAUTHENTICATED_NONLOOPBACK", False
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            stale_token_warning_seconds=stale_token_warning_seconds,
        )


def resolve_project_path(value):
    """Resolve config paths relative to this dedicated project directory."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def load_env_file(path):
    """Load a simple KEY=VALUE file without third-party packages."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProxyError("Cannot read configuration file {0}: {1}".format(path, exc))

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ProxyError(
                "Invalid configuration line {0} in {1}: expected KEY=VALUE".format(
                    lineno, path
                )
            )
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            not key
            or not all(ch.isalnum() or ch == "_" for ch in key)
            or key[0].isdigit()
        ):
            raise ProxyError("Invalid configuration key on line {0} in {1}".format(lineno, path))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ[key] = value


def _looks_like_placeholder(value):
    upper = value.upper()
    if "CHANGE_ME" in upper:
        return True
    if "<" in value or ">" in value:
        return True
    if upper.startswith("YOUR_") or upper.startswith("REPLACE_"):
        return True
    return False


def required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ProxyError("Required environment variable is missing: {0}".format(name))
    if _looks_like_placeholder(value):
        raise ProxyError(
            "Required environment variable still contains a placeholder: {0}".format(name)
        )
    return value


def validate_base_url(value):
    """Validate and normalize the StorageGRID base URL."""
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ProxyError("STORAGEGRID_BASE_URL must be a valid http:// or https:// URL")
    if "<" in parsed.netloc or ">" in parsed.netloc:
        raise ProxyError("STORAGEGRID_BASE_URL still contains a placeholder")
    if parsed.query or parsed.fragment:
        raise ProxyError("STORAGEGRID_BASE_URL must not contain a query string or fragment")
    if parsed.path not in ("", "/"):
        raise ProxyError(
            "STORAGEGRID_BASE_URL must contain only scheme and host[:port]; "
            "put /api/v4/... values in AUTH_PATH and USAGE_PATH"
        )
    return value


def env_bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ProxyError("{0} must be true/false, got {1!r}".format(name, raw))


def env_float(name, default):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        raise ProxyError("{0} must be numeric, got {1!r}".format(name, raw))


def env_int(name, default):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ProxyError("{0} must be an integer, got {1!r}".format(name, raw))


def normalize_path(value):
    value = value.strip()
    if not value:
        raise ProxyError("API path cannot be empty")
    return value if value.startswith("/") else "/" + value


def build_ssl_context(cfg):
    if not cfg.tls_verify:
        LOG.warning("TLS certificate verification is DISABLED")
        return ssl._create_unverified_context()
    if cfg.ca_bundle:
        if not Path(cfg.ca_bundle).is_file():
            raise ProxyError("CA_BUNDLE file does not exist: {0}".format(cfg.ca_bundle))
        return ssl.create_default_context(cafile=cfg.ca_bundle)
    return ssl.create_default_context()


class StorageGridClient(object):
    def __init__(self, cfg, context):
        self.cfg = cfg
        self.context = context

    def _request(self, path, method, headers=None, json_body=None):
        url = self.cfg.base_url + path
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)

        data = None
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            url=url,
            data=data,
            headers=request_headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.cfg.http_timeout,
                context=self.context,
            ) as response:
                body = response.read(self.cfg.max_response_bytes + 1)
                if len(body) > self.cfg.max_response_bytes:
                    raise ProxyError(
                        "StorageGRID response exceeded MAX_RESPONSE_BYTES for {0}".format(url)
                    )
                status = int(response.status)
                content_type = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            try:
                exc.read(min(self.cfg.max_response_bytes, 4096))
            except Exception:
                pass
            raise UpstreamHTTPError(exc.code, url)
        except urllib.error.URLError as exc:
            raise ProxyError("Connection failure for {0}: {1}".format(url, exc.reason))
        except (TimeoutError, socket_timeout_error()) as exc:
            raise ProxyError("Timeout connecting to {0}".format(url))

        if status < 200 or status >= 300:
            raise UpstreamHTTPError(status, url)
        return UpstreamResponse(status, body, content_type)

    @staticmethod
    def _parse_json(response, purpose):
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProxyError("StorageGRID returned invalid JSON for {0}".format(purpose))

    def authorize(self):
        response = self._request(
            self.cfg.auth_path,
            method="POST",
            json_body={
                "accountId": self.cfg.account_id,
                "username": self.cfg.username,
                "password": self.cfg.password,
                "cookie": True,
                "csrfToken": False,
            },
        )
        payload = self._parse_json(response, "authorization")
        if not isinstance(payload, dict):
            raise ProxyError("StorageGRID authorization response must be a JSON object")
        if payload.get("status") not in (None, "success"):
            raise ProxyError("StorageGRID authorization response did not report success")
        return extract_token(payload)

    def fetch_usage(self, token):
        response = self._request(
            self.cfg.usage_path,
            method="GET",
            headers={"Authorization": "Bearer " + token},
        )
        # Validate JSON, but return the exact original bytes unchanged.
        self._parse_json(response, "usage")
        return response


def socket_timeout_error():
    # socket.timeout is an alias/subclass whose exact hierarchy varies across
    # Python versions. Import lazily to keep the exception tuple portable.
    import socket
    return socket.timeout


def extract_token(payload):
    data = payload.get("data")
    candidates = []
    if isinstance(data, str):
        candidates.append(data)
    elif isinstance(data, dict):
        candidates.extend(
            [data.get("token"), data.get("access_token"), data.get("accessToken")]
        )
    candidates.extend(
        [payload.get("token"), payload.get("access_token"), payload.get("accessToken")]
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ProxyError("Authorization response did not contain a recognizable token")


class TokenManager(object):
    """Owns the bearer token in RAM and refreshes it safely."""

    def __init__(self, cfg, client):
        self.cfg = cfg
        self.client = client
        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._token = None
        self._last_success_epoch = None
        self._next_refresh_monotonic = 0.0
        self._last_error = None
        self._last_error_started_epoch = None
        self._consecutive_refresh_failures = 0
        self._next_attempt_monotonic = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name="storagegrid-token-refresh",
        )
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def get_token(self):
        with self._state_lock:
            return self._token

    def _perform_refresh_locked(self):
        LOG.info("Requesting a new StorageGRID bearer token")
        try:
            candidate = self.client.authorize()
            LOG.info("Validating the new bearer token against %s", self.cfg.usage_path)
            self.client.fetch_usage(candidate)
        except Exception as exc:
            now_epoch = time.time()
            now_mono = time.monotonic()
            with self._state_lock:
                self._last_error = str(exc)
                if self._last_error_started_epoch is None:
                    self._last_error_started_epoch = now_epoch
                self._consecutive_refresh_failures += 1
                self._next_attempt_monotonic = now_mono + self.cfg.refresh_retry_seconds
            raise

        now_epoch = time.time()
        now_mono = time.monotonic()
        with self._state_lock:
            self._token = candidate
            self._last_success_epoch = now_epoch
            self._next_refresh_monotonic = now_mono + self.cfg.refresh_interval_seconds
            self._next_attempt_monotonic = self._next_refresh_monotonic
            self._last_error = None
            self._last_error_started_epoch = None
            self._consecutive_refresh_failures = 0
        LOG.info(
            "New StorageGRID token validated and installed in memory; next regular refresh in %.2f hours",
            self.cfg.refresh_interval_seconds / 3600.0,
        )
        return True

    def refresh(self, force=False):
        with self._refresh_lock:
            now_mono = time.monotonic()
            with self._state_lock:
                if not force and now_mono < self._next_attempt_monotonic:
                    return False
                if (
                    not force
                    and self._token is not None
                    and now_mono < self._next_refresh_monotonic
                ):
                    return False
            return self._perform_refresh_locked()

    def refresh_rejected_token(self, rejected_token):
        """Refresh a token rejected with 401, with outage backoff protection.

        A normally healthy token is allowed to refresh immediately even though its
        regular 10-hour refresh is not due. If that recovery attempt fails, later
        sniffer requests respect REFRESH_RETRY_SECONDS instead of hammering the
        authorize endpoint. Concurrent 401s for the same rejected token cause at
        most one reauthorization: followers observe the replacement token while
        holding _refresh_lock and reuse it.
        """
        with self._refresh_lock:
            now_mono = time.monotonic()
            with self._state_lock:
                if self._token != rejected_token:
                    return False
                if self._last_error is not None and now_mono < self._next_attempt_monotonic:
                    return False
            return self._perform_refresh_locked()

    def seconds_until_refresh(self):
        with self._state_lock:
            if self._token is None:
                return 0.0
            return max(0.0, self._next_refresh_monotonic - time.monotonic())

    def snapshot(self):
        now_epoch = time.time()
        with self._state_lock:
            last_success = self._last_success_epoch
            token_loaded = self._token is not None
            last_error = self._last_error
            last_error_started = self._last_error_started_epoch
            consecutive_failures = self._consecutive_refresh_failures
            next_seconds = (
                max(0.0, self._next_refresh_monotonic - time.monotonic())
                if token_loaded
                else 0.0
            )
        return {
            "token_loaded": token_loaded,
            "last_refresh_utc": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_success))
                if last_success is not None
                else None
            ),
            "seconds_until_refresh": int(next_seconds),
            "last_refresh_error": last_error is not None,
            "refresh_error_age_seconds": (
                int(max(0.0, now_epoch - last_error_started))
                if last_error_started is not None
                else None
            ),
            "seconds_since_last_success": (
                int(max(0.0, now_epoch - last_success))
                if last_success is not None
                else None
            ),
            "consecutive_refresh_failures": consecutive_failures,
        }

    def _refresh_loop(self):
        delay = 0.0
        while not self._stop_event.wait(delay):
            try:
                changed = self.refresh(force=False)
                if changed:
                    delay = self.cfg.refresh_interval_seconds
                else:
                    delay = max(1.0, self.seconds_until_refresh())
            except Exception as exc:
                LOG.error("Scheduled token refresh failed: %s", exc)
                delay = self.cfg.refresh_retry_seconds


def get_usage_with_recovery(client, manager):
    token = manager.get_token()
    if token is None:
        LOG.info("No bearer token is loaded; authorizing on demand")
        manager.refresh(force=False)
        token = manager.get_token()
        if token is None:
            raise ProxyError("No StorageGRID token is available after authorization")

    try:
        return client.fetch_usage(token)
    except UpstreamHTTPError as exc:
        if exc.status != 401:
            raise
        LOG.warning("StorageGRID rejected the current token with HTTP 401; reauthorizing once")
        manager.refresh_rejected_token(token)
        replacement = manager.get_token()
        if replacement is None:
            raise ProxyError("No StorageGRID token is available after HTTP 401 recovery")
        if replacement == token:
            raise ProxyError("StorageGRID token recovery is in retry backoff")
        return client.fetch_usage(replacement)


def is_loopback_bind(host):
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_bind_security(cfg):
    """Reject unauthenticated network binds unless explicitly overridden."""
    if is_loopback_bind(cfg.bind_host) or cfg.proxy_api_key is not None:
        return
    if not cfg.allow_unauthenticated_nonloopback:
        raise ProxyError(
            "PROXY_API_KEY is required when PROXY_BIND_HOST is non-loopback; "
            "set ALLOW_UNAUTHENTICATED_NONLOOPBACK=true only for an explicitly "
            "accepted insecure deployment"
        )
    LOG.critical(
        "DANGEROUS OVERRIDE: proxy is bound to non-loopback host %s without "
        "PROXY_API_KEY because ALLOW_UNAUTHENTICATED_NONLOOPBACK=true",
        cfg.bind_host,
    )


class ProxyApplication(object):
    def __init__(self, cfg, client, manager, proxy_api_key):
        self.cfg = cfg
        self.client = client
        self.manager = manager
        self.proxy_api_key = proxy_api_key

    def authorized(self, supplied):
        if self.proxy_api_key is None:
            return True
        if supplied is None:
            return False
        return hmac.compare_digest(supplied, self.proxy_api_key)


class ProxyHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, app):
        self.app = app
        HTTPServer.__init__(self, server_address, ProxyRequestHandler)


class ProxyRequestHandler(BaseHTTPRequestHandler):
    server_version = "StorageGRIDUsageProxy/1.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        LOG.debug("HTTP %s - %s", self.client_address[0], fmt % args)

    def _send_json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_upstream(self, response):
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response.body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path

        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return

        if path == "/readyz":
            snapshot = self.app.manager.snapshot()
            stale = (
                snapshot["refresh_error_age_seconds"] is not None
                and snapshot["refresh_error_age_seconds"]
                >= self.app.cfg.stale_token_warning_seconds
            )
            status = 200 if snapshot["token_loaded"] and not stale else 503
            snapshot["status"] = "ready" if status == 200 else "not_ready"
            self._send_json(status, snapshot)
            return

        if path == "/metrics":
            snapshot = self.app.manager.snapshot()
            self._send_json(
                200,
                {
                    "token_loaded": snapshot["token_loaded"],
                    "seconds_since_last_success": snapshot["seconds_since_last_success"],
                    "consecutive_refresh_failures": snapshot[
                        "consecutive_refresh_failures"
                    ],
                    "last_refresh_error": snapshot["last_refresh_error"],
                },
            )
            return

        if path != "/storagegrid/usage":
            self._send_json(404, {"error": "not_found"})
            return

        supplied_key = self.headers.get("X-StorageGRID-Proxy-Key")
        if not self.app.authorized(supplied_key):
            self._send_json(401, {"error": "unauthorized"})
            return

        try:
            response = get_usage_with_recovery(self.app.client, self.app.manager)
        except UpstreamHTTPError as exc:
            LOG.error("StorageGRID usage request failed: %s", exc)
            self._send_json(
                502,
                {"error": "storagegrid_http_error", "upstream_status": exc.status},
            )
            return
        except ProxyError as exc:
            LOG.error("StorageGRID usage request failed: %s", exc)
            self._send_json(502, {"error": "storagegrid_unavailable"})
            return
        except Exception:
            LOG.exception("Unexpected error while serving StorageGRID usage")
            self._send_json(500, {"error": "internal_error"})
            return

        self._send_upstream(response)


def run_server(cfg):
    validate_bind_security(cfg)
    context = build_ssl_context(cfg)
    client = StorageGridClient(cfg, context)
    manager = TokenManager(cfg, client)

    app = ProxyApplication(cfg, client, manager, cfg.proxy_api_key)
    server = ProxyHTTPServer((cfg.bind_host, cfg.bind_port), app)
    stopping = threading.Event()

    def request_stop(signum, _frame):
        if stopping.is_set():
            return
        stopping.set()
        LOG.info("Received signal %s; stopping", signum)
        thread = threading.Thread(target=server.shutdown)
        thread.daemon = True
        thread.start()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    manager.start()
    LOG.info(
        "StorageGRID usage proxy listening on http://%s:%d/storagegrid/usage",
        cfg.bind_host,
        cfg.bind_port,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        manager.stop()
    return 0


def configure_logging(level):
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Path to proxy.env (default: <project>/config/proxy.env)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate local configuration/TLS settings and exit",
    )
    parser.add_argument(
        "--test-upstream",
        action="store_true",
        help="Authorize once, validate with /org/usage, print safe result, and exit",
    )
    args = parser.parse_args()

    try:
        env_file = Path(args.env_file).expanduser().resolve()
        load_env_file(env_file)
        configure_logging(os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO")
        LOG.info("Loaded configuration from %s", env_file)
        cfg = Config.from_env()

        if args.check_config:
            validate_bind_security(cfg)
            build_ssl_context(cfg)
            print("configuration_ok=yes")
            return 0

        context = build_ssl_context(cfg)

        if args.test_upstream:
            client = StorageGridClient(cfg, context)
            token = client.authorize()
            response = client.fetch_usage(token)
            print("upstream_test=ok")
            print("usage_status={0}".format(response.status))
            print("usage_bytes={0}".format(len(response.body)))
            return 0

        return run_server(cfg)
    except ProxyError as exc:
        LOG.error("%s", exc)
        return 1
    except Exception:
        LOG.exception("Unexpected fatal failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())

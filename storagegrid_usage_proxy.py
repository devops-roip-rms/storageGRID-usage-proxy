#!/usr/bin/env python3
"""StorageGRID Tenant API usage proxy for HTTP-SNIFFER.

The service owns the short-lived StorageGRID bearer token in memory. It refreshes
and validates a new token on a configurable interval (10 hours by default), then
serves a stable local GET endpoint that HTTP-SNIFFER can call without knowing or
persisting the StorageGRID bearer token.

No third-party Python packages are required.
"""

from __future__ import annotations

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
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LOG = logging.getLogger("storagegrid-usage-proxy")
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_ROOT / "config" / "proxy.env"


class ProxyError(RuntimeError):
    """Base error for safe, user-visible operational failures."""


class UpstreamHTTPError(ProxyError):
    """StorageGRID returned a non-2xx HTTP response."""

    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.url = url
        super().__init__(f"StorageGRID returned HTTP {status} for {url}")


@dataclass(frozen=True)
class UpstreamResponse:
    status: int
    body: bytes
    content_type: str


@dataclass(frozen=True)
class Config:
    base_url: str
    username: str
    account_id: str
    password: str
    auth_path: str
    usage_path: str
    http_timeout: float
    max_response_bytes: int
    tls_verify: bool
    ca_bundle: str | None
    refresh_interval_seconds: float
    refresh_retry_seconds: float
    bind_host: str
    bind_port: int
    proxy_api_key: str | None
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        base_url = validate_base_url(required_env("STORAGEGRID_BASE_URL"))
        refresh_hours = env_float("TOKEN_REFRESH_HOURS", 10.0)
        if refresh_hours <= 0:
            raise ProxyError("TOKEN_REFRESH_HOURS must be greater than 0")
        if refresh_hours >= 16:
            LOG.warning(
                "TOKEN_REFRESH_HOURS is %.2f. StorageGRID bearer tokens are normally "
                "documented with a 16-hour lifetime; configure a safety margin.",
                refresh_hours,
            )

        retry_seconds = env_float("REFRESH_RETRY_SECONDS", 300.0)
        if retry_seconds <= 0:
            raise ProxyError("REFRESH_RETRY_SECONDS must be greater than 0")

        timeout = env_float("HTTP_TIMEOUT_SECONDS", 30.0)
        if timeout <= 0:
            raise ProxyError("HTTP_TIMEOUT_SECONDS must be greater than 0")

        port = env_int("PROXY_PORT", 8787)
        if port < 1 or port > 65535:
            raise ProxyError("PROXY_PORT must be between 1 and 65535")

        max_response_bytes = env_int("MAX_RESPONSE_BYTES", 10 * 1024 * 1024)
        if max_response_bytes < 1024:
            raise ProxyError("MAX_RESPONSE_BYTES must be at least 1024")

        password = required_env("STORAGEGRID_PASSWORD")
        proxy_api_key = os.getenv("PROXY_API_KEY", "").strip() or None
        ca_bundle_raw = os.getenv("CA_BUNDLE", "").strip()

        return cls(
            base_url=base_url,
            username=required_env("STORAGEGRID_USERNAME"),
            account_id=required_env("STORAGEGRID_ACCOUNT_ID"),
            password=password,
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
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )



def resolve_project_path(value: str) -> Path:
    """Resolve config paths relative to the dedicated project directory."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def load_env_file(path: Path) -> None:
    """Load a simple KEY=VALUE file without third-party dependencies.

    The selected env file is authoritative for keys it defines.
    Relative certificate paths are resolved later against PROJECT_ROOT.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProxyError(f"Cannot read configuration file {path}: {exc}") from exc

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ProxyError(f"Invalid configuration line {lineno} in {path}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not all(ch.isalnum() or ch == "_" for ch in key) or key[0].isdigit():
            raise ProxyError(f"Invalid configuration key on line {lineno} in {path}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]
        os.environ[key] = value


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ProxyError(f"Required environment variable is missing: {name}")
    if "CHANGE_ME" in value:
        raise ProxyError(f"Required environment variable still contains a placeholder: {name}")
    return value



def validate_base_url(value: str) -> str:
    """Validate and normalize the StorageGRID base URL."""
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProxyError("STORAGEGRID_BASE_URL must be a valid http:// or https:// URL")
    if parsed.query or parsed.fragment:
        raise ProxyError("STORAGEGRID_BASE_URL must not contain a query string or fragment")
    if parsed.path not in {"", "/"}:
        raise ProxyError(
            "STORAGEGRID_BASE_URL must contain only scheme and host[:port]; "
            "put /api/v4/... values in AUTH_PATH and USAGE_PATH"
        )
    return value

def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ProxyError(f"{name} must be true/false, got {raw!r}")


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ProxyError(f"{name} must be numeric, got {raw!r}") from exc


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ProxyError(f"{name} must be an integer, got {raw!r}") from exc


def normalize_path(value: str) -> str:
    value = value.strip()
    if not value:
        raise ProxyError("API path cannot be empty")
    return value if value.startswith("/") else "/" + value


def build_ssl_context(cfg: Config) -> ssl.SSLContext:
    if not cfg.tls_verify:
        LOG.warning("TLS certificate verification is DISABLED")
        return ssl._create_unverified_context()  # noqa: SLF001
    if cfg.ca_bundle:
        return ssl.create_default_context(cafile=cfg.ca_bundle)
    return ssl.create_default_context()


class StorageGridClient:
    def __init__(self, cfg: Config, context: ssl.SSLContext) -> None:
        self.cfg = cfg
        self.context = context

    def _request(
        self,
        path: str,
        *,
        method: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> UpstreamResponse:
        url = self.cfg.base_url + path
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)

        data: bytes | None = None
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
                        f"StorageGRID response exceeded MAX_RESPONSE_BYTES for {url}"
                    )
                status = int(response.status)
                content_type = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            # Do not include an upstream error body in logs/exceptions. Authentication
            # endpoints should never have a chance to echo sensitive request material.
            try:
                exc.read(min(self.cfg.max_response_bytes, 4096))
            except Exception:
                pass
            raise UpstreamHTTPError(exc.code, url) from exc
        except urllib.error.URLError as exc:
            raise ProxyError(f"Connection failure for {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProxyError(f"Timeout connecting to {url}") from exc

        if status < 200 or status >= 300:
            raise UpstreamHTTPError(status, url)
        return UpstreamResponse(status=status, body=body, content_type=content_type)

    @staticmethod
    def _parse_json(response: UpstreamResponse, purpose: str) -> Any:
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProxyError(f"StorageGRID returned invalid JSON for {purpose}") from exc

    def authorize(self) -> str:
        password = self.cfg.password
        response = self._request(
            self.cfg.auth_path,
            method="POST",
            json_body={
                "username": self.cfg.username,
                "password": password,
                "accountId": self.cfg.account_id,
            },
        )
        payload = self._parse_json(response, "authorization")
        if not isinstance(payload, dict):
            raise ProxyError("StorageGRID authorization response must be a JSON object")
        return extract_token(payload)

    def fetch_usage(self, token: str) -> UpstreamResponse:
        response = self._request(
            self.cfg.usage_path,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Validate JSON before returning data to HTTP-SNIFFER. The original bytes
        # are still returned unchanged so the proxy does not alter the payload.
        self._parse_json(response, "usage")
        return response


def extract_token(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    candidates: list[Any] = []
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


class TokenManager:
    """Owns the bearer token in RAM and refreshes it safely."""

    def __init__(self, cfg: Config, client: StorageGridClient) -> None:
        self.cfg = cfg
        self.client = client
        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._token: str | None = None
        self._last_success_epoch: float | None = None
        self._next_refresh_monotonic = 0.0
        self._last_error: str | None = None
        self._next_attempt_monotonic = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name="storagegrid-token-refresh",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def get_token(self) -> str | None:
        with self._state_lock:
            return self._token

    def _perform_refresh_locked(self) -> bool:
        LOG.info("Requesting a new StorageGRID bearer token")
        try:
            candidate = self.client.authorize()
            LOG.info("Validating the new bearer token against %s", self.cfg.usage_path)
            self.client.fetch_usage(candidate)
        except Exception as exc:
            now_mono = time.monotonic()
            with self._state_lock:
                self._last_error = str(exc)
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
        LOG.info(
            "New StorageGRID token validated and installed in memory; next regular "
            "refresh in %.2f hours",
            self.cfg.refresh_interval_seconds / 3600.0,
        )
        return True

    def refresh(self, *, force: bool = False) -> bool:
        """Authorize and validate a candidate token before installing it.

        Returns True when a new token was installed. Returns False when a normal
        refresh is not due or a previous failure is still in retry backoff. On
        failure, the previous token remains in RAM.
        """
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

    def refresh_rejected_token(self, rejected_token: str) -> bool:
        """Refresh only if the rejected token is still the active token.

        This prevents a burst of concurrent HTTP 401 responses from causing a
        burst of duplicate authorization requests.
        """
        with self._refresh_lock:
            with self._state_lock:
                if self._token != rejected_token:
                    return False
            return self._perform_refresh_locked()

    def seconds_until_refresh(self) -> float:
        with self._state_lock:
            if self._token is None:
                return 0.0
            return max(0.0, self._next_refresh_monotonic - time.monotonic())

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            last_success = self._last_success_epoch
            token_loaded = self._token is not None
            last_error = self._last_error
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
        }

    def _refresh_loop(self) -> None:
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


def get_usage_with_recovery(
    client: StorageGridClient,
    manager: TokenManager,
) -> UpstreamResponse:
    """Fetch live usage; reauthorize once if StorageGRID rejects the token with 401."""
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
        return client.fetch_usage(replacement)


def is_loopback_bind(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class ProxyApplication:
    def __init__(
        self,
        cfg: Config,
        client: StorageGridClient,
        manager: TokenManager,
        proxy_api_key: str | None,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.manager = manager
        self.proxy_api_key = proxy_api_key

    def authorized(self, supplied: str | None) -> bool:
        if self.proxy_api_key is None:
            return True
        if supplied is None:
            return False
        return hmac.compare_digest(supplied, self.proxy_api_key)


class ProxyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], app: ProxyApplication) -> None:
        self.app = app
        super().__init__(server_address, ProxyRequestHandler)


class ProxyRequestHandler(BaseHTTPRequestHandler):
    server_version = "StorageGRIDUsageProxy/1.0"

    @property
    def app(self) -> ProxyApplication:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.debug("HTTP %s - %s", self.client_address[0], fmt % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_upstream(self, response: UpstreamResponse) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response.body)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path

        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return

        if path == "/readyz":
            snapshot = self.app.manager.snapshot()
            status = 200 if snapshot["token_loaded"] else 503
            snapshot["status"] = "ready" if status == 200 else "not_ready"
            self._send_json(status, snapshot)
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
                {
                    "error": "storagegrid_http_error",
                    "upstream_status": exc.status,
                },
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


def load_proxy_api_key(cfg: Config) -> str | None:
    return cfg.proxy_api_key


def run_server(cfg: Config) -> int:
    context = build_ssl_context(cfg)
    client = StorageGridClient(cfg, context)
    manager = TokenManager(cfg, client)
    proxy_api_key = load_proxy_api_key(cfg)

    if not is_loopback_bind(cfg.bind_host) and proxy_api_key is None:
        LOG.warning(
            "Proxy is bound to non-loopback host %s without PROXY_API_KEY. "
            "Restrict access with the host firewall or configure a proxy API key.",
            cfg.bind_host,
        )

    app = ProxyApplication(cfg, client, manager, proxy_api_key)
    server = ProxyHTTPServer((cfg.bind_host, cfg.bind_port), app)

    stopping = threading.Event()

    def request_stop(signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        LOG.info("Received signal %s; stopping", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

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


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Path to proxy.env (default: <project>/config/proxy.env)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate local configuration/TLS settings and exit without starting the server",
    )
    parser.add_argument(
        "--test-upstream",
        action="store_true",
        help="Authorize once, validate with /org/usage, print a safe result, and exit",
    )
    args = parser.parse_args()

    try:
        env_file = Path(args.env_file).expanduser().resolve()
        load_env_file(env_file)
        configure_logging(os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO")
        LOG.info("Loaded configuration from %s", env_file)
        cfg = Config.from_env()
        load_proxy_api_key(cfg)
        context = build_ssl_context(cfg)
        if args.check_config:
            print("configuration_ok=yes")
            return 0
        if args.test_upstream:
            client = StorageGridClient(cfg, context)
            token = client.authorize()
            response = client.fetch_usage(token)
            print("upstream_test=ok")
            print(f"usage_status={response.status}")
            print(f"usage_bytes={len(response.body)}")
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

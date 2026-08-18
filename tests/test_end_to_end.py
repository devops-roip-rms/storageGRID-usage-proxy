import importlib.util
import json
import pathlib
import subprocess
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "storagegrid_usage_proxy.py"
spec = importlib.util.spec_from_file_location("storagegrid_usage_proxy_e2e", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
assert spec.loader
spec.loader.exec_module(mod)


class FakeStorageGridHandler(BaseHTTPRequestHandler):
    auth_count = 0
    usage_count = 0
    valid_token = None

    def log_message(self, fmt, *args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/v4/authorize":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if payload != {
            "username": "monitoring-user",
            "password": "secret-password",
            "accountId": "tenant-123",
        }:
            self._json(401, {"error": "bad_credentials"})
            return
        type(self).auth_count += 1
        token = f"TOKEN_{type(self).auth_count}"
        type(self).valid_token = token
        self._json(200, {"status": "success", "data": token})

    def do_GET(self):
        if self.path != "/api/v4/org/usage":
            self._json(404, {"error": "not_found"})
            return
        type(self).usage_count += 1
        expected = f"Bearer {type(self).valid_token}"
        if self.headers.get("Authorization") != expected:
            self._json(401, {"error": "invalid_token"})
            return
        self._json(200, {"data": {"objectCount": 42, "dataBytes": 2048}})


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        FakeStorageGridHandler.auth_count = 0
        FakeStorageGridHandler.usage_count = 0
        FakeStorageGridHandler.valid_token = None

    def test_cli_env_file_upstream_test(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeStorageGridHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                env_file = pathlib.Path(td) / "proxy.env"
                env_file.write_text(
                    "\n".join(
                        [
                            f"STORAGEGRID_BASE_URL=http://127.0.0.1:{upstream.server_port}",
                            "STORAGEGRID_USERNAME=monitoring-user",
                            "STORAGEGRID_ACCOUNT_ID=tenant-123",
                            "STORAGEGRID_PASSWORD=secret-password",
                            "AUTH_PATH=/api/v4/authorize",
                            "USAGE_PATH=/api/v4/org/usage",
                            "TLS_VERIFY=true",
                            "PROXY_BIND_HOST=127.0.0.1",
                            "PROXY_PORT=8787",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [sys.executable, str(MODULE_PATH), "--env-file", str(env_file), "--test-upstream"],
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("upstream_test=ok", result.stdout)
                self.assertIn("usage_status=200", result.stdout)
                self.assertNotIn("TOKEN_", result.stdout + result.stderr)
                self.assertNotIn("secret-password", result.stdout + result.stderr)
                self.assertEqual(FakeStorageGridHandler.auth_count, 1)
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)

    def test_real_http_flow_and_401_recovery(self):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeStorageGridHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()

        proxy = None
        proxy_thread = None
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = pathlib.Path(td)
                cfg = mod.Config(
                    base_url=f"http://127.0.0.1:{upstream.server_port}",
                    username="monitoring-user",
                    account_id="tenant-123",
                    password="secret-password",
                    auth_path="/api/v4/authorize",
                    usage_path="/api/v4/org/usage",
                    http_timeout=2.0,
                    max_response_bytes=1024 * 1024,
                    tls_verify=True,
                    ca_bundle=None,
                    refresh_interval_seconds=10 * 3600.0,
                    refresh_retry_seconds=300.0,
                    bind_host="127.0.0.1",
                    bind_port=0,
                    proxy_api_key=None,
                    log_level="INFO",
                )
                client = mod.StorageGridClient(cfg, ssl.create_default_context())
                manager = mod.TokenManager(cfg, client)
                manager.refresh(force=True)
                self.assertEqual(manager.get_token(), "TOKEN_1")
                self.assertEqual(FakeStorageGridHandler.auth_count, 1)

                app = mod.ProxyApplication(cfg, client, manager, None)
                proxy = mod.ProxyHTTPServer(("127.0.0.1", 0), app)
                proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
                proxy_thread.start()
                url = f"http://127.0.0.1:{proxy.server_port}/storagegrid/usage"

                with urllib.request.urlopen(url, timeout=2) as response:
                    first = json.loads(response.read().decode("utf-8"))
                self.assertEqual(first["data"]["objectCount"], 42)
                self.assertEqual(FakeStorageGridHandler.auth_count, 1)

                # Simulate server-side rejection/expiration of TOKEN_1. The proxy
                # must authorize once, validate TOKEN_2, then retry usage once.
                FakeStorageGridHandler.valid_token = "SERVER_INVALIDATED_TOKEN_1"
                with urllib.request.urlopen(url, timeout=2) as response:
                    recovered = json.loads(response.read().decode("utf-8"))
                self.assertEqual(recovered["data"]["dataBytes"], 2048)
                self.assertEqual(FakeStorageGridHandler.auth_count, 2)
                self.assertEqual(manager.get_token(), "TOKEN_2")
        finally:
            if proxy is not None:
                proxy.shutdown()
                proxy.server_close()
            if proxy_thread is not None:
                proxy_thread.join(timeout=2)
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

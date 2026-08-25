import importlib.util
import json
import pathlib
import os
import ssl
import sys
import tempfile
import unittest
import threading
import urllib.error
import urllib.request
from unittest import mock

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "storagegrid_usage_proxy.py"
spec = importlib.util.spec_from_file_location("storagegrid_usage_proxy", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
assert spec.loader
spec.loader.exec_module(mod)


def base_config(tmp: pathlib.Path) -> mod.Config:
    return mod.Config(
        base_url="https://storagegrid.example.invalid",
        username="monitoring-user",
        account_id="tenant-123",
        password="secret-password",
        auth_path="/api/v4/authorize",
        usage_path="/api/v4/org/usage",
        http_timeout=30.0,
        max_response_bytes=1024 * 1024,
        tls_verify=True,
        ca_bundle=None,
        refresh_interval_seconds=10 * 3600.0,
        refresh_retry_seconds=300.0,
        bind_host="127.0.0.1",
        bind_port=8787,
        proxy_api_key=None,
        allow_unauthenticated_nonloopback=False,
        log_level="INFO",
    )


def usage_response(payload=None):
    if payload is None:
        payload = {"data": {"objectCount": 10, "dataBytes": 20}}
    body = json.dumps(payload).encode("utf-8")
    return mod.UpstreamResponse(200, body, "application/json")


class EnvFileTests(unittest.TestCase):
    def test_env_file_is_authoritative_and_preserves_equals_hash(self):
        with tempfile.TemporaryDirectory() as td:
            env_file = pathlib.Path(td) / "proxy.env"
            env_file.write_text(
                "STORAGEGRID_USERNAME=file-user\n"
                "STORAGEGRID_PASSWORD=p@ss=word#123\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                "os.environ",
                {"STORAGEGRID_USERNAME": "shell-user", "STORAGEGRID_PASSWORD": "shell-password"},
                clear=True,
            ):
                mod.load_env_file(env_file)
                self.assertEqual(os.environ["STORAGEGRID_USERNAME"], "file-user")
                self.assertEqual(os.environ["STORAGEGRID_PASSWORD"], "p@ss=word#123")

    def test_placeholder_required_value_is_rejected(self):
        with mock.patch.dict("os.environ", {"X": "CHANGE_ME_VALUE"}, clear=True):
            with self.assertRaises(mod.ProxyError):
                mod.required_env("X")

    def test_angle_bracket_placeholder_is_rejected(self):
        with mock.patch.dict("os.environ", {"X": "<USERNAME>"}, clear=True):
            with self.assertRaises(mod.ProxyError):
                mod.required_env("X")

    def test_packaged_base_url_placeholder_is_rejected(self):
        with mock.patch.dict(
            "os.environ",
            {"STORAGEGRID_BASE_URL": "https://<STORAGEGRID_HOST_OR_IP>"},
            clear=True,
        ):
            with self.assertRaises(mod.ProxyError):
                mod.Config.from_env()

    def test_base_url_rejects_full_api_path(self):
        with self.assertRaises(mod.ProxyError):
            mod.validate_base_url("https://sg.example/api/v4/authorize")


class TokenExtractionTests(unittest.TestCase):
    def test_extracts_string_data(self):
        self.assertEqual(mod.extract_token({"data": "TOKEN"}), "TOKEN")

    def test_extracts_nested_token(self):
        self.assertEqual(mod.extract_token({"data": {"token": "TOKEN"}}), "TOKEN")

    def test_extracts_top_level_token(self):
        self.assertEqual(mod.extract_token({"token": "TOKEN"}), "TOKEN")

    def test_missing_token_fails(self):
        with self.assertRaises(mod.ProxyError):
            mod.extract_token({"data": {"other": "value"}})


class TokenManagerTests(unittest.TestCase):
    def test_refresh_validates_before_installing_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.return_value = "NEW_TOKEN"
            client.fetch_usage.return_value = usage_response()
            manager = mod.TokenManager(cfg, client)

            manager.refresh(force=True)

            client.authorize.assert_called_once_with()
            client.fetch_usage.assert_called_once_with("NEW_TOKEN")
            self.assertEqual(manager.get_token(), "NEW_TOKEN")

    def test_failed_validation_does_not_replace_existing_token(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.side_effect = ["OLD_TOKEN", "BAD_NEW_TOKEN"]
            client.fetch_usage.side_effect = [usage_response(), mod.ProxyError("validation failed")]
            manager = mod.TokenManager(cfg, client)

            manager.refresh(force=True)
            self.assertEqual(manager.get_token(), "OLD_TOKEN")

            with self.assertRaises(mod.ProxyError):
                manager.refresh(force=True)
            self.assertEqual(manager.get_token(), "OLD_TOKEN")

    def test_normal_refresh_is_not_repeated_before_ten_hours(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.return_value = "TOKEN"
            client.fetch_usage.return_value = usage_response()
            manager = mod.TokenManager(cfg, client)

            changed_first = manager.refresh(force=False)
            changed_second = manager.refresh(force=False)

            self.assertTrue(changed_first)
            self.assertFalse(changed_second)
            self.assertEqual(client.authorize.call_count, 1)
            self.assertGreater(manager.seconds_until_refresh(), 9 * 3600)

    def test_background_thread_refreshes_automatically_after_interval(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            cfg.refresh_interval_seconds = 0.10
            cfg.refresh_retry_seconds = 0.05
            client = mock.Mock()
            client.authorize.side_effect = ["TOKEN1", "TOKEN2", "TOKEN3", "TOKEN4"]
            client.fetch_usage.return_value = usage_response()
            manager = mod.TokenManager(cfg, client)
            manager.start()
            try:
                deadline = __import__("time").time() + 1.5
                while manager.get_token() not in ("TOKEN3", "TOKEN4") and __import__("time").time() < deadline:
                    __import__("time").sleep(0.02)
                self.assertGreaterEqual(client.authorize.call_count, 3)
                self.assertIn(manager.get_token(), ("TOKEN3", "TOKEN4"))
            finally:
                manager.stop()

    def test_background_thread_retries_after_refresh_failure(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            cfg.refresh_interval_seconds = 0.10
            cfg.refresh_retry_seconds = 0.05
            client = mock.Mock()
            client.authorize.side_effect = [mod.ProxyError("temporary failure"), "RECOVERED"]
            client.fetch_usage.return_value = usage_response()
            manager = mod.TokenManager(cfg, client)
            manager.start()
            try:
                deadline = __import__("time").time() + 1.5
                while manager.get_token() != "RECOVERED" and __import__("time").time() < deadline:
                    __import__("time").sleep(0.02)
                self.assertEqual(manager.get_token(), "RECOVERED")
                self.assertEqual(client.authorize.call_count, 2)
            finally:
                manager.stop()

    def test_token_never_appears_in_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.return_value = "SUPER_SECRET_TOKEN"
            client.fetch_usage.return_value = usage_response()
            manager = mod.TokenManager(cfg, client)
            manager.refresh(force=True)

            snapshot_text = json.dumps(manager.snapshot())
            self.assertNotIn("SUPER_SECRET_TOKEN", snapshot_text)

    def test_failed_initial_refresh_respects_retry_backoff(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.side_effect = mod.ProxyError("login failed")
            manager = mod.TokenManager(cfg, client)

            with self.assertRaises(mod.ProxyError):
                manager.refresh(force=False)
            changed = manager.refresh(force=False)

            self.assertFalse(changed)
            self.assertEqual(client.authorize.call_count, 1)
            self.assertIsNone(manager.get_token())

    def test_snapshot_does_not_expose_error_detail(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.side_effect = mod.ProxyError("sensitive internal detail")
            manager = mod.TokenManager(cfg, client)
            with self.assertRaises(mod.ProxyError):
                manager.refresh(force=True)

            snapshot = manager.snapshot()
            self.assertTrue(snapshot["last_refresh_error"])
            self.assertNotIn("sensitive internal detail", json.dumps(snapshot))

    def test_snapshot_reports_refresh_failure_age_and_count_without_error_detail(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.side_effect = ["TOKEN", mod.ProxyError("sensitive outage detail")]
            client.fetch_usage.return_value = usage_response()
            manager = mod.TokenManager(cfg, client)
            manager.refresh(force=True)

            with self.assertRaises(mod.ProxyError):
                manager.refresh(force=True)

            snapshot = manager.snapshot()
            self.assertEqual(snapshot["consecutive_refresh_failures"], 1)
            self.assertGreaterEqual(snapshot["refresh_error_age_seconds"], 0)
            self.assertGreaterEqual(snapshot["seconds_since_last_success"], 0)
            self.assertNotIn("sensitive outage detail", json.dumps(snapshot))


class UsageRecoveryTests(unittest.TestCase):
    def test_live_usage_uses_current_token(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.return_value = "TOKEN"
            client.fetch_usage.side_effect = [usage_response(), usage_response({"fresh": True})]
            manager = mod.TokenManager(cfg, client)
            manager.refresh(force=True)

            response = mod.get_usage_with_recovery(client, manager)

            self.assertEqual(json.loads(response.body), {"fresh": True})
            self.assertEqual(client.authorize.call_count, 1)
            self.assertEqual(client.fetch_usage.call_args_list[-1], mock.call("TOKEN"))

    def test_401_forces_reauthorize_and_retries_once(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            manager = mod.TokenManager(cfg, client)

            # Initial valid token installation.
            client.authorize.return_value = "OLD_TOKEN"
            client.fetch_usage.return_value = usage_response()
            manager.refresh(force=True)

            client.authorize.return_value = "NEW_TOKEN"
            client.fetch_usage.side_effect = [
                mod.UpstreamHTTPError(401, "https://sg/api/v4/org/usage"),
                usage_response({"validation": True}),
                usage_response({"after_reauth": True}),
            ]

            response = mod.get_usage_with_recovery(client, manager)

            self.assertEqual(manager.get_token(), "NEW_TOKEN")
            self.assertEqual(json.loads(response.body), {"after_reauth": True})
            self.assertEqual(client.fetch_usage.call_args_list[-1], mock.call("NEW_TOKEN"))

    def test_concurrent_401_recovery_reauthorizes_once(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            manager = mod.TokenManager(cfg, client)
            client.authorize.return_value = "OLD_TOKEN"
            client.fetch_usage.return_value = usage_response()
            manager.refresh(force=True)

            client.reset_mock()
            client.authorize.return_value = "NEW_TOKEN"
            old_token_requests = threading.Barrier(2)

            def fetch_usage(token):
                if token == "OLD_TOKEN":
                    old_token_requests.wait(timeout=2)
                    raise mod.UpstreamHTTPError(401, "https://sg/api/v4/org/usage")
                return usage_response({"recovered_with": token})

            client.fetch_usage.side_effect = fetch_usage
            results = []
            errors = []

            def request_usage():
                try:
                    results.append(mod.get_usage_with_recovery(client, manager))
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=request_usage) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)

            self.assertFalse(errors)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(client.authorize.call_count, 1)
            self.assertEqual(len(results), 2)
            for result in results:
                self.assertEqual(json.loads(result.body), {"recovered_with": "NEW_TOKEN"})

    def test_failed_401_reauthorization_enters_backoff(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            manager = mod.TokenManager(cfg, client)

            client.authorize.return_value = "OLD_TOKEN"
            client.fetch_usage.return_value = usage_response()
            manager.refresh(force=True)

            client.authorize.side_effect = mod.ProxyError("authorize unavailable")
            client.fetch_usage.side_effect = mod.UpstreamHTTPError(
                401, "https://sg/api/v4/org/usage"
            )

            with self.assertRaises(mod.ProxyError):
                mod.get_usage_with_recovery(client, manager)
            first_auth_count = client.authorize.call_count

            # A second poll during retry backoff must not call authorize again.
            with self.assertRaises(mod.ProxyError):
                mod.get_usage_with_recovery(client, manager)
            self.assertEqual(client.authorize.call_count, first_auth_count)

    def test_non_401_does_not_reauthorize(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            manager = mod.TokenManager(cfg, client)
            client.authorize.return_value = "TOKEN"
            client.fetch_usage.return_value = usage_response()
            manager.refresh(force=True)
            client.reset_mock()
            client.fetch_usage.side_effect = mod.UpstreamHTTPError(
                403, "https://sg/api/v4/org/usage"
            )

            with self.assertRaises(mod.UpstreamHTTPError):
                mod.get_usage_with_recovery(client, manager)

            client.authorize.assert_not_called()

    def test_no_token_authorizes_on_demand(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.return_value = "TOKEN"
            client.fetch_usage.side_effect = [usage_response({"validation": True}), usage_response({"live": True})]
            manager = mod.TokenManager(cfg, client)

            response = mod.get_usage_with_recovery(client, manager)

            self.assertEqual(manager.get_token(), "TOKEN")
            self.assertEqual(json.loads(response.body), {"live": True})


class ProxyAuthenticationTests(unittest.TestCase):
    def test_no_proxy_key_means_local_endpoint_auth_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            app = mod.ProxyApplication(cfg, mock.Mock(), mock.Mock(), None)
            self.assertTrue(app.authorized(None))

    def test_proxy_key_must_match(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            app = mod.ProxyApplication(cfg, mock.Mock(), mock.Mock(), "STATIC_KEY")
            self.assertFalse(app.authorized(None))
            self.assertFalse(app.authorized("WRONG"))
            self.assertTrue(app.authorized("STATIC_KEY"))


class ConfigTests(unittest.TestCase):
    def test_default_refresh_is_ten_hours(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            env = {
                "STORAGEGRID_BASE_URL": "https://sg.example",
                "STORAGEGRID_USERNAME": "user",
                "STORAGEGRID_ACCOUNT_ID": "account",
                "STORAGEGRID_PASSWORD": "secret",
            }
            with mock.patch.dict("os.environ", env, clear=True):
                cfg = mod.Config.from_env()
            self.assertEqual(cfg.refresh_interval_seconds, 10 * 3600)
            self.assertEqual(cfg.auth_path, "/api/v4/authorize")
            self.assertEqual(cfg.usage_path, "/api/v4/org/usage")

    def test_loopback_detection(self):
        self.assertTrue(mod.is_loopback_bind("127.0.0.1"))
        self.assertTrue(mod.is_loopback_bind("::1"))
        self.assertTrue(mod.is_loopback_bind("localhost"))
        self.assertFalse(mod.is_loopback_bind("0.0.0.0"))
        self.assertFalse(mod.is_loopback_bind("10.0.0.10"))

    def test_loopback_without_proxy_key_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            mod.validate_bind_security(cfg)

    def test_non_loopback_without_proxy_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            cfg.bind_host = "0.0.0.0"
            with self.assertRaisesRegex(mod.ProxyError, "PROXY_API_KEY is required"):
                mod.validate_bind_security(cfg)

    def test_non_loopback_without_proxy_key_allows_explicit_override_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            cfg.bind_host = "0.0.0.0"
            cfg.allow_unauthenticated_nonloopback = True
            with self.assertLogs(mod.LOG, level="WARNING") as logged:
                mod.validate_bind_security(cfg)
            self.assertIn("DANGEROUS OVERRIDE", "\n".join(logged.output))


class ClientTests(unittest.TestCase):
    def test_authorize_sends_expected_body_without_exposing_password_in_return(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mod.StorageGridClient(cfg, ssl.create_default_context())
            auth_response = mod.UpstreamResponse(
                200,
                b'{"data":"TOKEN"}',
                "application/json",
            )
            with mock.patch.object(client, "_request", return_value=auth_response) as request:
                token = client.authorize()
            self.assertEqual(token, "TOKEN")
            kwargs = request.call_args[1]
            self.assertEqual(kwargs["json_body"]["accountId"], "tenant-123")
            self.assertEqual(kwargs["json_body"]["username"], "monitoring-user")
            self.assertEqual(kwargs["json_body"]["password"], "secret-password")
            self.assertTrue(kwargs["json_body"]["cookie"])
            self.assertFalse(kwargs["json_body"]["csrfToken"])

    def test_fetch_usage_preserves_original_json_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mod.StorageGridClient(cfg, ssl.create_default_context())
            raw = b'{"z":1, "a":2}'
            upstream = mod.UpstreamResponse(200, raw, "application/json;charset=UTF-8")
            with mock.patch.object(client, "_request", return_value=upstream):
                result = client.fetch_usage("TOKEN")
            self.assertEqual(result.body, raw)
            self.assertEqual(result.content_type, "application/json;charset=UTF-8")

    def test_invalid_usage_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mod.StorageGridClient(cfg, ssl.create_default_context())
            upstream = mod.UpstreamResponse(200, b'not-json', "text/plain")
            with mock.patch.object(client, "_request", return_value=upstream):
                with self.assertRaises(mod.ProxyError):
                    client.fetch_usage("TOKEN")


class HTTPServerTests(unittest.TestCase):
    def _start_server(self, app):
        server = mod.ProxyHTTPServer(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def test_healthz_and_readyz_do_not_expose_token(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.return_value = "SECRET_TOKEN"
            client.fetch_usage.return_value = usage_response()
            manager = mod.TokenManager(cfg, client)
            manager.refresh(force=True)
            app = mod.ProxyApplication(cfg, client, manager, None)
            server, thread = self._start_server(app)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(base + "/healthz", timeout=2) as response:
                    health = response.read().decode("utf-8")
                with urllib.request.urlopen(base + "/readyz", timeout=2) as response:
                    ready = response.read().decode("utf-8")
                self.assertNotIn("SECRET_TOKEN", health)
                self.assertNotIn("SECRET_TOKEN", ready)
                self.assertEqual(json.loads(health)["status"], "ok")
                self.assertEqual(json.loads(ready)["status"], "ready")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_readyz_reports_stale_refresh_and_metrics_do_not_leak_error_detail(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            cfg.stale_token_warning_seconds = 60
            client = mock.Mock()
            client.authorize.side_effect = ["TOKEN", mod.ProxyError("sensitive outage detail")]
            client.fetch_usage.return_value = usage_response()
            manager = mod.TokenManager(cfg, client)
            manager.refresh(force=True)
            with self.assertRaises(mod.ProxyError):
                manager.refresh(force=True)
            with manager._state_lock:
                manager._last_error_started_epoch = __import__("time").time() - 61

            app = mod.ProxyApplication(cfg, client, manager, None)
            server, thread = self._start_server(app)
            try:
                base = "http://127.0.0.1:{0}".format(server.server_port)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(base + "/readyz", timeout=2)
                self.assertEqual(ctx.exception.code, 503)
                ready = json.loads(ctx.exception.read().decode("utf-8"))
                self.assertEqual(ready["status"], "not_ready")
                self.assertGreaterEqual(ready["refresh_error_age_seconds"], 60)

                with urllib.request.urlopen(base + "/metrics", timeout=2) as response:
                    metrics_text = response.read().decode("utf-8")
                metrics = json.loads(metrics_text)
                self.assertTrue(metrics["token_loaded"])
                self.assertEqual(metrics["consecutive_refresh_failures"], 1)
                self.assertTrue(metrics["last_refresh_error"])
                self.assertIn("seconds_since_last_success", metrics)
                self.assertNotIn("sensitive outage detail", metrics_text)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_usage_endpoint_returns_live_upstream_body_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            raw = b'{"used":123, "files":456}'
            client.authorize.return_value = "TOKEN"
            client.fetch_usage.side_effect = [usage_response({"validation": True}), mod.UpstreamResponse(200, raw, "application/json")]
            manager = mod.TokenManager(cfg, client)
            manager.refresh(force=True)
            app = mod.ProxyApplication(cfg, client, manager, None)
            server, thread = self._start_server(app)
            try:
                url = f"http://127.0.0.1:{server.server_port}/storagegrid/usage"
                with urllib.request.urlopen(url, timeout=2) as response:
                    body = response.read()
                    status = response.status
                self.assertEqual(status, 200)
                self.assertEqual(body, raw)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_usage_endpoint_requires_configured_static_key(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            app = mod.ProxyApplication(cfg, mock.Mock(), mock.Mock(), "STATIC_KEY")
            server, thread = self._start_server(app)
            try:
                url = f"http://127.0.0.1:{server.server_port}/storagegrid/usage"
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(url, timeout=2)
                self.assertEqual(ctx.exception.code, 401)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_usage_endpoint_with_static_key_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = base_config(pathlib.Path(td))
            client = mock.Mock()
            client.authorize.return_value = "TOKEN"
            client.fetch_usage.side_effect = [usage_response({"validation": True}), usage_response({"used": 10})]
            manager = mod.TokenManager(cfg, client)
            manager.refresh(force=True)
            app = mod.ProxyApplication(cfg, client, manager, "STATIC_KEY")
            server, thread = self._start_server(app)
            try:
                url = f"http://127.0.0.1:{server.server_port}/storagegrid/usage"
                request = urllib.request.Request(url, headers={"X-StorageGRID-Proxy-Key": "STATIC_KEY"})
                with urllib.request.urlopen(request, timeout=2) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(body, {"used": 10})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
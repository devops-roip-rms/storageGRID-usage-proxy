# StorageGRID Usage Proxy - Final Build Report

## Inputs incorporated

This build incorporates the confirmed environment details:

- Splunk gateway Python: **3.6.8**.
- StorageGRID authorize endpoint: `/api/v4/authorize`.
- Usage endpoint: `/api/v4/org/usage`.
- Known-working authorize request includes:
  - `Accept: application/json`
  - `Authorization: Bearer 00000000-0000-0000-0000-000000000000`
  - `Content-Type: application/json`
  - `X-Csrf-Token: 00000000000000000000000000000000`
  - JSON `accountId`, `username`, `password`, `cookie: true`, `csrfToken: false`.
- Known authorize response returns bearer token as string in `data`.
- StorageGRID URL is HTTPS; supplied working curl does not use `-k`.
- Deployment is in a dedicated folder on a closed-network server.
- No application files may depend on `/etc`.
- Proxy should start automatically after server reboot.

## Major corrections from the previous package

1. Runtime backported from Python 3.10+ syntax/library features to Python 3.6-compatible code.
   - Removed `dataclasses`.
   - Removed PEP 604 `X | None` types.
   - Removed built-in generic annotations such as `dict[str, ...]`.
   - Replaced `ThreadingHTTPServer` with `ThreadingMixIn + HTTPServer`.
2. Authorize request now matches the supplied working v4 curl.
3. Packaged `<...>` placeholders are rejected by `check-config`.
4. Added `SETUP_GUIDE.md`.
5. Added `scripts/setup.sh` so lost archive executable bits are repairable with `sh scripts/setup.sh`.
6. Added user-crontab `@reboot` installer/remover without application files under `/etc`.
7. Kept TLS verification enabled by default.
8. Kept all runtime state/logs inside the dedicated project folder.
9. Added retry-backoff protection when HTTP 401 recovery authorization itself fails, preventing repeated sniffer polls from hammering `/authorize`.

## Verification performed

- Python compilation: PASS under the available Python runtime.
- Python 3.6 grammar parse using a 3.6 grammar: PASS.
- Manual scan for runtime features known to require Python >3.6: PASS.
- POSIX shell syntax for all scripts: PASS.
- Executable bit set on all `.sh` scripts in the packaged project: PASS.
- Untouched placeholder `proxy.env` rejected by `check-config`: PASS.
- Automated tests: **35/35 PASS**.
- Exact authorize headers/body asserted in unit and end-to-end tests: PASS.
- Exact v4-like authorize response shape with token in `data`: PASS.
- Candidate token validated through `/api/v4/org/usage` before activation: PASS.
- Automatic background interval refresh test: PASS.
- Automatic retry after scheduled refresh failure: PASS.
- HTTP 401 immediate reauthorization/retry: PASS.
- Token/password not printed by upstream test: PASS.
- Autostart installer/remover mock test (including duplicate prevention): PASS.
- Operator-style clean-copy test after changing only the four required env values:
  - `setup.sh`: PASS
  - `check-config.sh`: PASS
  - `run-tests.sh`: PASS
  - `test-upstream.sh`: PASS
  - `start.sh`: PASS
  - `/readyz`: PASS
  - `/storagegrid/usage`: PASS
  - `status.sh`: PASS
  - `stop.sh`: PASS

## Remaining environment-dependent verification

Only real-environment checks remain:

1. Actual StorageGRID credentials/account ID.
2. Actual DNS/IP connectivity from the Splunk gateway to StorageGRID.
3. Actual TLS trust on the gateway (`test-upstream.sh` will prove this).
4. HTTP-SNIFFER container/process connectivity to `<gateway-IP>:8787`.
5. Availability of the `crontab` command for the included no-`/etc` reboot autostart method.

No Codex work is required for these checks.

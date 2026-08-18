# Final Build Report

## Consolidation

This package combines the useful parts of the two previous variants:

- portable dedicated-folder configuration and start/stop scripts;
- the complete proxy/token-manager implementation;
- unit, HTTP, failure-path, and end-to-end tests;
- closed-network/no-third-party-dependency behavior.

The previous separate password-file requirement was removed so the only project file that must be edited before testing is `config/proxy.env`.

## Final behavior

1. Load `config/proxy.env` from the project directory.
2. Authorize to StorageGRID with `POST /api/v4/authorize`.
3. Validate every candidate token with `GET /api/v4/org/usage`.
4. Keep the validated bearer token in RAM only.
5. Refresh every 10 hours.
6. Keep the working token if a scheduled refresh fails and retry after the configured retry interval.
7. Serve `GET /storagegrid/usage` to HTTP-SNIFFER.
8. Fetch live StorageGRID usage for every proxy usage request.
9. On upstream HTTP 401, reauthorize safely and retry once.
10. Never edit HTTP-SNIFFER config at runtime and never restart its Compose project.

## Additional hardening in this final merge

- `proxy.env` is authoritative for keys it defines, avoiding accidental stale shell-environment overrides.
- Placeholder values such as `CHANGE_ME_*` cause config validation to fail instead of starting incorrectly.
- StorageGRID base URL validation prevents accidentally putting `/api/v4/...` into the base URL.
- Passwords containing characters such as `=` and `#` are supported by the env-file parser.
- Real CLI `--test-upstream` path is covered by an end-to-end test.
- Routine HTTP access logs are DEBUG level to avoid unnecessary long-term log growth.
- start/status/stop scripts verify the PID belongs to the proxy before treating or killing it as the managed process on Linux `/proc` systems.

## Validation performed

- Python compilation: PASS
- Shell syntax: PASS
- Unit/integration/end-to-end suite: 30/30 PASS
- Real subprocess CLI env-file + mocked StorageGRID authorize/usage test: PASS
- HTTP 401 reauthorization/retry path: PASS
- Candidate-token validation-before-install path: PASS
- Secret/token log-output checks in tests: PASS
- Legacy `/etc` / password-file / proxy-key-file dependency scan: PASS
- Docker Compose runtime dependency: NONE
- Third-party Python dependency: NONE

## Operator-style dedicated-folder test

A clean copy of the final folder was tested exactly as intended for deployment:

1. Edited only `config/proxy.env`.
2. Ran `scripts/check-config.sh` -> PASS.
3. Ran `scripts/test-upstream.sh` against a real local mock HTTP StorageGRID service -> PASS, HTTP 200.
4. Ran `scripts/start.sh` -> PASS.
5. Ran `scripts/status.sh` -> running with managed PID.
6. Called `/readyz` -> HTTP 200 / ready.
7. Called `/storagegrid/usage` -> HTTP 200 with the live mock usage JSON.
8. Ran `scripts/stop.sh` -> clean shutdown.

No project file other than `config/proxy.env` was modified for that run.

# Codex review prompt - StorageGRID Usage Proxy

Review the complete `storagegrid-usage-proxy` folder as a final closed-network deployment package. Do not redesign the architecture unless you find a concrete defect.

The intended behavior is fixed:

- dedicated project folder only; no `/etc` application paths;
- runtime values are edited only in `config/proxy.env`;
- no pip/internet dependency;
- `POST /api/v4/authorize` obtains a candidate StorageGRID tenant bearer token;
- the candidate must pass `GET /api/v4/org/usage` before becoming active;
- active bearer token stays in RAM only;
- normal refresh interval is 10 hours;
- refresh failure preserves the existing token and retries after the configured retry interval;
- HTTP-SNIFFER calls `GET /storagegrid/usage` on this proxy;
- each usage request fetches live `/api/v4/org/usage` data using the current token;
- upstream HTTP 401 causes one safe reauthorization and one retry;
- non-401 errors must not create authentication loops;
- no runtime HTTP-SNIFFER config edits;
- no Docker Compose restart commands;
- password or bearer token must never be logged or exposed by health endpoints.

Before reporting completion:

1. Run `./scripts/run-tests.sh`.
2. Run `python3 -m py_compile storagegrid_usage_proxy.py`.
3. Run `sh -n scripts/*.sh` (or validate each script if the shell does not expand that form).
4. Confirm only `config/proxy.env` requires project-specific value changes.
5. Confirm there are no hard-coded system application paths.
6. Confirm `./scripts/test-upstream.sh` uses the configured env file and does not print the token/password.
7. Do not replace working code with extra frameworks or third-party packages without a concrete requirement.

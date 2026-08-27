# StorageGRID Usage Proxy

A self-contained StorageGRID Tenant API usage proxy for a closed-network Splunk gateway.

HTTP-SNIFFER calls one stable local endpoint:

```text
GET http://<splunk-gateway-IP>:8787/storagegrid/usage
```

The proxy owns the StorageGRID bearer token in RAM, obtains and validates a token immediately at startup, refreshes it automatically every 10 hours, retries failed refreshes after 5 minutes, and reauthorizes once automatically if StorageGRID rejects the active token with HTTP 401.

It does **not** edit HTTP-SNIFFER `conf.json`, restart HTTP-SNIFFER, or persist the bearer token.

## How it works

```text
HTTP-SNIFFER
     |
     | GET /storagegrid/usage
     v
+-----------------------------+
|   StorageGRID Usage Proxy   |
|                             |
|  1. Keeps bearer token RAM  |
|  2. Refreshes every 10h     |
|  3. Validates new token     |
|  4. Retries on HTTP 401     |
+-----------------------------+
     |
     | GET /api/v4/org/usage
     | Authorization: Bearer <token>
     v
+-----------------------------+
|     StorageGRID Tenant      |
|                             |
| POST /api/v4/authorize      |
| GET  /api/v4/org/usage      |
+-----------------------------+
     |
     | Usage JSON
     v
StorageGRID Usage Proxy
     |
     | Same Usage JSON
     v
HTTP-SNIFFER
     |
     | Monitoring Event
     v
   SPLUNK
```

Token lifecycle:

```text
Startup -> Authorize -> Validate -> Use token -> Refresh every 10h
                                      |
                                      +-> HTTP 401 -> Reauthorize and retry
```

## Authentication request

The v4 authorize request uses the configured StorageGRID account ID, username, and password and sends:

```json
{
  "accountId": "...",
  "username": "...",
  "password": "...",
  "cookie": true,
  "csrfToken": false
}
```

The bearer token returned in `response.data` is validated with `/api/v4/org/usage` before it becomes the active in-memory token.

## Deployment options

The same application source supports two deployment methods.

### Option A - Native Python

Retained as a fallback and troubleshooting method. The application supports Python **3.6+** and was specifically backported for the gateway's Python **3.6.8**.

```bash
sh scripts/setup.sh
./scripts/start.sh
./scripts/status.sh
./scripts/stop.sh
```

### Option B - Docker - recommended for production

Docker owns the Python runtime and process lifecycle. The proxy remains a separate container/Compose project from HTTP-SNIFFER.

```bash
docker load -i storagegrid-usage-proxy_<version>.tar
docker compose -f compose.yml up -d
docker compose -f compose.yml ps
docker compose -f compose.yml logs -f storagegrid-usage-proxy
```

The image contains **no StorageGRID credentials and no `PROXY_API_KEY`**. `config/proxy.env` and optional CA certificates are mounted read-only at runtime.

The closed-network Splunk gateway does not need internet access. Build/export the image on a connected CI/build system, transfer the `.tar` and checksum through the approved process, then use `docker load` on the gateway.

## Configuration

The runtime configuration file is:

```text
config/proxy.env
```

Keep placeholder values in Git. Enter real credentials only in the deployment copy and never commit those values.

Production deployments that expose the proxy on a non-loopback listener use
`PROXY_BIND_HOST=0.0.0.0` and keep `ALLOW_UNAUTHENTICATED_NONLOOPBACK=false`.
`PROXY_API_KEY` is therefore required. Configure HTTP-SNIFFER to send that value in the
`X-StorageGRID-Proxy-Key` header. The only way to start an unauthenticated non-loopback
listener is the deliberately conspicuous `ALLOW_UNAUTHENTICATED_NONLOOPBACK=true` override;
the proxy logs this as a dangerous configuration and it is not a normal production setting.

`/readyz` returns 503 if the token is missing or refresh failures persist for
`STALE_TOKEN_WARNING_SECONDS` (default: 900 seconds, or three retry intervals when that is
longer). Its JSON response reports the non-sensitive failure age and count. `/metrics` exposes
JSON health counters: token presence, seconds since the last successful refresh, consecutive
refresh failures, and a boolean indicating a refresh error. Neither endpoint exposes a token,
password, or error details.

The Docker image health check uses `/readyz`. A persistent 503 can mark the container
`unhealthy`, but `restart: unless-stopped` does not restart a container only because it is
unhealthy; it restarts after process/container exit and host reboot. This avoids turning an
upstream StorageGRID outage into an automatic restart loop.

These five values are environment-specific production values:

```ini
STORAGEGRID_BASE_URL=https://<real-host-or-ip>
STORAGEGRID_USERNAME=<real-username>
STORAGEGRID_ACCOUNT_ID=<real-account-id>
STORAGEGRID_PASSWORD=<real-password>
PROXY_API_KEY=<strong-local-shared-secret>
```

`config/proxy.env` is excluded from the Docker build context by `.dockerignore`, so credentials and the proxy shared key are not baked into image layers.

## Versioning

`TAG` is the **single source of truth for the Docker release version** in both GitHub Actions and GitLab CI.

Example:

```text
v1.1.0
```

Both CI systems read the file and build:

```text
storagegrid-usage-proxy:v1.1.0
storagegrid-usage-proxy:latest
storagegrid-usage-proxy_v1.1.0.tar
storagegrid-usage-proxy_v1.1.0.tar.sha256
IMAGE_VERSION.txt containing v1.1.0
```

Use semantic release values such as:

```text
v1.0.0  first production release
v1.0.1  bug fix
v1.1.0  backward-compatible feature
v2.0.0  breaking/major change
```

Do not let CI auto-increment `TAG`. Change it deliberately when a new deployable image should be produced.

## CI policy

Normal source commits and merge/pull requests run the compatibility tests.

```text
source change
    |
    +--> Python 3.6 compatibility tests
    |
    +--> Python 3.11 tests
```

A Docker offline artifact is produced only when a release is requested:

```text
TAG changed on default branch
           OR
manual pipeline/workflow run
          |
          v
     tests must pass
          |
          v
read version from TAG
          |
          v
build versioned + latest image
          |
          v
docker save
          |
          v
versioned .tar + SHA256
```

This keeps ordinary commits from rebuilding a deployable image unnecessarily.

## GitHub Actions - current CI

The active GitHub workflow is:

```text
.github/workflows/build-offline-image.yml
```

Current behavior:

- Python 3.6.15 compatibility suite runs in the official Python 3.6 Docker image.
- The Python 3.6 image pull is retried to reduce failures from transient registry errors.
- Python 3.11 uses `actions/setup-python`, so that test does not depend on pulling a Python 3.11 Docker image.
- Both test suites must pass before packaging.
- The Docker base image is explicitly pulled with retry before `docker build`.
- The image version comes from `TAG`.
- The offline artifact includes the versioned image, `latest`, SHA256 checksum, `IMAGE_VERSION.txt`, and `compose.yml`.

Expected GitHub Actions compatibility result after the local/container validation:

```text
Python 3.6.15: 44/44 PASS
Python 3.11:   44/44 PASS
```

The updated 44-test suite still needs a real GitHub Actions run before it should be
recorded as GitHub-proven.

## GitLab CI - future migration

`.gitlab-ci.yml` is kept in the repository so the same source can later move/copy to GitLab without changing the application.

The GitLab pipeline follows the same release model:

- tests on Python 3.6.15 and Python 3.11;
- version read from `TAG`;
- Docker packaging only when `TAG` changes on the default branch or when the pipeline is started manually;
- versioned and `latest` tags exported to an offline `.tar`;
- SHA256 checksum generated.

The packaging job uses Docker-in-Docker, so its GitLab Runner must support privileged Docker-in-Docker.

For GitLab image-pull reliability, prefer the GitLab Dependency Proxy or a runner/registry mirror when the GitLab environment is created. Job container images are pulled by the runner **before** the job script starts, so a shell retry inside the job cannot protect those initial pulls.

## Tests

Local test suite:

```bash
./scripts/run-tests.sh
```

Expected:

```text
Ran 44 tests
OK
```

Real StorageGRID connectivity test:

```bash
./scripts/test-upstream.sh
```

This performs one authorize request, validates the returned token with `/api/v4/org/usage`, and does not print the password or bearer token.

## Project layout

```text
storageGRID-usage-proxy/
|-- TAG                           # Docker release version; single CI source of truth
|-- storagegrid_usage_proxy.py
|-- Dockerfile
|-- compose.yml
|-- .dockerignore
|-- .gitlab-ci.yml
|-- .github/
|   `-- workflows/
|       `-- build-offline-image.yml
|-- config/
|   `-- proxy.env                 # runtime config; keep real credentials out of Git
|-- certs/
|   `-- .keep                     # optional internal CA PEM directory
|-- scripts/
|   |-- setup.sh
|   |-- check-config.sh
|   |-- run-tests.sh
|   |-- test-upstream.sh
|   |-- run.sh
|   |-- start.sh
|   |-- status.sh
|   |-- stop.sh
|   |-- install-autostart.sh
|   |-- remove-autostart.sh
|   `-- logrotate/
|       `-- storagegrid-usage-proxy
|-- tests/
|   |-- test_storagegrid_usage_proxy.py
|   `-- test_end_to_end.py
|-- docs/
|   |-- SETUP_GUIDE.md
|   `-- BUILD_REPORT.md
|-- logs/
|   `-- .keep
|-- runtime/
|   `-- .keep
`-- README.md
```

For full native, Docker, offline-transfer, versioning, GitHub Actions, and GitLab deployment instructions, see **[SETUP_GUIDE.md](docs/SETUP_GUIDE.md)**. The current validation summary is in **[BUILD_REPORT.md](docs/BUILD_REPORT.md)**.
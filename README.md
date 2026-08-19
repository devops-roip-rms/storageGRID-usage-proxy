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

The image contains **no StorageGRID credentials**. `config/proxy.env` and optional CA certificates are mounted read-only at runtime.

The closed-network Splunk gateway does not need internet access. Build/export the image on a connected CI/build system, transfer the `.tar` and checksum through the approved process, then use `docker load` on the gateway.

## Configuration

The runtime configuration file is:

```text
config/proxy.env
```

Keep placeholder values in Git. Enter real credentials only in the deployment copy and never commit those values.

Only these four values are environment-specific:

```ini
STORAGEGRID_BASE_URL=https://<real-host-or-ip>
STORAGEGRID_USERNAME=<real-username>
STORAGEGRID_ACCOUNT_ID=<real-account-id>
STORAGEGRID_PASSWORD=<real-password>
```

`config/proxy.env` is excluded from the Docker build context by `.dockerignore`, so credentials are not baked into image layers.

## Versioning

`TAG` is the **single source of truth for the Docker release version** in both GitHub Actions and GitLab CI.

Example:

```text
v1.0.0
```

Both CI systems read the file and build:

```text
storagegrid-usage-proxy:v1.0.0
storagegrid-usage-proxy:latest
storagegrid-usage-proxy_v1.0.0.tar
storagegrid-usage-proxy_v1.0.0.tar.sha256
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

Observed GitHub Actions compatibility result for the current application:

```text
Python 3.6.15: 35/35 PASS
Python 3.11:   35/35 PASS
```

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
Ran 35 tests
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
├── TAG                           # Docker release version; single CI source of truth
├── storagegrid_usage_proxy.py
├── Dockerfile
├── compose.yml
├── .dockerignore
├── .gitlab-ci.yml
├── .github/
│   └── workflows/
│       └── build-offline-image.yml
├── config/
│   └── proxy.env                 # runtime config; keep real credentials out of Git
├── certs/                        # optional internal CA PEM
├── scripts/
│   ├── setup.sh
│   ├── check-config.sh
│   ├── run-tests.sh
│   ├── test-upstream.sh
│   ├── run.sh
│   ├── start.sh
│   ├── status.sh
│   ├── stop.sh
│   ├── install-autostart.sh
│   └── remove-autostart.sh
├── tests/
│   ├── test_storagegrid_usage_proxy.py
│   └── test_end_to_end.py
├── logs/
├── runtime/
├── SETUP_GUIDE.md
├── BUILD_REPORT.md
└── README.md
```

For full native, Docker, offline-transfer, versioning, GitHub Actions, and GitLab deployment instructions, see **[SETUP_GUIDE.md](SETUP_GUIDE.md)**.

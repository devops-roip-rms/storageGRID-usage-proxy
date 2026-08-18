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

The same application source supports two deployment methods:

### Option A - Native Python

Kept as a fallback and for direct server testing. It supports Python **3.6+** and was specifically backported for the gateway's Python **3.6.8**.

Native lifecycle commands:

```bash
sh scripts/setup.sh
./scripts/start.sh
./scripts/status.sh
./scripts/stop.sh
```

### Option B - Docker - recommended for the production server

Docker owns the Python runtime and process lifecycle. The proxy remains a separate container from HTTP-SNIFFER.

```bash
docker load -i storagegrid-usage-proxy_<version>.tar
docker compose -f compose.yml up -d
docker compose -f compose.yml ps
docker compose -f compose.yml logs -f storagegrid-usage-proxy
```

The image contains **no StorageGRID credentials**. `config/proxy.env` is mounted read-only at runtime.

For the closed network, build/export the image on a connected build system, transfer the `.tar`, then use `docker load` on the Splunk gateway.

## Configuration

The runtime configuration file is:

```text
config/proxy.env
```

Keep the repository copy as placeholders. Enter real credentials only in the deployment copy and do not commit those values to Git.

Only these four values must be environment-specific:

```ini
STORAGEGRID_BASE_URL=https://<real-host-or-ip>
STORAGEGRID_USERNAME=<real-username>
STORAGEGRID_ACCOUNT_ID=<real-account-id>
STORAGEGRID_PASSWORD=<real-password>
```

`config/proxy.env` is intentionally excluded from the Docker build context by `.dockerignore`, so credentials are not baked into image layers.

## Tests

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

## GitLab CI / offline image artifact

`.gitlab-ci.yml` is included for the future GitLab repository.

The pipeline:

```text
Push / Merge Request
        |
        +--> Test on Python 3.6
        |
        +--> Test on Python 3.11
        |
Default branch or Git tag
        |
        v
Build Docker image
        |
        v
Tag <version-or-commit> + latest
        |
        v
docker save
        |
        v
storagegrid-usage-proxy_<version>.tar
+ SHA256 checksum
```

The Docker packaging job uses Docker-in-Docker, so the GitLab Runner used for that job must allow privileged Docker-in-Docker. Test jobs do not require Docker-in-Docker.

## Project layout

```text
storageGRID-usage-proxy/
├── storagegrid_usage_proxy.py
├── Dockerfile
├── compose.yml
├── .dockerignore
├── .gitlab-ci.yml
├── config/
│   └── proxy.env                 # runtime configuration; keep real credentials out of Git
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

For full native and Docker deployment instructions, see **[SETUP_GUIDE.md](SETUP_GUIDE.md)**.

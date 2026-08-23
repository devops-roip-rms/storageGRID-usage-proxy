# StorageGRID Usage Proxy - Setup, Test, Release, and Deployment Guide

This guide describes the current project version.

Supported deployment methods:

- **Option A - Native Python:** fallback/troubleshooting deployment.
- **Option B - Docker:** recommended production deployment on the Splunk gateway.

Supported CI systems:

- **GitHub Actions:** current active CI.
- **GitLab CI:** retained for the planned migration/copy to GitLab.

The proxy application logic is identical in all deployment paths.

---

## 1. Requirements

### Common

- Network access from the Splunk gateway to the StorageGRID Tenant Management API over HTTPS.
- Network access from HTTP-SNIFFER to TCP `8787` on the Splunk gateway.
- The five real production values in `config/proxy.env`, including the local proxy shared key.

### Native deployment

- Linux server / Splunk gateway.
- Python 3.6 or newer.
- The application was specifically backported and tested for the gateway's Python 3.6.x environment.
- No pip packages, Git, internet access, `/etc` application files, or system-wide application installation are required.

### Docker deployment

- Docker Engine.
- Docker Compose plugin.
- The target closed-network server does **not** require internet access.
- Build the image on a connected CI/build machine, export it with `docker save`, and transfer it as a `.tar`.

---

## 2. Runtime configuration

The runtime configuration file is:

```text
config/proxy.env
```

Keep placeholder values in the repository. Enter real credentials only in the deployment copy and do not commit them.

Protect the deployment copy:

```bash
chmod 600 config/proxy.env
```

Edit these five required production values:

```ini
STORAGEGRID_BASE_URL=https://<real-storagegrid-host-or-ip>
STORAGEGRID_USERNAME=<real-tenant-username>
STORAGEGRID_ACCOUNT_ID=<real-tenant-account-id>
STORAGEGRID_PASSWORD=<real-tenant-password>
PROXY_API_KEY=<strong-local-shared-secret>
```

The normal defaults are:

```ini
AUTH_PATH=/api/v4/authorize
USAGE_PATH=/api/v4/org/usage
TOKEN_REFRESH_HOURS=10
REFRESH_RETRY_SECONDS=300
STALE_TOKEN_WARNING_SECONDS=900
HTTP_TIMEOUT_SECONDS=30
MAX_RESPONSE_BYTES=10485760
TLS_VERIFY=true
CA_BUNDLE=
PROXY_BIND_HOST=0.0.0.0
PROXY_PORT=8787
ALLOW_UNAUTHENTICATED_NONLOOPBACK=false
LOG_LEVEL=INFO
```

`PROXY_API_KEY` is required when `PROXY_BIND_HOST` is non-loopback, including `0.0.0.0`.
Set the same value in HTTP-SNIFFER's `X-StorageGRID-Proxy-Key` header. A non-loopback
listener without a key fails during `--check-config` and at startup unless the operator
deliberately sets `ALLOW_UNAUTHENTICATED_NONLOOPBACK=true`; that override is logged as
dangerous and should only be used for an explicitly accepted insecure deployment.

`/readyz` returns 503 when no token is loaded or refresh failures have persisted for
`STALE_TOKEN_WARNING_SECONDS`. The default is 900 seconds (or three retry intervals if longer).
The endpoint's JSON status includes only non-sensitive refresh failure age/count fields. The
unauthenticated `GET /metrics` JSON endpoint exposes token presence, seconds since last success,
consecutive failure count, and a boolean error state; it never includes token, password, or error
text.

The authorize body contains `cookie: true` and `csrfToken: false` directly in application code. There is no bootstrap bearer or bootstrap CSRF configuration.

---

## 3. Version control with `TAG`

`TAG` is the release-version source for **both GitHub Actions and GitLab CI**.

The file must contain one valid Docker tag, for example:

```text
v1.1.0
```

Recommended convention:

```text
v1.0.0  first production release
v1.0.1  bug fix
v1.1.0  backward-compatible feature
v2.0.0  major/breaking release
```

### Release rule

Normal code commits do **not** require a version change.

When the code is ready to become a new deployable Docker image:

1. Change `TAG`.
2. Commit the `TAG` change with the release-ready source.
3. Push to the default branch.
4. CI runs both test suites.
5. Only after the tests pass does CI build/export the offline Docker image.

Example:

```text
TAG before: v1.0.0
TAG after:  v1.0.1
```

CI must not auto-increment this file.

---

## 4. Validate the source

From the project folder:

```bash
sh scripts/setup.sh
./scripts/check-config.sh
./scripts/run-tests.sh
```

Expected:

```text
configuration_ok=yes
Ran 44 tests
OK
```

Then test the real StorageGRID endpoint:

```bash
./scripts/test-upstream.sh
```

Expected:

```text
upstream_test=ok
usage_status=200
usage_bytes=<number>
```

The command does not print the password or bearer token.

### TLS

Keep:

```ini
TLS_VERIFY=true
CA_BUNDLE=
```

If Python reports certificate verification failure, obtain the approved StorageGRID/internal CA certificate, place it under `certs/`, and set for example:

```ini
CA_BUNDLE=certs/storagegrid-ca.pem
```

Do not use `TLS_VERIFY=false` as the normal production configuration.

---

# Option A - Native Python

## 5A. Start and verify

```bash
./scripts/start.sh
./scripts/status.sh
```

Expected:

```text
status=running
pid=<number>
```

Health/live tests:

```bash
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/readyz
curl http://127.0.0.1:8787/storagegrid/usage
```

Normal operations:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/stop.sh
```

Native log:

```text
logs/storagegrid-usage-proxy.log
```

### Native log rotation

`scripts/start.sh` appends to the native log and intentionally leaves application logging
unchanged. Install the included `copytruncate` logrotate template so the running proxy can keep
its file descriptor open while old logs are rotated.

For a system-wide installation (replace the project path only through this command):

```bash
sudo sed "s|__PROJECT_DIR__|$(pwd)|g" scripts/logrotate/storagegrid-usage-proxy \
  | sudo tee /etc/logrotate.d/storagegrid-usage-proxy >/dev/null
sudo logrotate -d /etc/logrotate.d/storagegrid-usage-proxy
```

Run those commands from the project directory. The installed policy rotates the log daily, keeps
14 archives, and compresses old archives after one rotation.

Without root access, install a per-user policy and invoke logrotate from the current user's cron:

```bash
mkdir -p "$HOME/.config/logrotate" "$HOME/.local/state"
sed "s|__PROJECT_DIR__|$(pwd)|g" scripts/logrotate/storagegrid-usage-proxy \
  > "$HOME/.config/logrotate/storagegrid-usage-proxy"
crontab -e
```

Add this daily crontab entry, adjusting the `logrotate` path if needed:

```cron
0 0 * * * /usr/sbin/logrotate -s "$HOME/.local/state/storagegrid-usage-proxy.logrotate.status" "$HOME/.config/logrotate/storagegrid-usage-proxy"
```

## 6A. Native reboot autostart

After native deployment works:

```bash
./scripts/install-autostart.sh
```

This uses the current user's `crontab @reboot` and does not install application files under `/etc`.

Remove:

```bash
./scripts/remove-autostart.sh
```

Do **not** install this crontab autostart when Docker is the production deployment method. Docker uses `restart: unless-stopped`.

---

# Option B - Docker - Recommended

## 5B. Docker files

The project contains:

```text
Dockerfile
compose.yml
.dockerignore
```

The Docker image contains application code and its Python runtime. It does **not** contain `config/proxy.env`, real credentials, `PROXY_API_KEY`, runtime logs, or private CA certificates.

Compose mounts:

```text
./config/proxy.env -> /app/config/proxy.env:ro
./certs            -> /app/certs:ro
```

The current runtime base image is:

```text
python:3.11-slim-bookworm
```

## 6B. Manual connected build

Normally use CI. For a manual build, read the version from `TAG`:

```bash
VERSION="$(tr -d '[:space:]' < TAG)"
printf 'Building version %s\n' "$VERSION"

docker pull python:3.11-slim-bookworm

docker build \
  --build-arg APP_VERSION="$VERSION" \
  -t "storagegrid-usage-proxy:$VERSION" \
  -t storagegrid-usage-proxy:latest \
  .
```

Run the source tests before export:

```bash
./scripts/run-tests.sh
```

Optional local container test:

```bash
docker compose -f compose.yml up -d
docker compose -f compose.yml ps
curl http://127.0.0.1:8787/readyz
docker compose -f compose.yml down
```

This requires a valid `config/proxy.env` and StorageGRID connectivity from the test machine.

## 7B. Export for the closed network

```bash
VERSION="$(tr -d '[:space:]' < TAG)"
TAR_FILE="storagegrid-usage-proxy_${VERSION}.tar"

docker save \
  -o "$TAR_FILE" \
  "storagegrid-usage-proxy:$VERSION" \
  storagegrid-usage-proxy:latest

sha256sum "$TAR_FILE" > "${TAR_FILE}.sha256"
printf '%s\n' "$VERSION" > IMAGE_VERSION.txt
```

Transfer through the approved process:

```text
storagegrid-usage-proxy_<version>.tar
storagegrid-usage-proxy_<version>.tar.sha256
IMAGE_VERSION.txt
compose.yml
```

## 8B. Load on the Splunk gateway

Verify:

```bash
sha256sum -c storagegrid-usage-proxy_<version>.tar.sha256
```

Load:

```bash
docker load -i storagegrid-usage-proxy_<version>.tar
```

Confirm:

```bash
docker image ls storagegrid-usage-proxy
```

The archive contains both the versioned tag and `latest`. `compose.yml` therefore remains unchanged across releases.

## 9B. Start Docker deployment

```bash
docker compose -f compose.yml up -d
```

Check:

```bash
docker compose -f compose.yml ps
docker inspect --format='{{.State.Health.Status}}' storagegrid-usage-proxy
```

Logs:

```bash
docker compose -f compose.yml logs -f storagegrid-usage-proxy
```

Endpoints:

```bash
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/readyz
curl http://127.0.0.1:8787/storagegrid/usage
```

Stop/remove:

```bash
docker compose -f compose.yml down
```

`restart: unless-stopped` handles process/container exits and host reboot. The Dockerfile
health check uses `/readyz`, so a sustained 503 can mark the container `unhealthy`, but Docker
does not restart a container merely because its health status is unhealthy. No native crontab
autostart or outage-driven restart watchdog is required.

## 10B. Upgrade Docker deployment

Release flow:

```text
source changes
     |
     v
tests continue on normal commits
     |
     v
ready to release
     |
     v
update TAG
     |
     v
CI tests
     |
     v
build/export new image
     |
     v
transfer TAR
     |
     v
docker load
     |
     v
recreate proxy container
```

On the closed gateway:

```bash
docker load -i storagegrid-usage-proxy_<new-version>.tar
docker compose -f compose.yml up -d --force-recreate
```

This Compose project contains only the proxy, so this does not restart HTTP-SNIFFER.

---

# GitHub Actions - Current CI

## 11. Workflow

Current workflow:

```text
.github/workflows/build-offline-image.yml
```

### Test behavior

Every push and pull request runs:

```text
Python 3.6.15 compatibility suite
Python 3.11 suite
```

Expected result after local/container validation:

```text
Python 3.6.15: 44/44 PASS
Python 3.11:   44/44 PASS
```

The updated 44-test suite still needs a real GitHub Actions run before it should be
recorded as GitHub-proven.

Python 3.6 runs in `python:3.6.15-slim-buster`. The workflow explicitly retries the Docker pull.

Python 3.11 uses `actions/setup-python`, avoiding a Docker Hub pull for that test.

### Packaging behavior

The offline image job runs only when:

```text
TAG changes on the default branch
OR
the workflow is started manually
```

Both test jobs must pass first.

The job:

1. Reads `VERSION` from `TAG`.
2. Validates the value.
3. Pulls `python:3.11-slim-bookworm` with retry.
4. Builds `storagegrid-usage-proxy:$VERSION`.
5. Also tags it `storagegrid-usage-proxy:latest`.
6. Exports both tags to one TAR.
7. Creates SHA256 and `IMAGE_VERSION.txt`.
8. Uploads the files as a GitHub Actions artifact.

A transient registry `502 Bad Gateway` during image pull is an infrastructure failure, not an application test failure. The current GitHub workflow reduces this risk with explicit pull retry; the Python 3.11 test itself no longer pulls its runtime from Docker Hub.

---

# GitLab CI - Future Migration

## 12. Pipeline

The repository also includes:

```text
.gitlab-ci.yml
```

It uses the same `TAG` file.

Normal pushes / merge requests:

```text
Python 3.6.15 tests
Python 3.11 tests
```

Offline Docker packaging:

```text
TAG changes on default branch
OR
pipeline started manually
```

The packaging job must depend on both successful test jobs.

It:

1. Reads/validates `TAG`.
2. Pulls the Python 3.11 Docker base with retry.
3. Builds the versioned and `latest` image tags.
4. Saves them to a versioned `.tar`.
5. Generates SHA256 and `IMAGE_VERSION.txt`.
6. Publishes the offline files as GitLab artifacts.

## 13. GitLab Runner and image-pull reliability

The Docker packaging job uses Docker-in-Docker and requires a runner that permits privileged Docker-in-Docker.

There are two different pull stages to understand:

### Pulls performed inside the packaging script

These can be retried in `.gitlab-ci.yml`, so the Python 3.11 Docker base should be explicitly pulled with retry before `docker build`.

### Job/service images pulled by GitLab Runner

Examples:

```text
python:3.6.15-slim-buster
python:3.11-slim-bookworm
docker:27.5.1-cli
docker:27.5.1-dind
```

The runner pulls these **before the job script starts**. A shell retry in the job cannot protect that step.

When the GitLab environment is prepared, prefer one of:

1. **GitLab Dependency Proxy** for public Docker Hub images.
2. An approved internal registry/mirror.
3. Runner pull policy configured to retry/fall back to an already cached image.

Do not change application code for any of these options; they are CI/runner infrastructure decisions.

---

# HTTP-SNIFFER Configuration

## 14. Configure HTTP-SNIFFER once

The StorageGRID usage entry should call the proxy instead of StorageGRID directly:

```json
{
  "name": "StorageGRID-usage",
  "src_url": "http://<splunk-gateway-IP>:8787/storagegrid/usage",
  "dst_url": "http://<existing-splunk-gateway-destination>",
  "src_header_name": "X-StorageGRID-Proxy-Key",
  "src_header_value": "<SAME VALUE AS PROXY_API_KEY>",
  "dst_header_name": "",
  "dst_header_value": ""
}
```

Leave the real existing `dst_url` unchanged.

The proxy never edits HTTP-SNIFFER configuration at runtime.

## 15. Network verification

Because HTTP-SNIFFER is a separate container/Compose project, verify from its own network context that it can reach:

```text
http://<splunk-gateway-IP>:8787/storagegrid/usage
```

`PROXY_BIND_HOST=0.0.0.0` is the packaged default for this deployment model.

---

# Token Behavior

## 16. Automatic 10-hour logic

On process/container start:

```text
immediate authorize
    -> validate candidate with /api/v4/org/usage
    -> install token in RAM only
```

Then:

```text
wait 10 hours
    -> authorize new candidate
    -> validate candidate
    -> only then replace active in-memory token
```

If scheduled refresh fails:

```text
keep previous token
wait 300 seconds
retry
```

If a live usage call receives HTTP 401:

```text
reauthorize immediately
validate replacement
retry usage once
```

Concurrent HTTP 401 responses for the same active token trigger only one reauthorization;
the remaining requests reuse the validated replacement token.

No bearer token is written to `conf.json`, `proxy.env`, a database, or another runtime file.

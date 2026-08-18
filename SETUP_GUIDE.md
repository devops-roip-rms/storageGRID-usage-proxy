StorageGRID Usage Proxy - Setup and Test Guide

This guide covers both supported deployment methods:

Option A - Native Python: retained as a fallback and troubleshooting method.

Option B - Docker: recommended for the production Splunk gateway because Docker is already available there.

The proxy application logic is identical in both methods.

1. Requirements

Common

Network access from the Splunk gateway to the StorageGRID Tenant Management API over HTTPS.

Network access from HTTP-SNIFFER to TCP 8787 on the Splunk gateway.

The four real StorageGRID values in config/proxy.env.

Native deployment

Linux server / Splunk gateway.

Python 3.6 or newer. The application was specifically backported for Python 3.6.8.

No pip packages, Git, internet access, /etc application files, or system-wide installation are required.

Docker deployment

Docker Engine and Docker Compose plugin on the Splunk gateway.

The closed-network server does not require internet access.

The Docker image is built on a connected build machine or GitLab Runner and transferred as a .tar file.

2. Configuration

The runtime configuration file is:

config/proxy.env

Keep placeholder values in the Git repository. Enter real credentials only in the deployment copy on the target/build test system and do not commit those real values back to Git. Protect the deployment copy with:

chmod 600 config/proxy.env

Edit only the four required environment-specific values:

STORAGEGRID_BASE_URL=https://<real-storagegrid-host-or-ip>
STORAGEGRID_USERNAME=<real-tenant-username>
STORAGEGRID_ACCOUNT_ID=<real-tenant-account-id>
STORAGEGRID_PASSWORD=<real-tenant-password>

Keep the base URL limited to scheme and host/IP. Do not append /api/v4/authorize.

The normal defaults are:

AUTH_PATH=/api/v4/authorize
USAGE_PATH=/api/v4/org/usage
TOKEN_REFRESH_HOURS=10
REFRESH_RETRY_SECONDS=300
HTTP_TIMEOUT_SECONDS=30
MAX_RESPONSE_BYTES=10485760
TLS_VERIFY=true
CA_BUNDLE=
PROXY_BIND_HOST=0.0.0.0
PROXY_PORT=8787
PROXY_API_KEY=
LOG_LEVEL=INFO

The authorize body includes cookie: true and csrfToken: false directly in application code. There is no bootstrap bearer or bootstrap CSRF configuration.

3. Validate source before deployment

From the project folder:

sh scripts/setup.sh
./scripts/check-config.sh
./scripts/run-tests.sh

Expected:

configuration_ok=yes
Ran 35 tests
OK

Then test the real StorageGRID endpoint:

./scripts/test-upstream.sh

Expected:

upstream_test=ok
usage_status=200
usage_bytes=<number>

The command does not print the password or bearer token.

TLS

Keep:

TLS_VERIFY=true
CA_BUNDLE=

If Python reports certificate verification failure, obtain the approved StorageGRID/internal CA certificate, place it under certs/, and set for example:

CA_BUNDLE=certs/storagegrid-ca.pem

Do not use TLS_VERIFY=false as the normal production configuration.

Option A - Native Python

4A. Start and verify

./scripts/start.sh
./scripts/status.sh

Expected:

status=running
pid=<number>

Health checks:

curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/readyz
curl http://127.0.0.1:8787/storagegrid/usage

Normal operations:

./scripts/start.sh
./scripts/status.sh
./scripts/stop.sh

Native log:

logs/storagegrid-usage-proxy.log

5A. Native reboot autostart

After the native deployment works:

./scripts/install-autostart.sh

This uses the current user's crontab @reboot and does not install application files under /etc.

Remove it with:

./scripts/remove-autostart.sh

Do not install this crontab autostart when Docker is the production deployment method. Docker Compose uses restart: unless-stopped instead.

Option B - Docker - Recommended

4B. Docker files

The project contains:

Dockerfile
compose.yml
.dockerignore

The Docker image contains the Python application only. It does not contain config/proxy.env, credentials, runtime logs, or private CA certificates.

At runtime, Compose mounts:

./config/proxy.env -> /app/config/proxy.env:ro
./certs            -> /app/certs:ro

5B. Build manually on a connected machine

Choose a version, for example 1.0.0:

docker build \
  --build-arg APP_VERSION=1.0.0 \
  -t storagegrid-usage-proxy:1.0.0 \
  -t storagegrid-usage-proxy:latest \
  .

Run the source tests before export:

./scripts/run-tests.sh

Optional local container test:

docker compose -f compose.yml up -d
docker compose -f compose.yml ps
curl http://127.0.0.1:8787/readyz
docker compose -f compose.yml down

This local container test requires a valid config/proxy.env and connectivity from the build/test machine to StorageGRID.

6B. Export for the closed network

Export both the versioned tag and latest into one archive:

docker save \
  -o storagegrid-usage-proxy_1.0.0.tar \
  storagegrid-usage-proxy:1.0.0 \
  storagegrid-usage-proxy:latest

Create a checksum:

sha256sum storagegrid-usage-proxy_1.0.0.tar \
  > storagegrid-usage-proxy_1.0.0.tar.sha256

Transfer both files through the approved closed-network transfer process.

7B. Load on the Splunk gateway

Verify the transferred archive if sha256sum is available:

sha256sum -c storagegrid-usage-proxy_1.0.0.tar.sha256

Load it:

docker load -i storagegrid-usage-proxy_1.0.0.tar

Confirm:

docker image ls storagegrid-usage-proxy

Because the archive contains the latest tag, compose.yml does not need to change for each release.

8B. Start Docker deployment

From the project folder on the Splunk gateway:

docker compose -f compose.yml up -d

Check:

docker compose -f compose.yml ps
docker inspect --format='{{.State.Health.Status}}' storagegrid-usage-proxy

Logs:

docker compose -f compose.yml logs -f storagegrid-usage-proxy

Live endpoint tests:

curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/readyz
curl http://127.0.0.1:8787/storagegrid/usage

Stop/remove the container:

docker compose -f compose.yml down

restart: unless-stopped handles process crashes and server reboot. No crontab entry is required for the Docker deployment.

9B. Upgrade Docker deployment

For a new release:

change source
   -> tests
   -> build new image
   -> docker save new .tar
   -> transfer
   -> docker load
   -> docker compose up -d

After loading a new archive that contains a new latest tag:

docker compose -f compose.yml up -d

If the existing container is still based on the previous image and Compose does not recreate it automatically, run:

docker compose -f compose.yml up -d --force-recreate

This recreates only this proxy's separate Compose project; it does not touch HTTP-SNIFFER.

GitLab CI pipeline

10. Pipeline behavior

The included .gitlab-ci.yml runs:

All pushes / merge requests
    -> Python 3.6 test suite
    -> Python 3.11 test suite

Default branch or Git tag
    -> Docker build
    -> tag image with Git tag or short commit SHA
    -> also tag image as latest
    -> docker save to .tar
    -> generate SHA256 checksum
    -> publish GitLab artifacts

For releases, prefer Git tags such as:

v1.0.0
v1.0.1
v1.1.0

A tagged pipeline then produces an artifact such as:

storagegrid-usage-proxy_v1.0.1.tar
storagegrid-usage-proxy_v1.0.1.tar.sha256
IMAGE_VERSION.txt

11. GitLab Runner requirement

The packaging job uses Docker-in-Docker and therefore requires a GitLab Runner configured to allow privileged Docker-in-Docker.

If your future GitLab environment does not permit privileged Docker-in-Docker, the application does not need to change; only the image-build job should be adapted to your approved GitLab build mechanism (for example an internal build runner or another OCI builder).

HTTP-SNIFFER configuration

12. Configure HTTP-SNIFFER once

The StorageGRID usage entry should call the proxy instead of StorageGRID directly:

{
  "name": "StorageGRID-usage",
  "src_url": "http://<splunk-gateway-IP>:8787/storagegrid/usage",
  "dst_url": "http://<existing-splunk-gateway-destination>",
  "src_header_name": "",
  "src_header_value": "",
  "dst_header_name": "",
  "dst_header_value": ""
}

Leave the existing real dst_url unchanged.

If PROXY_API_KEY is configured, HTTP-SNIFFER must send the same value in:

X-StorageGRID-Proxy-Key

The proxy never edits HTTP-SNIFFER configuration at runtime.

13. Container/network verification

Because HTTP-SNIFFER is a separate container/Compose project, verify from its network context that it can reach:

http://<splunk-gateway-IP>:8787/storagegrid/usage

PROXY_BIND_HOST=0.0.0.0 is the packaged default for this deployment model.

Token behavior

14. Automatic 10-hour logic

On process/container start:

immediate authorize
    -> validate candidate with /api/v4/org/usage
    -> install token in RAM only

Then:

wait 10 hours
    -> authorize new candidate
    -> validate candidate
    -> only then replace active in-memory token

If scheduled refresh fails:

keep previous token
wait 300 seconds
retry

If a live usage call receives HTTP 401:

reauthorize immediately
validate replacement
retry usage once

No bearer token is written to conf.json, proxy.env, a database, or another runtime file.
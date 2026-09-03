# StorageGRID Usage Proxy - Setup and Deployment Guide

This guide is the production deployment procedure for the closed-network Docker server.

The production server does **not** need the source repository, Python source files, tests, Git metadata, CI files, or Dockerfile.

## 1. Requirements

The target server needs:

- Linux with Docker Engine;
- Docker Compose plugin (`docker compose`);
- network access to the StorageGRID Tenant Management API over HTTPS;
- TCP `8787` reachable from HTTP-SNIFFER to the proxy server;
- the approved StorageGRID/internal CA certificate;
- the prebuilt offline Docker image TAR and its SHA256 file.

Internet access is not required on the target server.

## 2. Production server directory

Create a permanent deployment directory:

```bash
sudo mkdir -p /mnt/storagegrid-usage-proxy/certs
sudo chown -R "$(id -u):$(id -g)" /mnt/storagegrid-usage-proxy
cd /mnt/storagegrid-usage-proxy
```

The final runtime directory should be:

```text
/mnt/storagegrid-usage-proxy/
├── compose.yml
├── proxy.env
└── certs/
    └── storagegrid-ca.pem
```

The image TAR and checksum may be copied into this directory temporarily for loading, or kept in an approved transfer directory.

## 3. Required `compose.yml`

The production Compose file should use the already-loaded local image and mount only the runtime configuration and certificate material:

```yaml
services:
  storagegrid-usage-proxy:
    image: storagegrid-usage-proxy:latest
    container_name: storagegrid-usage-proxy
    restart: unless-stopped

    ports:
      - "8787:8787"

    volumes:
      - ./proxy.env:/app/config/proxy.env:ro
      - ./certs:/app/certs:ro

    read_only: true

    tmpfs:
      - /tmp

    cap_drop:
      - ALL

    security_opt:
      - no-new-privileges:true

    stop_grace_period: 15s
```

Important: the host-side configuration path is `./proxy.env`. Inside the container it is mounted as `/app/config/proxy.env`.

## 4. Configure `proxy.env`

Protect the file:

```bash
chmod 600 proxy.env
```

The five required production values are:

```ini
STORAGEGRID_BASE_URL=https://<real-storagegrid-host-or-ip>
STORAGEGRID_USERNAME=<real-tenant-username>
STORAGEGRID_ACCOUNT_ID=<real-tenant-account-id>
STORAGEGRID_PASSWORD=<real-tenant-password>
PROXY_API_KEY=<strong-local-shared-secret>
```

Keep the normal API paths and runtime defaults:

```ini
AUTH_PATH=/api/v4/authorize
USAGE_PATH=/api/v4/org/usage

TOKEN_REFRESH_HOURS=10
REFRESH_RETRY_SECONDS=300

HTTP_TIMEOUT_SECONDS=30
MAX_RESPONSE_BYTES=10485760

TLS_VERIFY=true
CA_BUNDLE=/app/certs/storagegrid-ca.pem

PROXY_BIND_HOST=0.0.0.0
PROXY_PORT=8787
ALLOW_UNAUTHENTICATED_NONLOOPBACK=false

LOG_LEVEL=INFO
```

Do not append `/api/v4/authorize` or `/api/v4/org/usage` to `STORAGEGRID_BASE_URL`.

`ALLOW_UNAUTHENTICATED_NONLOOPBACK=true` is not the recommended production configuration.

## 5. Install the CA certificate

Copy the approved CA certificate or CA chain to:

```text
/mnt/storagegrid-usage-proxy/certs/storagegrid-ca.pem
```

Set normal read permissions:

```bash
chmod 644 certs/storagegrid-ca.pem
```

The host path:

```text
./certs/storagegrid-ca.pem
```

is visible inside the container as:

```text
/app/certs/storagegrid-ca.pem
```

Therefore `proxy.env` must contain:

```ini
TLS_VERIFY=true
CA_BUNDLE=/app/certs/storagegrid-ca.pem
```

## 6. Transfer and verify the Docker image

Transfer the release artifacts through the approved offline process:

```text
storagegrid-usage-proxy_<version>.tar
storagegrid-usage-proxy_<version>.tar.sha256
```

If `IMAGE_VERSION.txt` is supplied by CI, transfer it as well.

Verify the archive before loading:

```bash
sha256sum -c storagegrid-usage-proxy_<version>.tar.sha256
```

Expected result:

```text
storagegrid-usage-proxy_<version>.tar: OK
```

Do not load an archive that fails checksum verification.

## 7. Load the Docker image

```bash
docker load -i storagegrid-usage-proxy_<version>.tar
```

Confirm the tags:

```bash
docker image ls storagegrid-usage-proxy
```

The preferred release TAR contains both:

```text
storagegrid-usage-proxy:<version>
storagegrid-usage-proxy:latest
```

If the transferred archive contains only the versioned tag, create the `latest` tag before starting Compose:

```bash
docker tag storagegrid-usage-proxy:<version> storagegrid-usage-proxy:latest
```

## 8. Start the proxy

From `/mnt/storagegrid-usage-proxy`:

```bash
docker compose config
docker compose up -d
```

Check status:

```bash
docker compose ps
```

Check logs:

```bash
docker compose logs --tail=100 storagegrid-usage-proxy
```

Do not expect the bearer token or password to appear in logs.

## 9. Verify the service

Process health:

```bash
curl -i http://127.0.0.1:8787/healthz
```

Operational readiness:

```bash
curl -i http://127.0.0.1:8787/readyz
```

Once StorageGRID authorization succeeds, `/readyz` should return HTTP `200`.

Test the protected usage endpoint locally:

```bash
curl -i \
  -H 'X-StorageGRID-Proxy-Key: <same-value-as-PROXY_API_KEY>' \
  http://127.0.0.1:8787/storagegrid/usage
```

A request without the correct key should not be accepted in the recommended non-loopback production configuration.

## 10. Configure HTTP-SNIFFER

Change only the StorageGRID usage source so it calls the proxy.

Example:

```json
{
  "name": "StorageGRID-usage",
  "src_url": "http://<proxy-server-IP>:8787/storagegrid/usage",
  "dst_url": "<KEEP THE EXISTING REAL DESTINATION URL>",
  "src_header_name": "X-StorageGRID-Proxy-Key",
  "src_header_value": "<SAME VALUE AS PROXY_API_KEY>",
  "dst_header_name": "",
  "dst_header_value": ""
}
```

Keep the existing real `dst_url` unchanged.

The proxy does not update HTTP-SNIFFER configuration later and does not restart HTTP-SNIFFER during bearer-token refresh.

## 11. Verify from HTTP-SNIFFER's network context

If HTTP-SNIFFER runs in Docker, do not test with `127.0.0.1` from inside that container unless the proxy is in the same container/network namespace.

Verify that HTTP-SNIFFER can reach:

```text
http://<proxy-server-IP>:8787/storagegrid/usage
```

and that it sends:

```text
X-StorageGRID-Proxy-Key: <shared key>
```

## 12. Token behavior

On container start:

```text
POST /api/v4/authorize
    -> candidate bearer token
    -> validate with GET /api/v4/org/usage
    -> install token in RAM only
```

Normal refresh:

```text
wait configured interval
    -> authorize candidate
    -> validate candidate
    -> replace active token only after validation
```

If scheduled refresh fails:

```text
keep previous working token
wait REFRESH_RETRY_SECONDS
retry
```

If live usage returns HTTP `401`:

```text
reauthorize
validate replacement
retry the usage request once
```

The dynamic StorageGRID bearer token must not be written to `proxy.env`, HTTP-SNIFFER configuration, logs, a database, or a runtime token file.

## 13. Restart and reboot behavior

Compose uses:

```yaml
restart: unless-stopped
```

This restarts the container after a process failure or host reboot according to Docker restart-policy behavior.

A container becoming `unhealthy` does not by itself cause `restart: unless-stopped` to restart it.

## 14. Upgrade procedure

Transfer the new release TAR and SHA256 file, then:

```bash
cd /mnt/storagegrid-usage-proxy
sha256sum -c storagegrid-usage-proxy_<new-version>.tar.sha256
docker load -i storagegrid-usage-proxy_<new-version>.tar
docker compose up -d --force-recreate
```

Verify again:

```bash
docker compose ps
curl -i http://127.0.0.1:8787/healthz
curl -i http://127.0.0.1:8787/readyz
```

This Compose project should contain only the StorageGRID Usage Proxy, so recreating it does not restart HTTP-SNIFFER.

## 15. Stop/remove the proxy

```bash
cd /mnt/storagegrid-usage-proxy
docker compose down
```

This removes the proxy container only. It does not delete the locally loaded image, `proxy.env`, or the CA certificate.

## 16. Production deployment checklist

- Docker Engine is running.
- Docker Compose plugin is available.
- `compose.yml` is present.
- `proxy.env` is present and mode `600`.
- `certs/storagegrid-ca.pem` is present.
- `TLS_VERIFY=true`.
- `CA_BUNDLE=/app/certs/storagegrid-ca.pem`.
- `PROXY_API_KEY` is configured.
- `ALLOW_UNAUTHENTICATED_NONLOOPBACK=false`.
- TAR checksum passes.
- Docker image loads successfully.
- `storagegrid-usage-proxy:latest` exists locally.
- Container starts.
- `/healthz` is HTTP `200`.
- `/readyz` becomes HTTP `200` after authorization.
- Protected `/storagegrid/usage` returns live StorageGRID usage.
- HTTP-SNIFFER can reach the proxy.
- HTTP-SNIFFER sends `X-StorageGRID-Proxy-Key`.
- Splunk receives the expected event.

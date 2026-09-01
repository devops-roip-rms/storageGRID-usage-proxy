# StorageGRID Usage Proxy

A small long-running proxy for a closed-network Splunk gateway. HTTP-SNIFFER calls one stable local endpoint while the proxy manages the StorageGRID Tenant API bearer token in memory.

## Runtime architecture

```text
HTTP-SNIFFER
     |
     | GET http://<gateway-ip>:8787/storagegrid/usage
     | X-StorageGRID-Proxy-Key: <shared key>
     v
StorageGRID Usage Proxy
     |
     | POST /api/v4/authorize
     | GET  /api/v4/org/usage
     | Authorization: Bearer <token kept in RAM only>
     v
StorageGRID Tenant API
     |
     | usage JSON
     v
StorageGRID Usage Proxy -> HTTP-SNIFFER -> Splunk
```

The proxy:

- obtains a bearer token from `POST /api/v4/authorize`;
- validates a candidate token with `GET /api/v4/org/usage` before activating it;
- refreshes the token on the configured interval;
- keeps the previous working token if a scheduled refresh fails;
- reauthorizes and retries once after an upstream HTTP `401`;
- keeps the dynamic bearer token in process memory only;
- does not edit HTTP-SNIFFER configuration at runtime;
- does not restart HTTP-SNIFFER when the bearer token changes.

## Recommended production deployment

Docker is the recommended production method.

The closed-network server does not need the source repository. The production runtime directory is intentionally small:

```text
/opt/storagegrid-usage-proxy/
├── compose.yml
├── proxy.env
└── certs/
    └── storagegrid-ca.pem
```

The Docker image is transferred separately as an offline release artifact, for example:

```text
storagegrid-usage-proxy_<version>.tar
storagegrid-usage-proxy_<version>.tar.sha256
```

The release archive should contain both:

```text
storagegrid-usage-proxy:<version>
storagegrid-usage-proxy:latest
```

After transfer, the server verifies the checksum, loads the image with `docker load`, and starts the service with Docker Compose.

See [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) for the exact production procedure.

## Production configuration

The production proxy listens on a non-loopback address so HTTP-SNIFFER can reach it. Therefore the recommended production configuration requires five environment-specific values:

```ini
STORAGEGRID_BASE_URL=https://<storagegrid-host-or-ip>
STORAGEGRID_USERNAME=<tenant-username>
STORAGEGRID_ACCOUNT_ID=<tenant-account-id>
STORAGEGRID_PASSWORD=<tenant-password>
PROXY_API_KEY=<strong-local-shared-secret>
```

Keep the secure listener defaults:

```ini
PROXY_BIND_HOST=0.0.0.0
ALLOW_UNAUTHENTICATED_NONLOOPBACK=false
```

HTTP-SNIFFER must send the same `PROXY_API_KEY` value in:

```text
X-StorageGRID-Proxy-Key
```

## TLS

TLS verification remains enabled in production:

```ini
TLS_VERIFY=true
CA_BUNDLE=/app/certs/storagegrid-ca.pem
```

On the host, the approved CA certificate is stored at:

```text
./certs/storagegrid-ca.pem
```

Compose mounts `./certs` read-only at `/app/certs`, so the application sees the certificate as:

```text
/app/certs/storagegrid-ca.pem
```

Do not use `TLS_VERIFY=false` as the normal production configuration.

## Health endpoints

```text
GET /healthz
GET /readyz
GET /storagegrid/usage
```

`/healthz` indicates that the proxy process is running. `/readyz` represents operational readiness, including token/readiness state. Docker `restart: unless-stopped` restarts the container when the process exits; it does not restart a container merely because Docker marks it unhealthy.

## Source repository and release flow

The source repository keeps development, tests, CI/CD, Dockerfile, and documentation. `TAG` is the Docker release-version source.

Typical release flow:

```text
source change
    -> tests
    -> update TAG for a release
    -> CI builds versioned + latest image
    -> docker save
    -> SHA256
    -> approved offline transfer
    -> docker load on closed server
    -> docker compose up -d --force-recreate
```

No Docker registry is required on the closed server when the image is transferred as a TAR.

## Documentation

- [Setup and Deployment Guide](docs/SETUP_GUIDE.md)
- [Build and Validation Report](docs/BUILD_REPORT.md)

# StorageGRID Usage Proxy - Current Build and Validation Report

## 1. Target architecture

The project is a small StorageGRID Tenant API usage proxy for HTTP-SNIFFER.

```text
HTTP-SNIFFER
    |
    | GET /storagegrid/usage
    v
StorageGRID Usage Proxy :8787
    |
    | POST /api/v4/authorize
    | GET  /api/v4/org/usage
    v
StorageGRID
```

The proxy owns the StorageGRID bearer token in memory. It does not write the token to disk,
does not modify HTTP-SNIFFER configuration at runtime, and does not restart HTTP-SNIFFER.

Both HTTP-SNIFFER and the proxy are Docker Compose services on the same Docker host. Port
`8787` is published so an operator on the approved network can also inspect the endpoint directly.

## 2. Token lifecycle

The configured normal refresh interval is exactly 10 hours:

```text
container start
    -> authorize
    -> validate candidate token with /api/v4/org/usage
    -> install validated token in memory

every 10 hours
    -> authorize a new token
    -> validate it
    -> replace active token only after validation

unexpected HTTP 401
    -> authorize again
    -> validate replacement
    -> retry the usage request once
    -> if recovery fails, use the configured retry backoff
```

A normal 10-hour refresh is not replaced by a 16-hour schedule, container restart, or
"refresh only after 401" behavior.

## 3. Production Docker deployment

Production uses a versioned image tag. `latest` is not used by Compose and is not exported
as part of the production offline artifact.

`TAG` is the source of truth for the release version.

For example:

```text
TAG
  v1.1.1
```

produces:

```text
storagegrid-usage-proxy:v1.1.1
storagegrid-usage-proxy_v1.1.1.tar
storagegrid-usage-proxy_v1.1.1.tar.sha256
.env                         IMAGE_TAG=v1.1.1
IMAGE_VERSION.txt            v1.1.1
compose.yml
```

The runtime configuration remains outside the image:

```text
config/proxy.env
certs/                       optional StorageGRID/internal CA material
```

The image contains no StorageGRID credentials.

## 4. Official production deployment contract

The production server already has Docker Engine and Docker Compose.

Transfer:

```text
storagegrid-usage-proxy_<VERSION>.tar
storagegrid-usage-proxy_<VERSION>.tar.sha256
.env
IMAGE_VERSION.txt
compose.yml
config/proxy.env
certs/                       only when an internal CA is required
```

Deploy:

```bash
sha256sum -c storagegrid-usage-proxy_<VERSION>.tar.sha256
docker load -i storagegrid-usage-proxy_<VERSION>.tar
docker compose up -d
```

`sha256sum -c` is an integrity check. It is not required by Docker; it verifies that the
transferred image archive matches the archive produced by CI before the archive is loaded.

Compose reads `IMAGE_TAG` from `.env` and therefore starts the exact version loaded into Docker.

The production server does not require Internet access and does not build the image.

## 5. Compose review

`compose.yml` intentionally contains one service.

Confirmed design:

- exact versioned image through `IMAGE_TAG`;
- no `latest` dependency;
- host port `8787` published for HTTP-SNIFFER and operator access;
- runtime configuration mounted read-only;
- optional CA directory mounted read-only;
- read-only container filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- `restart: unless-stopped`;
- 15-second graceful stop period.

No additional services, databases, queues, reverse proxies, or orchestration layers are required.

## 6. GitLab CI review

`.gitlab-ci.yml` supports both closed-network image-source options.

### Option B - Internal registry (recommended)

Default:

```yaml
IMAGE_PREFIX: "<registry.company.local>"
```

Required internal images:

```text
<registry.company.local>/python:3.6.15-slim-buster
<registry.company.local>/python:3.11-slim-bookworm
<registry.company.local>/docker:27.5.1-cli
<registry.company.local>/docker:27.5.1-dind
```

No Internet access is required.

### Option A - GitLab Dependency Proxy

Set the GitLab CI/CD variable:

```text
IMAGE_PREFIX=$CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX
```

The Dependency Proxy must already have access to the required images. In a completely
air-gapped environment, those images must be imported/cached before the pipeline runs.

### Runner requirement

The Docker packaging job uses Docker-in-Docker. The GitLab Runner must be configured
appropriately for that executor and must permit privileged Docker-in-Docker.

This is runner infrastructure and cannot be enabled safely by `.gitlab-ci.yml` itself.

The project does not invent a runner type because that is controlled by the GitLab administrator.

## 7. GitLab pipeline behavior

Tests:

```text
Python 3.6.15
Python 3.11
```

Packaging:

```text
TAG changes on default branch
OR
manual web pipeline
```

Both tests must pass before packaging.

The package job:

1. reads and validates `TAG`;
2. pulls the internal Python 3.11 base image;
3. builds the exact versioned application image;
4. saves that versioned image to a TAR;
5. creates SHA256, `.env`, and `IMAGE_VERSION.txt`;
6. publishes the deployment artifacts.

No `latest` tag is required.

## 8. GitHub Actions

GitHub remains supported.

The workflow continues to test Python 3.6.15 and Python 3.11 and builds the same versioned
Docker artifact model. It no longer exports `latest` as part of the offline production artifact.

## 9. Runtime API

Operator / HTTP-SNIFFER endpoint:

```text
GET http://<server>:8787/storagegrid/usage
```

StorageGRID endpoint used internally:

```text
GET /api/v4/org/usage
```

Authorization:

```text
POST /api/v4/authorize
```

The proxy does not require an application-level API key for the local network endpoint. The
approved closed network and host/network controls are the access boundary.

## 10. TLS

Normal production setting:

```ini
TLS_VERIFY=true
CA_BUNDLE=
```

If StorageGRID uses an internal CA that is not trusted by the Python runtime, provide the
approved CA PEM under `certs/` and configure `CA_BUNDLE` accordingly.

`TLS_VERIFY=false` is not the normal production configuration.

## 11. Validation performed on the supplied project

The modified application compiles successfully.

The application/unit test suite currently contains 32 unit tests after removing obsolete
proxy-API-key and non-loopback authentication tests. All 32 unit tests pass in this environment.
Two of the three end-to-end tests also pass. The remaining CLI subprocess test is blocked by
the execution environment's Python subprocess startup behavior described below.

The tests cover, among other things:

- 10-hour token refresh;
- refresh retry/backoff;
- validation before token activation;
- HTTP 401 reauthorization/retry;
- failed 401 recovery backoff;
- token extraction;
- StorageGRID request construction;
- response preservation;
- placeholder configuration validation;
- health/readiness behavior;
- direct `/storagegrid/usage` access.

The remaining end-to-end CLI subprocess test could not be completed in this execution
environment because the child Python process does not complete startup here. The test process
times out before the proxy's CLI test can finish. The same environment also emits an external
artifact-tool startup timeout during Python startup. This is an execution-environment limitation,
not a reported StorageGRID application failure.

Docker Engine is not available in this execution environment, so `docker compose config`,
`docker build`, `docker save`, and `docker load` cannot be executed here.

Therefore the following remain real-environment acceptance tests rather than claims of local proof:

1. Build with the actual internal registry.
2. Run the GitLab pipeline on the actual GitLab Runner.
3. Verify the Runner's Docker-in-Docker capability.
4. Load the resulting TAR on the closed production server.
5. Run `docker compose up -d`.
6. Verify `/healthz`, `/readyz`, and `/storagegrid/usage`.
7. Verify HTTP-SNIFFER reaches `http://<server>:8787/storagegrid/usage`.
8. Verify the proxy reaches StorageGRID `/api/v4/org/usage`.
9. Verify the 10-hour refresh in the real environment.

## 12. Final engineering decision

The project should remain intentionally small:

```text
HTTP-SNIFFER
      |
      v
StorageGRID Usage Proxy
      |
      v
StorageGRID Tenant API
```

No token-persistence mechanism, container restart automation, extra service, registry
deployment layer, Kubernetes layer, or application-level proxy API key is required for
the stated closed-network design.

The remaining unknowns are infrastructure facts, not reasons to invent application code:
the actual internal registry hostname/path and the GitLab Runner executor/configuration.
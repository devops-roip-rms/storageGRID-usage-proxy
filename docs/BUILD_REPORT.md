# StorageGRID Usage Proxy - Current Build and Validation Report

## Current architecture

The project is a StorageGRID Tenant API usage proxy for HTTP-SNIFFER on a closed-network Splunk gateway.

Runtime flow:

```text
HTTP-SNIFFER
    -> GET /storagegrid/usage
StorageGRID Usage Proxy
    -> GET /api/v4/org/usage with active bearer token
StorageGRID
    -> usage JSON
Proxy
    -> same JSON
HTTP-SNIFFER
    -> Splunk event
```

Token lifecycle:

```text
startup
  -> POST /api/v4/authorize
  -> receive candidate token
  -> validate with /api/v4/org/usage
  -> keep validated token in RAM

every 10 hours
  -> authorize candidate
  -> validate candidate
  -> replace active token only after validation

HTTP 401
  -> reauthorize
  -> validate replacement
  -> retry usage once
```

The proxy never edits HTTP-SNIFFER `conf.json`, never restarts HTTP-SNIFFER, and does not persist the bearer token.

## Confirmed environment

- Splunk gateway native Python: **3.6.8**.
- StorageGRID authorize endpoint: `/api/v4/authorize`.
- StorageGRID usage endpoint: `/api/v4/org/usage`.
- Authorization body:
  - `accountId`
  - `username`
  - `password`
  - `cookie: true`
  - `csrfToken: false`
- Authorization response returns the bearer token as a string in `data`.
- StorageGRID endpoint is HTTPS.
- Deployment target is a closed-network server with Docker available.
- Application files remain in a dedicated folder; no `/etc` application deployment is required.
- HTTP-SNIFFER remains a separate container/Compose project.

## Supported deployment methods

### Native Python

Retained as fallback/troubleshooting.

- Python 3.6+ compatible.
- Native start/status/stop scripts.
- Optional user-crontab reboot startup.

### Docker - recommended production deployment

- Runtime image based on `python:3.11-slim-bookworm`.
- `config/proxy.env` mounted read-only; credentials and `PROXY_API_KEY` are not baked
  into image layers.
- optional `certs/` mounted read-only.
- `restart: unless-stopped`.
- read-only root filesystem and dropped capabilities from `compose.yml`.
- offline transfer with `docker save` / `docker load`.
- Docker health uses `/readyz`; an unhealthy state does not by itself trigger
  `restart: unless-stopped`.
- Production non-loopback deployments require `PROXY_API_KEY` and HTTP-SNIFFER must send
  `X-StorageGRID-Proxy-Key`.

## Release versioning

`TAG` is now the single source of truth for Docker image versioning in both GitHub Actions and GitLab CI.

Example:

```text
v1.1.0
```

Both CI systems use it to produce:

```text
storagegrid-usage-proxy:v1.1.0
storagegrid-usage-proxy:latest
storagegrid-usage-proxy_v1.1.0.tar
storagegrid-usage-proxy_v1.1.0.tar.sha256
IMAGE_VERSION.txt containing v1.1.0
```

Normal code commits do not require a `TAG` change.

A deployable image should be packaged only when `TAG` changes on the default branch or when packaging is explicitly started manually.

## Application verification

Verified application behavior:

- Python compilation: PASS.
- Python 3.6 grammar compatibility: PASS.
- POSIX shell syntax: PASS.
- Placeholder runtime configuration rejection: PASS.
- Automated suite: **44/44 PASS**.
- `--check-config` accepts non-loopback binds only when `PROXY_API_KEY` is configured
  or the explicit unsafe override is enabled.
- Authorize body test: PASS.
- v4-like token extraction from `response.data`: PASS.
- Candidate validation before activation: PASS.
- 10-hour background refresh behavior: PASS.
- retry after scheduled refresh failure: PASS.
- HTTP 401 reauthorization/retry: PASS.
- failed 401-recovery backoff: PASS.
- token/password non-disclosure tests: PASS.
- proxy `/healthz`, `/readyz`, `/storagegrid/usage` endpoint tests: PASS.

## GitHub Actions verification

GitHub Actions is the current active CI system.

Current source compatibility expectation after local container validation:

```text
Python 3.6.15: 44/44 PASS
Python 3.11:   44/44 PASS
```

The updated 44-test suite still requires a real GitHub Actions run before it should be
recorded as GitHub-proven.

A later Python 3.11 Docker-based test attempt encountered Docker Hub `502 Bad Gateway` **before the test container started**. This was an external image-pull failure, not an application test failure.

The GitHub workflow was subsequently hardened:

- Python 3.6 Docker image pull: explicit retry.
- Python 3.11 test: `actions/setup-python`, avoiding Docker Hub for that test.
- packaging base image pull: explicit retry.
- `docker build` uses the successfully pre-pulled local base instead of requesting another `--pull`.
- image version read from `TAG`.
- packaging gated behind both compatibility test jobs.
- packaging release-driven by `TAG` change on the default branch, with manual workflow packaging also supported.

The newly adjusted release-trigger behavior must receive its first CI run after the workflow update; do not record it as production-proven until that run succeeds.

## GitLab CI status

GitLab support is included for the planned migration/copy, but the current GitLab pipeline has not yet been proven on the future GitLab Runner.

Required/current design:

- Python 3.6.15 test job.
- Python 3.11 test job.
- both tests required before packaging.
- version read from `TAG`.
- package on `TAG` change on the default branch or manual pipeline.
- Docker-in-Docker packaging.
- explicit retry when the packaging script pulls `python:3.11-slim-bookworm`.
- versioned + `latest` image export.
- SHA256 checksum and `IMAGE_VERSION.txt`.

### GitLab image-pull reliability

GitLab Runner pulls job/service images before job scripts execute. Therefore a retry loop inside `.gitlab-ci.yml` cannot protect the initial pull of the job image itself.

When the GitLab environment is prepared, use an approved resilience mechanism such as:

- GitLab Dependency Proxy;
- internal Docker registry/mirror;
- runner pull-policy fallback/retry.

This is a CI infrastructure concern and requires no application-code change.

## GitLab CI validation status

Confirmed locally:

- `.gitlab-ci.yml` parses as YAML and defines the expected test and packaging stages.
- The Docker-in-Docker job explicitly sets `DOCKER_HOST=tcp://docker:2375`, disables
  `DOCKER_TLS_CERTDIR`, gives the service the `docker` alias, validates `TAG`, and
  builds/saves both the versioned and `latest` image tags.
- GitLab job, service, and Docker build-base image references use
  `CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX`, so a configured GitLab Dependency Proxy
  protects the pulls that happen before the job script as well as the retried base-image pull.

Still requires a live GitLab pipeline:

- Confirm the selected GitLab Runner uses the Docker executor with `privileged = true` for
  Docker-in-Docker; this is runner configuration and cannot be granted by `.gitlab-ci.yml`.
- Confirm the group Dependency Proxy is enabled and accessible to the runner, or replace its
  image prefix with the approved self-hosted registry/mirror.
- Run a normal test pipeline and a `TAG`-change/manual packaging pipeline to confirm artifact
  upload and Docker-in-Docker behavior on that runner.

## Remaining real-environment verification

The following still require the real target environment:

1. Real StorageGRID credentials/account ID.
2. DNS/IP reachability from the Splunk gateway to StorageGRID.
3. Real TLS trust from the production Docker container/gateway.
4. HTTP-SNIFFER reachability to `<splunk-gateway-IP>:8787`.
5. First production Docker image build/export using the chosen `TAG`.
6. Transfer checksum verification and `docker load` on the closed gateway.
7. Container health and live usage after deployment.
8. First GitLab pipeline execution after the future migration.
9. GitLab Runner Docker-in-Docker capability and chosen image-pull cache/proxy policy.

## Current production recommendation

Use Docker for production and keep native Python as fallback.

Release process:

```text
normal source commits
       -> tests only

ready for a new deployment
       -> update TAG
       -> commit/push
       -> Python 3.6 + 3.11 tests
       -> build versioned + latest Docker image
       -> docker save
       -> SHA256
       -> transfer into closed network
       -> verify checksum
       -> docker load
       -> docker compose up -d --force-recreate
```

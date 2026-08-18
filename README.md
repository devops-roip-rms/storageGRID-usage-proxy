# StorageGRID Usage Proxy

A self-contained Python service for a closed-network Splunk gateway. It lets HTTP-SNIFFER read StorageGRID tenant usage through a stable local URL while this proxy owns the StorageGRID bearer token in memory.

## Final runtime logic

```text
HTTP-SNIFFER
     |
     | GET http://<splunk-gateway-IP>:8787/storagegrid/usage
     v
StorageGRID Usage Proxy
     |
     | current Bearer token kept in RAM only
     |
     +---- every 10 hours -----------------------------+
     |                                                 |
     | POST /api/v4/authorize                          |
     |        -> candidate token                       |
     | GET /api/v4/org/usage with candidate token     |
     |        -> validate before activating token      |
     |                                                 |
     +-------------------------------------------------+
     |
     | for every HTTP-SNIFFER usage request:
     | GET /api/v4/org/usage with active token
     v
StorageGRID
     |
     | usage JSON
     v
StorageGRID Usage Proxy -> HTTP-SNIFFER -> Splunk
```

If StorageGRID returns HTTP 401 for the active token, the proxy authorizes again, validates the replacement token, and retries that usage request once.

The proxy does **not** edit HTTP-SNIFFER `conf.json`, does **not** restart Docker/HTTP-SNIFFER, and does **not** save the StorageGRID bearer token to disk.

## Closed-network / dedicated-folder design

The whole project can be copied to any dedicated folder on the Splunk gateway. No `/etc` path, system-wide installation, Git repository, pip download, or internet access is required.

Python 3 and the Python standard library are sufficient.

```text
storagegrid-usage-proxy/
├── storagegrid_usage_proxy.py
├── config/
│   ├── proxy.env                 <- EDIT THIS FILE ONLY
│   └── proxy.env.example
├── certs/
│   └── .keep                     <- optional internal CA can be placed here
├── scripts/
│   ├── check-config.sh
│   ├── test-upstream.sh
│   ├── run-tests.sh
│   ├── run.sh
│   ├── start.sh
│   ├── status.sh
│   └── stop.sh
├── tests/
│   ├── test_storagegrid_usage_proxy.py
│   └── test_end_to_end.py
├── logs/
├── runtime/
├── requirements.txt
├── BUILD_REPORT.md
└── CODEX_PROMPT.md
```

## What you need to edit

Only edit:

```text
config/proxy.env
```

Required values:

```ini
STORAGEGRID_BASE_URL=https://<storagegrid-host-or-ip>
STORAGEGRID_USERNAME=<tenant-api-user>
STORAGEGRID_ACCOUNT_ID=<tenant-account-id>
STORAGEGRID_PASSWORD=<tenant-password>
```

`STORAGEGRID_BASE_URL` is the base URL only. Do not append `/api/v4/authorize` or `/api/v4/org/usage`; those are already configured separately:

```ini
AUTH_PATH=/api/v4/authorize
USAGE_PATH=/api/v4/org/usage
```

The default token rotation is:

```ini
TOKEN_REFRESH_HOURS=10
REFRESH_RETRY_SECONDS=300
```

If a 10-hour refresh fails, the currently active token is not replaced. The proxy retries the token refresh after the retry interval.

### Password in `proxy.env`

This final package intentionally keeps the password in the same dedicated `proxy.env` so no separate password file has to be created. Restrict the file on the server:

```bash
chmod 600 config/proxy.env
```

The password and bearer token are never intentionally written to logs.

## TLS

Recommended default:

```ini
TLS_VERIFY=true
CA_BUNDLE=
```

If the Splunk gateway already trusts the certificate used by StorageGRID, leave `CA_BUNDLE` empty.

If StorageGRID uses an internal CA that is not in the server trust store, copy the CA PEM certificate into `certs/` and set, for example:

```ini
CA_BUNDLE=certs/storagegrid-ca.pem
```

For a temporary connectivity test only, TLS verification can be disabled in the env file:

```ini
TLS_VERIFY=false
```

Do not leave it disabled unless that is an explicit accepted design decision for the environment.

## Proxy listener

Default:

```ini
PROXY_BIND_HOST=0.0.0.0
PROXY_PORT=8787
```

This is useful when HTTP-SNIFFER is inside Docker and must reach the proxy through the Splunk-gateway IP.

HTTP-SNIFFER should call the real gateway address, for example:

```text
http://10.10.10.20:8787/storagegrid/usage
```

It should never use `0.0.0.0` as a destination URL.

### Optional local proxy key

For a closed network this can remain empty:

```ini
PROXY_API_KEY=
```

If you set a value, HTTP-SNIFFER must send:

```text
X-StorageGRID-Proxy-Key: <same value>
```

This key is static and is unrelated to the StorageGRID bearer token.

## Ready-to-test procedure

### 1. Edit the env file

```bash
vi config/proxy.env
chmod 600 config/proxy.env
```

### 2. Run the included offline tests

These do not contact the real StorageGRID server:

```bash
./scripts/run-tests.sh
```

Expected:

```text
Ran 30 tests
OK
```

### 3. Validate the local configuration

```bash
./scripts/check-config.sh
```

Expected:

```text
configuration_ok=yes
```

This checks local settings and TLS configuration. It does not authenticate to StorageGRID.

### 4. Test the real StorageGRID API once

```bash
./scripts/test-upstream.sh
```

This performs exactly one authentication flow:

```text
POST /api/v4/authorize
        |
        v
candidate bearer token
        |
        v
GET /api/v4/org/usage
        |
        v
successful validation
```

Expected output resembles:

```text
upstream_test=ok
usage_status=200
usage_bytes=<number>
```

The token and password are not printed.

### 5. Start the proxy

```bash
./scripts/start.sh
```

Check process status:

```bash
./scripts/status.sh
```

Check HTTP health:

```bash
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/readyz
```

If you are testing from another host/container, use the Splunk-gateway IP instead of `127.0.0.1`.

### 6. Test the actual proxy usage endpoint

Without `PROXY_API_KEY`:

```bash
curl http://127.0.0.1:8787/storagegrid/usage
```

With `PROXY_API_KEY` configured:

```bash
curl -H 'X-StorageGRID-Proxy-Key: <key>' \
  http://127.0.0.1:8787/storagegrid/usage
```

A successful response is the live StorageGRID usage JSON returned by `/api/v4/org/usage`.

### 7. Configure HTTP-SNIFFER once

For the StorageGRID entry only:

```json
{
  "name": "StorageGRID-usage",
  "src_url": "http://<splunk-gateway-IP>:8787/storagegrid/usage",
  "dst_url": "http://<splunk-gateway-IP>/netapp?dc=XXXXXXX",
  "src_header_name": "",
  "src_header_value": "",
  "dst_header_name": "",
  "dst_header_value": ""
}
```

If `PROXY_API_KEY` is set, use:

```text
src_header_name  = X-StorageGRID-Proxy-Key
src_header_value = <same static key>
```

This is a **one-time** HTTP-SNIFFER configuration change. There are no token updates and no sniffer restarts every 10 hours.

## Normal operations

Start:

```bash
./scripts/start.sh
```

Status:

```bash
./scripts/status.sh
```

Stop:

```bash
./scripts/stop.sh
```

Foreground troubleshooting:

```bash
./scripts/run.sh
```

Log:

```text
logs/storagegrid-usage-proxy.log
```

PID:

```text
runtime/storagegrid-usage-proxy.pid
```

Routine HTTP access logging is DEBUG-level, so `LOG_LEVEL=INFO` does not generate one log line for every sniffer poll.

## HTTP endpoints

| Endpoint | Purpose |
|---|---|
| `GET /storagegrid/usage` | Live StorageGRID tenant usage for HTTP-SNIFFER |
| `GET /healthz` | Confirms the proxy HTTP process is alive |
| `GET /readyz` | Indicates whether a validated token is currently loaded |

## Failure behavior

| Condition | Result |
|---|---|
| Startup authorization fails | Process remains running; refresh loop retries |
| Scheduled token authorization fails | Existing token stays active; retry after `REFRESH_RETRY_SECONDS` |
| New token fails `/org/usage` validation | New token is discarded; existing token remains active |
| Active token receives HTTP 401 | Authorize + validate replacement + retry usage once |
| Usage returns non-401 upstream error | Proxy returns HTTP 502; no authentication loop |
| Proxy restarts | Bearer token is intentionally lost; startup authorization obtains a new one |
| Bad/missing env setting | Startup/check command fails before proxy service starts |

## Security notes

- StorageGRID bearer tokens exist only in process memory.
- The tenant password is stored only in the dedicated `config/proxy.env`; protect it with filesystem permissions.
- StorageGRID TLS verification is enabled by default.
- The proxy does not expose bearer tokens through health/readiness responses.
- Upstream error response bodies are not logged.
- The static local proxy key is optional and can be used if network-level restriction is not sufficient.

# StorageGRID Usage Proxy - Setup and Test Guide

This is the deployment guide for the final closed-network package.

## 1. Requirements

- Linux server / Splunk gateway.
- Python **3.6 or newer**. This package was specifically backported for the server's Python 3.6.8.
- Network access from the Splunk gateway to the StorageGRID Tenant Management API over HTTPS.
- Network access from HTTP-SNIFFER to the proxy listener on the Splunk gateway (default TCP 8787).
- No pip packages, Git, internet access, Docker restart, `/etc` application files, or system-wide Python installation are required.

## 2. Copy and enter the dedicated folder

Copy this complete folder to the desired permanent location on the Splunk gateway, then:

```bash
cd <dedicated-folder>/storagegrid-usage-proxy
```

Do not move the folder after installing reboot autostart unless you reinstall the autostart entry.

## 3. Prepare script permissions

Run this with `sh` so it works even if ZIP/RAR transfer lost executable permissions:

```bash
sh scripts/setup.sh
```

Expected:

```text
python=Python 3.6.8
scripts_ok=yes
setup_ok=yes
```

## 4. Edit the only required configuration file

Edit:

```text
config/proxy.env
```

Only these four values are required to be changed:

```ini
STORAGEGRID_BASE_URL=https://<real-storagegrid-host-or-ip>
STORAGEGRID_USERNAME=<real-tenant-username>
STORAGEGRID_ACCOUNT_ID=<real-tenant-account-id>
STORAGEGRID_PASSWORD=<real-tenant-password>
```

Use the base URL only. Do not append `/api/v4/authorize`.

The large account ID is kept as a string by the proxy, so it is not converted to a floating-point or integer value.

The rest of the authorize configuration already matches the known-working curl:

```ini
AUTH_PATH=/api/v4/authorize
AUTH_BOOTSTRAP_BEARER=00000000-0000-0000-0000-000000000000
AUTH_CSRF_HEADER_VALUE=00000000000000000000000000000000
AUTH_COOKIE=true
AUTH_CSRF_TOKEN=false
USAGE_PATH=/api/v4/org/usage
```

Default token refresh:

```ini
TOKEN_REFRESH_HOURS=10
REFRESH_RETRY_SECONDS=300
```

Protect the env file:

```bash
chmod 600 config/proxy.env
```

## 5. Validate local configuration

```bash
./scripts/check-config.sh
```

Expected:

```text
configuration_ok=yes
```

The packaged placeholder values are intentionally rejected. This command does not contact StorageGRID.

## 6. Run offline tests

```bash
./scripts/run-tests.sh
```

Expected final lines:

```text
Ran 35 tests
OK
```

These tests do not require the internet or the real StorageGRID server.

## 7. Test the real StorageGRID API

```bash
./scripts/test-upstream.sh
```

The test performs:

```text
POST /api/v4/authorize
  Authorization: Bearer 00000000-0000-0000-0000-000000000000
  X-Csrf-Token: 00000000000000000000000000000000
  Content-Type: application/json

  {
    "accountId": "...",
    "username": "...",
    "password": "...",
    "cookie": true,
    "csrfToken": false
  }
        |
        v
candidate bearer token from response.data
        |
        v
GET /api/v4/org/usage
Authorization: Bearer <candidate>
```

Expected:

```text
upstream_test=ok
usage_status=200
usage_bytes=<number>
```

Neither the tenant password nor bearer token is printed.

### TLS note

Keep:

```ini
TLS_VERIFY=true
CA_BUNDLE=
```

The supplied successful curl uses HTTPS and does not use `-k`, which indicates the Splunk gateway can already validate the StorageGRID HTTPS certificate in that working path.

If `test-upstream.sh` reports a certificate verification error, obtain the StorageGRID/internal CA certificate, copy it under `certs/`, and set for example:

```ini
CA_BUNDLE=certs/storagegrid-ca.pem
```

Use `TLS_VERIFY=false` only as a short diagnostic test, not as the normal configuration.

## 8. Start and verify the proxy

Start:

```bash
./scripts/start.sh
```

Check process:

```bash
./scripts/status.sh
```

Expected:

```text
status=running
pid=<number>
```

Check health:

```bash
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/readyz
```

Once authorization succeeds, `/readyz` returns HTTP 200 and `status=ready`.

Test live usage through the proxy:

```bash
curl http://127.0.0.1:8787/storagegrid/usage
```

The response should be the live JSON returned by StorageGRID `/api/v4/org/usage`.

## 9. Configure HTTP-SNIFFER once

Change only the StorageGRID usage entry so HTTP-SNIFFER calls this proxy instead of StorageGRID directly.

Example:

```json
{
  "name": "StorageGRID-usage",
  "src_url": "http://<splunk-gateway-IP>:8787/storagegrid/usage",
  "dst_url": "http://<existing-splunk-gateway-destination>",
  "src_header_name": "",
  "src_header_value": "",
  "dst_header_name": "",
  "dst_header_value": ""
}
```

Leave the existing `dst_url` exactly as it is in your real HTTP-SNIFFER configuration.

After this one-time change, HTTP-SNIFFER never needs the StorageGRID bearer token. The proxy does not edit `conf.json` and does not restart HTTP-SNIFFER.

## 10. Verify from HTTP-SNIFFER's network context

If HTTP-SNIFFER runs inside Docker, verify that it can reach:

```text
http://<splunk-gateway-IP>:8787/storagegrid/usage
```

`PROXY_BIND_HOST=0.0.0.0` is already the default in `proxy.env` for this reason.

## 11. Enable automatic start after gateway reboot

The application remains entirely in its dedicated folder. The included autostart installer uses the **current user's crontab** and does not install application files under `/etc`.

First verify the proxy works normally. Then run:

```bash
./scripts/install-autostart.sh
```

Expected:

```text
autostart_installed=yes
method=user_crontab_at_reboot
project=<absolute-project-path>
```

Verify:

```bash
crontab -l
```

You should see one line containing:

```text
storagegrid-usage-proxy-autostart
```

To remove it:

```bash
./scripts/remove-autostart.sh
```

If `crontab` is not installed/available on this server, `install-autostart.sh` stops with a clear error and does not change anything. In that case, the server's approved process manager must be used for reboot startup.

## 12. Normal operations

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

Log:

```text
logs/storagegrid-usage-proxy.log
```

PID file:

```text
runtime/storagegrid-usage-proxy.pid
```

## 13. Automatic 10-hour logic

The 10-hour schedule is internal to the continuously running Python process; cron is **not** used for token refresh.

On process start:

```text
immediate authorize -> validate with usage -> install token in RAM
```

Then:

```text
wait 10 hours
    -> authorize new candidate
    -> validate candidate with /api/v4/org/usage
    -> only then replace in-memory token
```

If a scheduled refresh fails:

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

No bearer token is stored in `conf.json` or any proxy file.

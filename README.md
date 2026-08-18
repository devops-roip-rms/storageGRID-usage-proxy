# StorageGRID Usage Proxy

A self-contained, Python 3.6-compatible proxy for a closed-network Splunk gateway.

HTTP-SNIFFER calls one stable local endpoint:

```text
GET http://<splunk-gateway-IP>:8787/storagegrid/usage
```

The proxy owns the StorageGRID bearer token in RAM, obtains/validates a token immediately at startup, refreshes it automatically every 10 hours, retries failed refreshes after 5 minutes, and reauthorizes once automatically if StorageGRID rejects the active token with HTTP 401.

It does **not** edit HTTP-SNIFFER `conf.json`, restart Docker, or persist the bearer token.

## Server compatibility

- Python 3.6+; specifically backported for Python **3.6.8**.
- Python standard library only.
- No pip, Git, internet access, `/etc` application files, or system-wide installation required.

## Only required edits

Edit only these four values in `config/proxy.env`:

```ini
STORAGEGRID_BASE_URL=https://<real-host-or-ip>
STORAGEGRID_USERNAME=<real-username>
STORAGEGRID_ACCOUNT_ID=<real-account-id>
STORAGEGRID_PASSWORD=<real-password>
```

The v4 authorize request uses the configured StorageGRID account ID,
username, and password and sends `cookie: true` and `csrfToken: false`., `cookie=true`, and `csrfToken=false`.

## First test

```bash
sh scripts/setup.sh
vi config/proxy.env
chmod 600 config/proxy.env
./scripts/check-config.sh
./scripts/run-tests.sh
./scripts/test-upstream.sh
./scripts/start.sh
./scripts/status.sh
curl http://127.0.0.1:8787/readyz
curl http://127.0.0.1:8787/storagegrid/usage
```

For complete deployment steps, HTTP-SNIFFER configuration, TLS handling, and reboot startup, read **[SETUP_GUIDE.md](SETUP_GUIDE.md)**.

## Project layout

```text
storagegrid-usage-proxy/
├── storagegrid_usage_proxy.py
├── config/
│   └── proxy.env                 # only file requiring environment values
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

FROM python:3.11-slim-bookworm

ARG APP_VERSION=dev

LABEL org.opencontainers.image.title="StorageGRID Usage Proxy" \
    org.opencontainers.image.description="StorageGRID Tenant API usage proxy for HTTP-SNIFFER" \
    org.opencontainers.image.version="$APP_VERSION"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The image contains application code only. Runtime configuration and optional
# CA certificates are bind-mounted by compose.yml and are not baked into layers.
COPY storagegrid_usage_proxy.py /app/storagegrid_usage_proxy.py

RUN python3 -m py_compile /app/storagegrid_usage_proxy.py \
    && mkdir -p /app/config /app/certs

EXPOSE 8787
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/readyz', timeout=3).read()" || exit 1

CMD ["python3", "/app/storagegrid_usage_proxy.py", "--env-file", "/app/config/proxy.env"]

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATE_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY lalux_paperless_sync ./lalux_paperless_sync
RUN pip install . \
 && useradd --uid 1000 --create-home app \
 && mkdir -p /data && chown app /data

USER app
VOLUME ["/data"]

HEALTHCHECK --interval=10m --timeout=10s --start-period=2m \
  CMD ["lalux-paperless-sync", "health"]

ENTRYPOINT ["lalux-paperless-sync"]
CMD ["run"]

FROM python:3.12-slim-bookworm

ARG VERSION=dev
LABEL org.opencontainers.image.title="science-assistant" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.description="Science Assistant MCP scientific data gateway"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCIENCE_ASSISTANT_CONFIG_DIR=/etc/science-assistant \
    SCIENCE_ASSISTANT_DATA_DIR=/var/science-assistant/data \
    SCIENCE_ASSISTANT_LOG_DIR=/var/log/science-assistant \
    SCIENCE_ASSISTANT_USER=scasuser \
    SCIENCE_ASSISTANT_GROUP=scasgrp \
    SCIENCE_ASSISTANT_HOST_PORT=18180

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md MANIFEST.in ./
COPY science_assistant ./science_assistant
COPY dist/client-release /usr/share/science-assistant/client-release
COPY dist/science-assistant-client-offline-*.zip /usr/share/science-assistant/client-release/
COPY dist/science-assistant-client-portable-*.zip /usr/share/science-assistant/client-release/
COPY dist/science-assistant-client-py313-linux-x86_64-*.zip /usr/share/science-assistant/client-release/
COPY dist/agent-release /usr/share/science-assistant/agent-release
COPY dist/science-assistant-agent-*.zip /usr/share/science-assistant/agent-release/
RUN pip install --no-cache-dir .
COPY docker/entrypoint.sh /usr/local/bin/science-assistant-entrypoint
RUN chmod 0755 /usr/local/bin/science-assistant-entrypoint \
    && mkdir -p /etc/science-assistant /var/science-assistant/data /var/log/science-assistant \
    && ln -sfn /var/log/science-assistant /app/logs

EXPOSE 18180
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsk https://127.0.0.1:18180/health || exit 1
ENTRYPOINT ["/usr/local/bin/science-assistant-entrypoint"]
CMD ["science-assistant-server", "--config", "/etc/science-assistant/science-assistant.json"]

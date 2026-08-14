#!/bin/bash
set -euo pipefail
[ -f /etc/default/science-assistant ] && . /etc/default/science-assistant
USER_NAME="${SCIENCE_ASSISTANT_USER:-scasuser}"
GROUP_NAME="${SCIENCE_ASSISTANT_GROUP:-scasgrp}"
CONTAINER="${SCIENCE_ASSISTANT_CONTAINER:-science-assistant}"
NETWORK="${SCIENCE_ASSISTANT_NETWORK:-smart-assistant}"
HOST_PORT="${SCIENCE_ASSISTANT_HOST_PORT:-18180}"
CONTAINER_PORT="${SCIENCE_ASSISTANT_CONTAINER_PORT:-18180}"
CONFIG_DIR="${SCIENCE_ASSISTANT_CONFIG_DIR:-/etc/science-assistant}"
LOG_DIR="${SCIENCE_ASSISTANT_LOG_DIR:-/var/log/science-assistant}"
IMAGE="${SCIENCE_ASSISTANT_IMAGE:-vasilyvz/science-assistant:latest}"
UID_VALUE="$(id -u "$USER_NAME")"
GID_VALUE="$(getent group "$GROUP_NAME" | cut -d: -f3)"
start_container() {
  docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    --network "$NETWORK" \
    --add-host mcp-proxy.techsup.od.ua:172.18.0.1 \
    --user "${UID_VALUE}:${GID_VALUE}" \
    -e SCIENCE_ASSISTANT_USER="$USER_NAME" \
    -e SCIENCE_ASSISTANT_GROUP="$GROUP_NAME" \
    -e SCAS_UID="$UID_VALUE" \
    -e SCAS_GID="$GID_VALUE" \
    -e HOME=/var/science-assistant/home \
    -e USER="$USER_NAME" \
    -e LOGNAME="$USER_NAME" \
    -e SCIENCE_ASSISTANT_HOST_PORT="$HOST_PORT" \
    -p "0.0.0.0:${HOST_PORT}:${CONTAINER_PORT}" \
    -v "${CONFIG_DIR}:${CONFIG_DIR}:ro" \
    -v "/var/science-assistant:/var/science-assistant:rw" \
    -v "${LOG_DIR}:${LOG_DIR}:rw" \
    "$IMAGE"
}
case "${1:-start}" in
  start|recreate) start_container ;;
  stop) docker rm -f "$CONTAINER" >/dev/null 2>&1 || true ;;
  pull) docker pull "$IMAGE" ;;
  status) docker inspect "$CONTAINER" ;;
  logs) docker logs --tail 200 "$CONTAINER" ;;
  *) echo "Usage: $0 start|recreate|stop|pull|status|logs" >&2; exit 2 ;;
esac

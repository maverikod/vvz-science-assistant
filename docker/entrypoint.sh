#!/bin/bash
set -euo pipefail
USER_NAME="${SCIENCE_ASSISTANT_USER:-scasuser}"
GROUP_NAME="${SCIENCE_ASSISTANT_GROUP:-scasgrp}"
UID_VALUE="${SCAS_UID:-1000}"
GID_VALUE="${SCAS_GID:-1000}"
if getent group "$GID_VALUE" >/dev/null 2>&1; then
  GROUP_NAME="$(getent group "$GID_VALUE" | cut -d: -f1)"
else
  groupadd -g "$GID_VALUE" "$GROUP_NAME"
fi
if getent passwd "$UID_VALUE" >/dev/null 2>&1; then
  USER_NAME="$(getent passwd "$UID_VALUE" | cut -d: -f1)"
else
  useradd -u "$UID_VALUE" -g "$GROUP_NAME" -d /var/science-assistant/home -m -s /usr/sbin/nologin "$USER_NAME"
fi
mkdir -p /var/science-assistant/data /var/science-assistant/home /var/log/science-assistant
AGENT_RELEASE_DIR=/var/science-assistant/data/releases/science-assistant-agent
mkdir -p "$AGENT_RELEASE_DIR"
if [ -d /usr/share/science-assistant/agent-release ]; then
  find "$AGENT_RELEASE_DIR" -mindepth 1 -maxdepth 1 -type f -delete
  cp -f /usr/share/science-assistant/agent-release/* "$AGENT_RELEASE_DIR/" 2>/dev/null || true
  chmod 0644 "$AGENT_RELEASE_DIR"/* 2>/dev/null || true
  [ ! -f "$AGENT_RELEASE_DIR/mcp_file_parts.py" ] || chmod 0755 "$AGENT_RELEASE_DIR/mcp_file_parts.py"
fi
chown -R "$UID_VALUE:$GID_VALUE" /var/science-assistant/data /var/science-assistant/home /var/log/science-assistant
exec gosu "$UID_VALUE:$GID_VALUE" "$@"

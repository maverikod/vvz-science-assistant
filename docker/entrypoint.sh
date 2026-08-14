#!/bin/bash
set -euo pipefail
# The container is started with `docker run --user <uid>:<gid>` (see
# docker/docker-run.sh), so this script already runs as the non-root service
# user end to end -- no root phase, no privilege drop. Host-side ownership of
# every mounted directory is established once by the Debian package's
# postinst (see docker/debian/DEBIAN/postinst) and matches the uid:gid the
# container is started with, so plain mkdir/cp here need no root privilege.
mkdir -p /var/science-assistant/data /var/science-assistant/home /var/log/science-assistant
AGENT_RELEASE_DIR=/var/science-assistant/data/releases/science-assistant-agent
mkdir -p "$AGENT_RELEASE_DIR"
if [ -d /usr/share/science-assistant/agent-release ]; then
  find "$AGENT_RELEASE_DIR" -mindepth 1 -maxdepth 1 -type f -delete
  cp -f /usr/share/science-assistant/agent-release/* "$AGENT_RELEASE_DIR/" 2>/dev/null || true
  chmod 0644 "$AGENT_RELEASE_DIR"/* 2>/dev/null || true
  [ ! -f "$AGENT_RELEASE_DIR/mcp_file_parts.py" ] || chmod 0755 "$AGENT_RELEASE_DIR/mcp_file_parts.py"
fi
exec "$@"

#!/bin/bash
set -euo pipefail
TARGET="${1:-/etc/science-assistant/mtls}"
GROUP="${2:-scasgrp}"
SOURCE_PROJECT="${SCIENCE_ASSISTANT_CERT_SOURCE_PROJECT:-/var/casmgr/watch_catalog/550e8400-e29b-41d4-a716-446655440001/scientific_research_large_galaxy_smbh/runtime/certs}"
TERMINAL_MTLS="${SCIENCE_ASSISTANT_TERMINAL_MTLS:-/etc/mcp-terminal/mtls_certificates}"
install -d -o root -g "$GROUP" -m 0750 "$TARGET"
copy_if_missing() {
  local src="$1" dst="$2" mode="$3"
  if [ ! -f "$dst" ] && [ -f "$src" ]; then install -o root -g "$GROUP" -m "$mode" "$src" "$dst"; fi
}
copy_if_missing "$SOURCE_PROJECT/science-assistant.crt" "$TARGET/server.crt" 0640
copy_if_missing "$SOURCE_PROJECT/science-assistant.key" "$TARGET/server.key" 0640
copy_if_missing "$SOURCE_PROJECT/proxy-ca.crt" "$TARGET/proxy-ca.crt" 0640
copy_if_missing "$SOURCE_PROJECT/registration-client.crt" "$TARGET/client.crt" 0640
copy_if_missing "$SOURCE_PROJECT/registration-client.key" "$TARGET/client.key" 0640
copy_if_missing "$SOURCE_PROJECT/registration-ca.crt" "$TARGET/registration-ca.crt" 0640
copy_if_missing "$TERMINAL_MTLS/mtls_certificates/client/mcp-proxy.crt" "$TARGET/client.crt" 0640
copy_if_missing "$TERMINAL_MTLS/mtls_certificates/client/mcp-proxy.key" "$TARGET/client.key" 0640
copy_if_missing "$TERMINAL_MTLS/mtls_certificates/ca/ca.crt" "$TARGET/registration-ca.crt" 0640
copy_if_missing "$TERMINAL_MTLS/ca/ca.crt" "$TARGET/proxy-ca.crt" 0640
missing=0
for f in server.crt server.key proxy-ca.crt client.crt client.key registration-ca.crt; do
  if [ ! -f "$TARGET/$f" ]; then echo "Missing $TARGET/$f" >&2; missing=1; fi
done
chown -R root:"$GROUP" "$TARGET"
find "$TARGET" -type d -exec chmod 0750 {} +
find "$TARGET" -type f -exec chmod 0640 {} +
exit "$missing"

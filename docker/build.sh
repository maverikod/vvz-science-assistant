#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SKIP_PUSH=0
SKIP_DEB=0
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-push) SKIP_PUSH=1 ;;
    --skip-deb) SKIP_DEB=1 ;;
    -h|--help) echo "Usage: ./build.sh [--skip-push] [--skip-deb]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

VERSION="$(python3 scripts/sync_versions.py)"
REPO="${SCIENCE_ASSISTANT_DOCKERHUB_REPO:-vasilyvz/science-assistant}"
CLIENT_WHEEL="science_assistant_client-${VERSION}-py3-none-any.whl"
SERVER_WHEEL="science_assistant-${VERSION}-py3-none-any.whl"

rm -rf dist/server dist/client-release dist/agent-release "dist/science-assistant-client-offline-${VERSION}.zip" "dist/science-assistant-client-portable-${VERSION}.zip" "dist/science-assistant-client-py313-linux-x86_64-${VERSION}.zip" "dist/science-assistant-agent-${VERSION}.zip"
mkdir -p dist/server dist/client-release/wheels

# Build server and client Python wheels from the same release version.
python3 -m pip wheel --disable-pip-version-check --no-deps --wheel-dir dist/server .
python3 -m pip wheel --disable-pip-version-check --no-deps --wheel-dir dist/client-release/wheels ./client

# Build a complete offline client wheelhouse. This includes the exact
# mcp-proxy-adapter dependency and test tools used for release acceptance.
python3 -m pip wheel --disable-pip-version-check --wheel-dir dist/client-release/wheels \
  "dist/client-release/wheels/${CLIENT_WHEEL}" \
  "pytest>=8" "pytest-asyncio>=0.23"

python3 scripts/build_client_release.py "$VERSION"
python3 scripts/build_portable_client_release.py "$VERSION"
python3 scripts/build_py313_client_release.py "$VERSION"
python3 scripts/build_agent_release.py "$VERSION"
python3 scripts/mcp_file_parts.py self-test >/dev/null
SENDER_TEST_ROOT="$(mktemp -d)"
printf 'sender-release-test' > "$SENDER_TEST_ROOT/source.bin"
python3 agent/mcp_file_sender.py prepare "$SENDER_TEST_ROOT/source.bin" --state "$SENDER_TEST_ROOT/state.json" --part-chars 8 --ttl 60 >/dev/null
python3 agent/mcp_file_sender.py next "$SENDER_TEST_ROOT/state.json" | python3 -m json.tool >/dev/null
rm -rf "$SENDER_TEST_ROOT"

# Test the wheel exactly as a consumer installs it, only from the wheelhouse.
TEST_ROOT="$(mktemp -d)"
cleanup() { rm -rf "$TEST_ROOT"; }
trap cleanup EXIT
python3 -m venv "$TEST_ROOT/venv"
"$TEST_ROOT/venv/bin/python" -m pip install --disable-pip-version-check --no-index \
  --find-links dist/client-release/wheels \
  "dist/client-release/wheels/${CLIENT_WHEEL}" pytest pytest-asyncio
"$TEST_ROOT/venv/bin/python" -m pip install --disable-pip-version-check \
  "dist/server/${SERVER_WHEEL}"
"$TEST_ROOT/venv/bin/python" -m pytest -q client/tests tests

# Validate the dedicated Python 3.13 offline environment exactly as it will be
# created under /mnt/data. Network is disabled deliberately.
docker run --rm --network none \
  -v "$ROOT/dist/client-py313/archive:/release:ro" \
  python:3.13-slim-bookworm sh -c \
  'cp -a /release /tmp/release && /tmp/release/create_venv.sh /tmp/scas-client-venv && /tmp/scas-client-venv/bin/python -c "import science_assistant_client, mcp_proxy_adapter; print(science_assistant_client.__version__)"'

# Image and Debian package are built only after both client environments pass.
docker build -f docker/Dockerfile --build-arg VERSION="$VERSION" -t "$REPO:$VERSION" -t "$REPO:latest" .
if [ "$SKIP_PUSH" -eq 0 ]; then
  docker push "$REPO:$VERSION"
  docker push "$REPO:latest"
fi
if [ "$SKIP_DEB" -eq 0 ]; then
  bash docker/build-deb.sh
  python3 scripts/verify_release.py
fi

echo "Built release $VERSION"
echo "  server wheel: dist/server/${SERVER_WHEEL}"
echo "  client wheel: dist/client-release/wheels/${CLIENT_WHEEL}"
echo "  client offline archive: dist/science-assistant-client-offline-${VERSION}.zip"
echo "  client portable archive: dist/science-assistant-client-portable-${VERSION}.zip"
echo "  client Python 3.13 archive: dist/science-assistant-client-py313-linux-x86_64-${VERSION}.zip"
echo "  agent helpers: dist/agent-release/mcp_file_parts.py, dist/agent-release/mcp_file_sender.py"
echo "  agent archive: dist/science-assistant-agent-${VERSION}.zip"
echo "  image: $REPO:$VERSION"
[ "$SKIP_DEB" -eq 1 ] || echo "  deb: dist/science-assistant-docker_${VERSION}_amd64.deb"

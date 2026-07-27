# science-assistant-client

High-level client for Science Assistant. It is built on `mcp_proxy_adapter.client.jsonrpc_client.JsonRpcClient`; it does not implement a second JSON-RPC/TLS stack.

## Installation

Online:

```bash
python -m pip install science_assistant_client-<version>-py3-none-any.whl
```

Offline release archive:

```bash
unzip science-assistant-client-offline-<version>.zip
python -m pip install --no-index --find-links wheels wheels/science_assistant_client-<version>-py3-none-any.whl
```

The archive contains the client wheel, the matching `mcp-proxy-adapter` wheel and its dependency wheels, examples and tests.

## Configuration

Environment variables:

- `SCIENCE_ASSISTANT_PROXY_PROTOCOL`, default `https`;
- `SCIENCE_ASSISTANT_PROXY_HOST`, default `mcp-proxy.techsup.od.ua`;
- `SCIENCE_ASSISTANT_PROXY_PORT`, default `3004`;
- `SCIENCE_ASSISTANT_PROXY_TOKEN_HEADER` and `SCIENCE_ASSISTANT_PROXY_TOKEN`;
- `SCIENCE_ASSISTANT_PROXY_CERT`, `SCIENCE_ASSISTANT_PROXY_KEY`, `SCIENCE_ASSISTANT_PROXY_CA`;
- `SCIENCE_ASSISTANT_SERVER_ID`, default `science-assistant-vvz`;
- `SCIENCE_ASSISTANT_COPY_NUMBER`, default `1`.

Client and server release versions must match. The first operation calls `info(include_markdown=false)` and raises `VersionMismatchError` when they differ.

## Python

```python
from science_assistant_client import ScienceAssistantClient

client = ScienceAssistantClient()
info = client.info()  # all pages are assembled automatically
client.upload_file("/mnt/data/input.fits", "incoming/input.fits")
client.download_file("exports/result.ecsv", "/mnt/data/result.ecsv")
result = client.call("astroquery_object", {"service": "simbad", "target": "M 31"})
```

Async usage:

```python
from science_assistant_client import AsyncScienceAssistantClient

async with AsyncScienceAssistantClient() as client:
    result = await client.query_catalog(catalog="J/ApJ/714/25", row_limit=10)
```

Uploads and downloads persist sidecar state files until completion. Retry with the same paths to resume from the server-confirmed raw-byte offset. Final size and SHA-256 are mandatory.

## CLI

```bash
science-assistant-client info
science-assistant-client upload /mnt/data/input.bin incoming/input.bin
science-assistant-client download incoming/input.bin /mnt/data/copy.bin
science-assistant-client call astroquery_object --params '{"service":"simbad","target":"M 31"}'
```


## Dedicated Python 3.13 environment

The release also contains `science-assistant-client-py313-linux-x86_64-<version>.zip`.
After extraction:

```bash
./create_venv.sh /mnt/data/science-assistant-client-venv
```

This creates an isolated environment and installs every dependency from the included wheelhouse without network access.

The direct `JsonRpcClient` transport still requires a network route to MCP Proxy. In an agent runtime where `call_server` is supplied as a callable bridge, pass a compatible `proxy_client` object to `AsyncScienceAssistantClient`. ChatGPT's isolated Python sandbox does not expose model tools as importable Python functions, so tool calls there remain controlled by the model runtime rather than by the venv.

## Library and CLI

The distribution is a normal Python package. Import `ScienceAssistantClient` or `AsyncScienceAssistantClient` from application code, and use the installed `science-assistant-client` CLI for shell workflows. The `package-upload` CLI command sends independently checksummed parts and requests verified server-side assembly.

The standalone `mcp_file_parts.py` model-tool bridge is a separate release artifact. It is intentionally not imported by this package and has no network dependencies.

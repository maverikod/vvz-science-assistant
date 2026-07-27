# Science Assistant

Science Assistant is an MCP scientific-data gateway built on `mcp-proxy-adapter`. It exposes command classes only; there is no parallel custom REST API.

## One release, one build

```bash
./build.sh --skip-push
```

One invocation builds and validates all release artifacts from the single version in the root `pyproject.toml`:

- `dist/server/science_assistant-<version>-py3-none-any.whl`;
- `dist/client-release/wheels/science_assistant_client-<version>-py3-none-any.whl`;
- complete offline client wheelhouse;
- `dist/science-assistant-client-offline-<version>.zip`;
- `vasilyvz/science-assistant:<version>` and `latest` Docker images;
- `dist/science-assistant-docker_<version>_amd64.deb`.

The build synchronizes the client version, installs the client wheel into an isolated environment, executes tests, builds the image and Debian package, and verifies every artifact version. A mismatch fails the build.

## Commands

Scientific acquisition:

- `astroquery_catalog`;
- `astroquery_object`;
- `astroquery_adql`;
- `download_file`.

MCP-native bidirectional file stream:

- `data_upload_begin`, `data_upload_chunk`, `data_upload_complete`, `data_upload_status`;
- `data_download_begin`, `data_download_chunk`, `data_download_status`.

Documentation:

- `info`, paginated by `page_size` and one-based `block_position`.

Full command metadata includes schemas, parameters, result contracts, examples, errors, best practices, query formats and pagination rules.

## Client package

`science-assistant-client` is a high-level wrapper over `mcp_proxy_adapter.client.jsonrpc_client.JsonRpcClient`. It hides MCP Proxy calls, Base64 chunks, offsets, retries, queue polling, resume state, pagination and SHA-256 verification.

The Debian package publishes the matching client release under:

```text
/var/science-assistant/data/releases/science-assistant-client/
```

Therefore the client wheel or offline archive can itself be downloaded through `data_download_*`.

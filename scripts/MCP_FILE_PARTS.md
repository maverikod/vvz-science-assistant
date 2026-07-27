# MCP file-parts helper

`mcp_file_parts.py` is a pure-stdlib helper for agent environments where MCP Proxy
is exposed only as a model tool and cannot be imported by a Python process.

It does **not** open sockets and does not depend on `mcp-proxy-adapter`. The script:

- creates a manifest with total size and SHA-256;
- prints one `data_package_part` payload at a time;
- prints the queued `data_package_wait` payload;
- accepts Base64 part responses for server-to-agent downloads;
- waits for all local parts, assembles atomically, and verifies whole-file SHA-256.

Typical agent-to-server flow:

```bash
python3 mcp_file_parts.py prepare FILE REMOTE_PATH --manifest transfer.json
python3 mcp_file_parts.py wait-payload transfer.json --envelope
python3 mcp_file_parts.py part-payload transfer.json 0 --envelope
```

The model starts `data_package_wait` with `use_queue=true`, invokes
`data_package_part` for every index, then polls the queued wait job.

Typical server-to-agent flow is also payload-driven:

```bash
python3 mcp_file_parts.py download-begin-payload REMOTE_PATH --envelope
python3 mcp_file_parts.py init-download begin-response.json --manifest transfer.json
python3 mcp_file_parts.py download-part-payload transfer.json 0 --envelope
python3 mcp_file_parts.py accept-part transfer.json response-0000.json
python3 mcp_file_parts.py assemble transfer.json OUTPUT --wait-seconds 300
```

All part indices are zero-based. The full file is accepted only when size and
SHA-256 match the manifest.

## Stateful `file_receive` sender

`mcp_file_sender.py` prepares a local file as one Base64 string, splits it into portions, emits the next complete `MCP_proxy.call_server` payload, and records each server response. The first response stores `upload_session_id`; the last response stores the permanent `file_id`.

```bash
python3 mcp_file_sender.py prepare FILE --state upload.json --ttl 900
python3 mcp_file_sender.py next upload.json
python3 mcp_file_sender.py accept upload.json response.json
```

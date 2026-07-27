"""Command-line interface for the packaged client."""
from __future__ import annotations
import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from .client import ScienceAssistantClient
from .version import __version__


def _print(value: Any) -> None:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Science Assistant MCP Proxy client")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="action", required=True)

    info = sub.add_parser("info", help="Read and assemble all info pages")
    info.add_argument("--page-size", type=int, default=80)

    call = sub.add_parser("call", help="Call any Science Assistant command")
    call.add_argument("command")
    call.add_argument("--params", default="{}", help="JSON object")
    call.add_argument("--queue", action="store_true")

    upload = sub.add_parser("upload", help="Upload a local file through MCP Proxy")
    upload.add_argument("local_path", type=Path)
    upload.add_argument("remote_path")
    upload.add_argument("--overwrite", action="store_true")
    upload.add_argument("--chunk-size", type=int, default=262144)

    package_upload = sub.add_parser("package-upload", help="Upload independent parts and assemble them server-side")
    package_upload.add_argument("local_path", type=Path)
    package_upload.add_argument("remote_path")
    package_upload.add_argument("--overwrite", action="store_true")
    package_upload.add_argument("--part-size", type=int, default=131072)
    package_upload.add_argument("--package-id")
    package_upload.add_argument("--wait-timeout", type=float, default=300)

    download = sub.add_parser("download", help="Download a server file through MCP Proxy")
    download.add_argument("remote_path")
    download.add_argument("local_path", type=Path)
    download.add_argument("--overwrite", action="store_true")
    download.add_argument("--chunk-size", type=int, default=262144)

    args = parser.parse_args()
    client = ScienceAssistantClient()
    if args.action == "info":
        _print(client.info(page_size=args.page_size))
    elif args.action == "call":
        params = json.loads(args.params)
        if not isinstance(params, dict):
            parser.error("--params must be a JSON object")
        _print(client.call(args.command, params, use_queue=args.queue))
    elif args.action == "upload":
        _print(client.upload_file(args.local_path, args.remote_path, overwrite=args.overwrite, chunk_size=args.chunk_size))
    elif args.action == "package-upload":
        _print(client.upload_package_file(
            args.local_path, args.remote_path, overwrite=args.overwrite, part_size=args.part_size,
            package_id=args.package_id, wait_timeout=args.wait_timeout,
        ))
    elif args.action == "download":
        _print(client.download_file(args.remote_path, args.local_path, overwrite=args.overwrite, chunk_size=args.chunk_size))

if __name__ == "__main__":
    main()

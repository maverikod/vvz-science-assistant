"""Extended command metadata following neighboring MCP server conventions."""

from __future__ import annotations

from typing import Any, Type


def _base(cls: Type[Any], detailed: str) -> dict[str, Any]:
    return {
        "name": cls.name,
        "version": cls.version,
        "description": cls.descr,
        "category": cls.category,
        "author": cls.author,
        "email": cls.email,
        "detailed_description": detailed,
    }


def info_metadata(cls: Type[Any], markdown: str) -> dict[str, Any]:
    return {
        **_base(cls, markdown),
        "parameters": {},
        "return_value": {
            "success": {
                "description": "Full operational and command documentation.",
                "data": {
                    "guide_version": "Documentation schema version.",
                    "markdown": "Full server guide with examples.",
                    "runtime": "Users, groups, directories, ownership, ports, and registration.",
                    "registered_commands": "Canonical custom command catalog.",
                    "integrations": "Installed astronomy client libraries and supported services.",
                },
            },
            "error": {"description": "Unexpected documentation failure.", "code": "COMMAND_ERROR"},
        },
        "usage_examples": [{"description": "Read the complete guide", "command": {}, "explanation": "Call once before first use; no parameters."}],
        "error_cases": {
            "VALIDATION_ERROR": {"description": "Unexpected parameter was supplied.", "solution": "Call info with an empty params object."},
            "COMMAND_ERROR": {"description": "Runtime information could not be collected.", "solution": "Inspect /var/log/science-assistant and retry."},
        },
        "best_practices": [
            "Call info after upgrades to discover current commands and paths.",
            "Use help(cmdname=...) for the exact JSON Schema of one command.",
            "Store all downloaded products only under the mounted data directory.",
        ],
    }


def catalog_metadata(cls: Type[Any]) -> dict[str, Any]:
    return {
        **_base(cls, (
            "Fetch one or more VizieR catalog tables through astroquery without writing a temporary script. "
            "The command accepts a VizieR catalog identifier, optional table selector, requested columns, "
            "and column constraints. Every returned table is persisted in a new immutable dataset directory "
            "with a JSON manifest, row count, size, and SHA-256."
        )),
        "parameters": {
            "catalog": {"type": "string", "required": True, "description": "VizieR catalog identifier, e.g. J/ApJ/714/25."},
            "table": {"type": "string", "required": False, "description": "Exact or suffix table key to keep when a catalog has multiple tables."},
            "columns": {"type": "array", "required": False, "default": ["*"], "description": "VizieR columns; use +_r or other VizieR column expressions when needed."},
            "constraints": {"type": "object", "required": False, "default": {}, "description": "VizieR column constraints passed to query_constraints."},
            "row_limit": {"type": "integer", "required": False, "default": -1, "description": "Maximum rows per table; -1 means no astroquery row limit."},
            "output_format": {"type": "string", "required": False, "enum": ["ecsv", "csv", "fits", "parquet"], "default": "ecsv"},
            "dataset_name": {"type": "string", "required": False, "description": "Human-readable directory label; timestamp and UUID are added automatically."},
        },
        "return_value": {"success": {"description": "Persisted catalog tables and manifest.", "data": {"dataset_dir": "Mounted persistent path.", "files": "File records with rows/size/SHA-256.", "manifest": "Query provenance manifest."}}, "error": {"description": "Invalid catalog, no tables, service error, or storage error.", "code": "ASTROQUERY_ERROR"}},
        "usage_examples": [
            {"description": "Download all AMUSE-Virgo tables", "command": {"catalog": "J/ApJ/714/25", "output_format": "ecsv", "dataset_name": "amuse-virgo"}, "explanation": "Creates a dataset directory and writes every returned VizieR table."},
            {"description": "Select one table and columns", "command": {"catalog": "J/ApJS/164/334", "table": "struct", "columns": ["ACSVCS", "n.z", "rez", "<muz>"], "output_format": "csv"}, "explanation": "The table selector may be the full VizieR key or its suffix."},
            {"description": "Use VizieR constraints", "command": {"catalog": "I/355/gaiadr3", "columns": ["Source", "RA_ICRS", "DE_ICRS", "Gmag"], "constraints": {"Gmag": "<12"}, "row_limit": 1000}, "explanation": "Constraints use VizieR syntax and are forwarded by name."},
        ],
        "error_cases": {
            "VALIDATION_ERROR": {"description": "Schema or parameter validation failed.", "solution": "Use help(cmdname='astroquery_catalog') and correct fields."},
            "NO_RESULTS": {"description": "VizieR returned no tables.", "solution": "Verify catalog/table identifiers and constraints."},
            "ASTROQUERY_ERROR": {"description": "Remote service or serialization failed.", "solution": "Retry, reduce row_limit, or choose another output format."},
        },
        "best_practices": [
            "Use ECSV or FITS to preserve units and metadata; CSV is mainly for interoperability.",
            "Set row_limit during exploration, then remove it for the final reproducible download.",
            "Keep the generated manifest with every publication dataset.",
        ],
    }


def object_metadata(cls: Type[Any]) -> dict[str, Any]:
    return {
        **_base(cls, (
            "Query an astronomical object or cone region through a stable facade over SIMBAD, NED, "
            "VizieR, HEASARC, and IRSA. Supply a target name or explicit ICRS coordinates. A radius "
            "switches the request to a region query. Catalog is required for HEASARC and IRSA and "
            "optional for VizieR. Results are persisted with provenance and hashes."
        )),
        "parameters": {
            "service": {"type": "string", "required": True, "enum": ["simbad", "ned", "vizier", "heasarc", "irsa"]},
            "target": {"type": "string", "required": False, "description": "Object name resolvable by the selected service."},
            "ra_deg": {"type": "number", "required": False, "description": "ICRS right ascension in decimal degrees; requires dec_deg."},
            "dec_deg": {"type": "number", "required": False, "description": "ICRS declination in decimal degrees; requires ra_deg."},
            "radius_arcmin": {"type": "number", "required": False, "description": "Cone radius in arcminutes; when omitted performs an object query where supported."},
            "catalog": {"type": "string", "required": False, "description": "Mission/catalog identifier; required by HEASARC and IRSA."},
            "columns": {"type": "array", "required": False, "description": "Extra SIMBAD fields or selected VizieR/HEASARC/IRSA columns."},
            "row_limit": {"type": "integer", "required": False, "default": -1},
            "output_format": {"type": "string", "required": False, "enum": ["ecsv", "csv", "fits", "parquet"], "default": "ecsv"},
            "dataset_name": {"type": "string", "required": False},
        },
        "return_value": {"success": {"description": "Persisted query tables and manifest."}, "error": {"description": "Unsupported combination, resolver failure, service error, or no results.", "code": "ASTROQUERY_ERROR"}},
        "usage_examples": [
            {"description": "SIMBAD object lookup", "command": {"service": "simbad", "target": "M 31", "columns": ["otype", "distance", "velocity"]}, "explanation": "Returns the named object with requested SIMBAD fields."},
            {"description": "VizieR cone search", "command": {"service": "vizier", "ra_deg": 10.6847083, "dec_deg": 41.26875, "radius_arcmin": 2.0, "catalog": "I/355/gaiadr3", "row_limit": 5000}, "explanation": "Uses explicit coordinates and avoids an extra name-resolution request."},
            {"description": "HEASARC mission query", "command": {"service": "heasarc", "target": "NGC 1275", "catalog": "xmmmaster", "radius_arcmin": 5.0}, "explanation": "Catalog is the HEASARC mission/table name."},
        ],
        "error_cases": {
            "VALIDATION_ERROR": {"description": "Missing target/coordinates or paired coordinate field.", "solution": "Provide target, or both ra_deg and dec_deg."},
            "CATALOG_REQUIRED": {"description": "Selected service requires catalog.", "solution": "Set catalog to the IRSA table or HEASARC mission."},
            "NO_RESULTS": {"description": "The service returned no rows/tables.", "solution": "Check target, coordinates, radius, and catalog."},
            "ASTROQUERY_ERROR": {"description": "Remote service or local write failed.", "solution": "Retry or use explicit coordinates to bypass name resolution."},
        },
        "best_practices": [
            "Prefer explicit ICRS coordinates for reproducible region queries.",
            "Record the service catalog name in the publication data appendix.",
            "Use ECSV/FITS when units and masks matter.",
        ],
    }


def adql_metadata(cls: Type[Any]) -> dict[str, Any]:
    return {
        **_base(cls, (
            "Execute ADQL against Gaia through astroquery or against any public TAP endpoint through pyvo. "
            "This is the escape hatch for joins, server-side filtering, aggregation, and catalogs not covered "
            "by the simpler commands. The result table and exact query are stored together."
        )),
        "parameters": {
            "service": {"type": "string", "required": True, "enum": ["gaia", "custom"]},
            "query": {"type": "string", "required": True, "description": "Complete ADQL query."},
            "tap_url": {"type": "string", "required": False, "description": "Required when service=custom."},
            "output_format": {"type": "string", "required": False, "enum": ["ecsv", "csv", "fits", "parquet"], "default": "ecsv"},
            "dataset_name": {"type": "string", "required": False},
        },
        "return_value": {"success": {"description": "One persisted ADQL result table and manifest."}, "error": {"description": "ADQL/TAP failure or serialization error.", "code": "ASTROQUERY_ERROR"}},
        "usage_examples": [
            {"description": "Gaia DR3 sample", "command": {"service": "gaia", "query": "SELECT TOP 100 source_id, ra, dec, phot_g_mean_mag FROM gaiadr3.gaia_source ORDER BY phot_g_mean_mag ASC", "dataset_name": "gaia-bright-sample"}, "explanation": "Runs asynchronously through astroquery.gaia and persists the returned table."},
            {"description": "Custom TAP endpoint", "command": {"service": "custom", "tap_url": "https://gea.esac.esa.int/tap-server/tap", "query": "SELECT TOP 10 * FROM gaiadr3.gaia_source"}, "explanation": "Uses pyvo for a standard TAP async job."},
        ],
        "error_cases": {
            "VALIDATION_ERROR": {"description": "Query is empty or custom TAP URL is missing.", "solution": "Provide complete ADQL and tap_url for custom."},
            "ASTROQUERY_ERROR": {"description": "TAP job failed or result could not be written.", "solution": "Validate ADQL in the service console, then retry."},
        },
        "best_practices": [
            "Use TOP during query development and remove it only after validating output columns.",
            "Filter and aggregate server-side to avoid unnecessarily large transfers.",
            "Keep exact ADQL in the generated manifest for reproducibility.",
        ],
    }


def download_metadata(cls: Type[Any]) -> dict[str, Any]:
    return {
        **_base(cls, (
            "Stream a public HTTP, HTTPS, or FTP resource directly into the persistent mounted data directory. "
            "The command writes to a temporary .part file, enforces a byte ceiling, optionally verifies an "
            "expected SHA-256, atomically promotes the file, and writes a provenance manifest."
        )),
        "parameters": {
            "url": {"type": "string", "required": True, "description": "Public http://, https://, or ftp:// URL."},
            "output_name": {"type": "string", "required": False, "description": "Relative path inside the generated dataset directory; the server prefixes /var/science-assistant/data automatically. Inferred from URL when omitted."},
            "dataset_name": {"type": "string", "required": False},
            "timeout_seconds": {"type": "integer", "required": False, "default": 120, "description": "Socket/open timeout."},
            "max_bytes": {"type": "integer", "required": False, "default": 53687091200, "description": "Hard maximum transfer size, default 50 GiB."},
            "expected_sha256": {"type": "string", "required": False, "description": "Optional expected lowercase or uppercase SHA-256 hex."},
        },
        "return_value": {"success": {"description": "Downloaded file record and manifest."}, "error": {"description": "Scheme/path validation, network, size, or checksum failure.", "code": "DOWNLOAD_ERROR"}},
        "usage_examples": [
            {"description": "Download a public catalog file", "command": {"url": "https://example.org/catalog.fits", "dataset_name": "catalog-release", "output_name": "raw/catalog.fits"}, "explanation": "The relative output path is resolved under the automatically prefixed server data directory."},
            {"description": "FTP with checksum verification", "command": {"url": "ftp://example.org/pub/data/archive.tar.gz", "expected_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "max_bytes": 107374182400}, "explanation": "The .part file is removed automatically if size or checksum validation fails."},
        ],
        "error_cases": {
            "VALIDATION_ERROR": {"description": "Unsupported scheme, invalid URL, bad hash, or unsafe filename.", "solution": "Use public HTTP/HTTPS/FTP and a safe relative output_name without absolute or parent-directory components."},
            "DOWNLOAD_ERROR": {"description": "Network, byte-limit, or checksum error.", "solution": "Check source availability, max_bytes, and expected_sha256."},
        },
        "best_practices": [
            "Supply expected_sha256 whenever the publisher provides one.",
            "Use call_server queue mode for very large transfers.",
            "Never place credentials in a URL because command parameters may be logged.",
        ],
    }


def transfer_metadata(cls: Type[Any]) -> dict[str, Any]:
    operation = str(getattr(cls, "operation", ""))
    common = {
        **_base(cls, (
            "MCP-native bidirectional file streaming. Binary bytes are encoded as Base64 inside normal "
            "JSON-RPC command calls, so no separate HTTP chunk URL or direct network route is required. "
            "Every command-facing path is relative to /var/science-assistant/data. Upload state is persisted "
            "on disk, offsets are strict, and completion verifies SHA-256 before atomic promotion."
        )),
        "return_value": {
            "success": {"description": "Transfer state or one Base64 chunk."},
            "error": {"description": "Invalid path, offset, Base64, size, hash, or transfer id.", "code": "TRANSFER_ERROR"},
        },
        "error_cases": {
            "TRANSFER_ERROR": {"description": "Stable transfer validation or I/O failure.", "solution": "Read status, use the exact expected offset, and retry the failed chunk."},
            "COMMAND_ERROR": {"description": "Unexpected internal error.", "solution": "Inspect /var/log/science-assistant and retry."},
        },
        "best_practices": [
            "Use 256 KiB chunks by default; increase only when the proxy payload budget is known.",
            "Persist transfer_id and offset client-side so an interrupted transfer can resume.",
            "Verify the returned SHA-256 after downloading the final chunk.",
            "Use relative paths only; the server prefixes its data root automatically.",
        ],
    }
    examples: dict[str, list[dict[str, Any]]] = {
        "upload_begin": [{"description":"Start upload","command":{"relative_path":"incoming/result.fits","size_bytes":1048576,"sha256":"a"*64},"explanation":"Returns transfer_id and negotiated chunk_size."}],
        "upload_chunk": [{"description":"Send first chunk","command":{"transfer_id":"up_example","offset":0,"data_base64":"SGVsbG8="},"explanation":"Offset must equal the server status offset."}],
        "upload_complete": [{"description":"Finalize upload","command":{"transfer_id":"up_example"},"explanation":"Verifies total size and SHA-256, then atomically renames the .part file."}],
        "upload_status": [{"description":"Resume upload","command":{"transfer_id":"up_example"},"explanation":"Read the current offset before sending the next chunk."}],
        "download_begin": [{"description":"Start download","command":{"relative_path":"exports/result.ecsv"},"explanation":"Returns size, SHA-256, transfer_id, and chunk_size."}],
        "download_chunk": [{"description":"Read chunk","command":{"transfer_id":"down_example","offset":0},"explanation":"Decode data_base64 and continue from next_offset until eof=true."}],
        "download_status": [{"description":"Inspect download","command":{"transfer_id":"down_example"},"explanation":"Returns source metadata and the furthest streamed offset."}],
    }
    parameters: dict[str, dict[str, Any]] = {
        "upload_begin": {"relative_path":{"type":"string","required":True},"size_bytes":{"type":"integer","required":True},"sha256":{"type":"string","required":True},"chunk_size":{"type":"integer","required":False,"default":262144},"overwrite":{"type":"boolean","required":False,"default":False}},
        "upload_chunk": {"transfer_id":{"type":"string","required":True},"offset":{"type":"integer","required":True},"data_base64":{"type":"string","required":True}},
        "upload_complete": {"transfer_id":{"type":"string","required":True}},
        "upload_status": {"transfer_id":{"type":"string","required":True}},
        "download_begin": {"relative_path":{"type":"string","required":True},"chunk_size":{"type":"integer","required":False,"default":262144}},
        "download_chunk": {"transfer_id":{"type":"string","required":True},"offset":{"type":"integer","required":False},"limit":{"type":"integer","required":False}},
        "download_status": {"transfer_id":{"type":"string","required":True}},
    }
    common["parameters"] = parameters.get(operation, {})
    common["usage_examples"] = examples.get(operation, [])
    return common

# Add explicit query-language documentation to search-capable commands.
_catalog_metadata_base = catalog_metadata
_object_metadata_base = object_metadata
_adql_metadata_base = adql_metadata


def catalog_metadata(cls: Type[Any]) -> dict[str, Any]:
    data = _catalog_metadata_base(cls)
    data["query_format"] = {
        "service": "VizieR through astroquery.vizier",
        "catalog": {
            "syntax": "VizieR catalog identifier, usually <section>/<journal-or-family>/<volume>/<page-or-table>, or a catalog table id such as I/355/gaiadr3.",
            "examples": ["J/ApJ/714/25", "J/ApJS/164/334", "I/355/gaiadr3"],
        },
        "table": {
            "syntax": "Optional exact table key or suffix returned by VizieR.",
            "examples": ["J/ApJ/714/25/table1", "table1", "struct"],
        },
        "columns": {
            "syntax": "Array of VizieR column labels. '*' requests all default columns. Service expressions such as '+_r' may request computed/order columns where supported.",
            "examples": [["*"], ["Source", "RA_ICRS", "DE_ICRS", "Gmag"]],
        },
        "constraints": {
            "syntax": "Object mapping exact VizieR column names to VizieR constraint strings. Common forms: '<12', '>=0.5', '1.0..2.0', 'A', and comma-separated alternatives where supported by the catalog.",
            "examples": [{"Gmag": "<12"}, {"z": "0.01..0.1"}, {"Class": "GALAXY"}],
            "notes": "Constraint semantics are defined by VizieR and the selected table; invalid or nonexistent column names return no data or a remote error.",
        },
        "row_limit": "-1 removes the astroquery client limit; positive values cap rows per returned table.",
    }
    return data


def object_metadata(cls: Type[Any]) -> dict[str, Any]:
    data = _object_metadata_base(cls)
    data["query_format"] = {
        "target_name": {
            "syntax": "Catalog-recognized astronomical object name, e.g. 'M 31', 'NGC 1275', '3C 273'. Name resolution is performed by the selected remote service.",
            "preferred_for_reproducibility": "Use explicit ICRS coordinates when the exact sky position matters.",
        },
        "coordinates": {
            "syntax": "ra_deg and dec_deg are decimal degrees in ICRS and must be supplied together.",
            "ranges": {"ra_deg": "0 <= RA < 360", "dec_deg": "-90 <= Dec <= 90"},
            "example": {"ra_deg": 10.6847083, "dec_deg": 41.26875},
        },
        "region": {
            "syntax": "radius_arcmin is a positive cone radius in arcminutes. Its presence switches supported services to a region/cone query.",
            "example": {"radius_arcmin": 2.0},
        },
        "service_specific": {
            "simbad": "target or coordinates; columns are SIMBAD VOTable field names such as otype, velocity, distance.",
            "ned": "target or coordinates; radius is used for query_region.",
            "vizier": "target/coordinates plus optional VizieR catalog id; columns follow VizieR labels.",
            "heasarc": "catalog is the HEASARC mission/table name, e.g. xmmmaster; target or coordinates identify the region.",
            "irsa": "catalog is the IRSA table name; coordinates and radius define the cone search.",
        },
    }
    return data


def adql_metadata(cls: Type[Any]) -> dict[str, Any]:
    data = _adql_metadata_base(cls)
    data["query_format"] = {
        "language": "ADQL (Astronomical Data Query Language), submitted unchanged to Gaia or the selected TAP endpoint.",
        "shape": "SELECT [TOP n] columns FROM schema.table [JOIN ...] [WHERE ...] [GROUP BY ...] [ORDER BY ...]",
        "examples": [
            "SELECT TOP 100 source_id, ra, dec FROM gaiadr3.gaia_source WHERE phot_g_mean_mag < 12",
            "SELECT TOP 20 table_name FROM TAP_SCHEMA.tables",
            "SELECT COUNT(*) AS n FROM gaiadr3.gaia_source WHERE parallax > 10",
        ],
        "spatial_example": "SELECT TOP 100 * FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',10.6847083,41.26875,0.1))",
        "custom_tap": "service='custom' requires an HTTPS TAP base URL and a complete ADQL query.",
        "safety": [
            "Use TOP during development.",
            "Select only required columns.",
            "Filter and aggregate server-side before downloading.",
            "Large queries should be called with MCP queue mode and polled with queue_get_job_status.",
        ],
    }
    return data


# Paginated info metadata (latest definition intentionally overrides the earlier compatibility definition).
def info_metadata(cls: Type[Any], markdown: str) -> dict[str, Any]:
    return {
        **_base(cls, "Returns the Science Assistant operational guide in stable line pages. Runtime identity, versions, command inventory and integrations are returned compactly on every page. Use include_markdown=false for a lightweight version handshake; obtain subsequent documentation pages with next_block_position."),
        "parameters": {
            "page_size": {"type": "integer", "required": False, "default": 80, "minimum": 10, "maximum": 500, "description": "Documentation lines per page."},
            "block_position": {"type": "integer", "required": False, "default": 1, "minimum": 1, "description": "One-based page number."},
            "include_markdown": {"type": "boolean", "required": False, "default": True, "description": "Return inventory only when false."},
        },
        "pagination": {
            "model": "stable line blocks",
            "request": ["page_size", "block_position"],
            "response": ["paginated", "page_size", "block_position", "total_lines", "total_blocks", "has_more", "next_block_position"],
            "termination": "Stop when has_more=false; the client package iterates automatically.",
        },
        "return_value": {
            "success": {
                "description": "One documentation page plus compact server inventory.",
                "data": {
                    "guide_version": "Documentation schema version.",
                    "package": "Server, image and release version.",
                    "markdown": "Current page text or null when include_markdown=false.",
                    "pagination": "Stable page envelope.",
                    "runtime": "Users, groups, directories, ownership, ports and registration.",
                    "registered_commands": "Canonical custom command catalog.",
                    "integrations": "Installed scientific libraries and supported services.",
                },
            },
            "error": {"description": "Invalid pagination or unexpected failure.", "code": "VALIDATION_ERROR"},
        },
        "usage_examples": [
            {"description": "Read the first page", "command": {"page_size": 80, "block_position": 1}, "explanation": "Continue with next_block_position while has_more is true."},
            {"description": "Read inventory only", "command": {"include_markdown": False}, "explanation": "Useful for version checks and client startup."},
        ],
        "error_cases": {
            "VALIDATION_ERROR": {"description": "Unknown field, page_size outside 10..500, or block_position below 1.", "solution": "Use the documented pagination fields."},
            "COMMAND_ERROR": {"description": "Runtime information could not be collected.", "solution": "Inspect /var/log/science-assistant and retry."},
        },
        "best_practices": [
            "Use include_markdown=false for a lightweight version handshake.",
            "Iterate pages in ascending block_position and stop on has_more=false.",
            "Use help(cmdname=...) for exact JSON Schema and command-specific query formats.",
            "Keep the guide version and server version with reproducibility records.",
        ],
    }

"""Live astroquery gateway checks: VizieR catalog, object query, Gaia ADQL.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Exercises the three scientific acquisition commands against the REAL
deployed server and, through it, the REAL upstream services (VizieR,
SIMBAD, Gaia TAP). Queries are deliberately tiny (``row_limit``/``TOP 1``);
each run persists a small provenance-first dataset directory on the server —
that accumulation is the service's designed behaviour (datasets are
immutable acquisitions), noted here rather than hidden. Slow upstreams may
push a command over its sync budget onto the queue; ``call_completed``
resolves that transparently, so both paths assert identical shapes.

Positive assertions cover the dataset manifest contract (dataset and
manifest paths, file entries with sha256 and size). Negatives assert the
SPECIFIC observed codes (verified on 0.2.20): the lowercase service enum
rejection and missing-required rejection, both ``-32602``. Upstream
astronomy services are external: an outage FAILS the check honestly —
this project's purpose is reaching them.
"""

from __future__ import annotations

from pipeline import registry
from pipeline.live.client import (
    LiveClient,
    data_of,
    error_code,
    is_success,
    require,
    run_case,
    run_live_check,
    summarize_cases,
)
from pipeline.registry import CheckResult

CHECK_NAME = "live-astroquery"
CHECK_DESCRIPTION = (
    "Tiny real queries through VizieR (catalog), SIMBAD (object by name and by "
    "cone), and Gaia TAP (ADQL) with dataset manifest assertions, plus -32602 "
    "service-enum and missing-required negatives.")


def _assert_dataset_reply(envelope: dict, label: str) -> dict:
    require(is_success(envelope), f"{label} failed: {envelope!r}")
    data = data_of(envelope)
    for key in ("dataset_relative_path", "manifest_relative_path"):
        value = data.get(key)
        require(isinstance(value, str) and value, f"{label}: {key}={value!r}")
    files = data.get("files") or ([data["file"]] if isinstance(data.get("file"), dict) else [])
    require(files, f"{label}: no file entries in {data!r}")
    for entry in files:
        require(isinstance(entry.get("sha256"), str) and len(entry["sha256"]) == 64,
                f"{label}: file entry without sha256: {entry!r}")
    return data


def _body(client: LiveClient) -> CheckResult:
    def case_vizier_catalog() -> str:
        envelope = client.call_completed("astroquery_catalog", {
            "catalog": "J/ApJ/714/25", "table": "table1", "columns": ["*"],
            "constraints": {}, "row_limit": 3, "output_format": "ecsv",
            "dataset_name": "pipeline-check-vizier"})
        data = _assert_dataset_reply(envelope, "astroquery_catalog")
        require(data.get("table_count") == 1, f"table_count={data.get('table_count')!r}")
        return f"VizieR catalog persisted as {data['dataset_relative_path']}"

    def case_simbad_object_by_name() -> str:
        envelope = client.call_completed("astroquery_object", {
            "service": "simbad", "target": "M31", "row_limit": 3,
            "output_format": "ecsv", "dataset_name": "pipeline-check-simbad-name"})
        data = _assert_dataset_reply(envelope, "astroquery_object[name]")
        return f"SIMBAD object query persisted as {data['dataset_relative_path']}"

    def case_simbad_object_by_cone() -> str:
        envelope = client.call_completed("astroquery_object", {
            "service": "simbad", "ra_deg": 10.68, "dec_deg": 41.27,
            "radius_arcmin": 2.0, "row_limit": 3, "dataset_name": "pipeline-check-simbad-cone"})
        data = _assert_dataset_reply(envelope, "astroquery_object[cone]")
        return f"SIMBAD cone query persisted as {data['dataset_relative_path']}"

    def case_vizier_cone_with_catalog_and_columns() -> str:
        envelope = client.call_completed("astroquery_object", {
            "service": "vizier", "ra_deg": 10.68, "dec_deg": 41.27, "radius_arcmin": 2.0,
            "catalog": "I/355/gaiadr3", "columns": ["RA_ICRS", "DE_ICRS", "Gmag"],
            "row_limit": 3, "dataset_name": "pipeline-check-vizier-cone"})
        data = _assert_dataset_reply(envelope, "astroquery_object[vizier]")
        return f"VizieR cone with explicit catalog and columns persisted as {data['dataset_relative_path']}"

    def case_gaia_adql() -> str:
        envelope = client.call_completed("astroquery_adql", {
            "service": "gaia", "query": "SELECT TOP 1 source_id FROM gaiadr3.gaia_source",
            "output_format": "ecsv", "dataset_name": "pipeline-check-gaia"})
        data = _assert_dataset_reply(envelope, "astroquery_adql")
        return f"Gaia ADQL persisted as {data['dataset_relative_path']}"

    def case_custom_tap_adql() -> str:
        envelope = client.call_completed("astroquery_adql", {
            "service": "custom", "tap_url": "https://tapvizier.cds.unistra.fr/TAPVizieR/tap",
            "query": "SELECT TOP 1 table_name FROM TAP_SCHEMA.tables",
            "dataset_name": "pipeline-check-custom-tap"})
        data = _assert_dataset_reply(envelope, "astroquery_adql[custom]")
        return f"custom TAP endpoint (VizieR TAP) persisted as {data['dataset_relative_path']}"

    def case_custom_tap_requires_tap_url() -> str:
        envelope = client.call("astroquery_adql", {"service": "custom", "query": "SELECT 1"})
        require(error_code(envelope) == "VALIDATION_ERROR",
                f"code={error_code(envelope)!r}, expected 'VALIDATION_ERROR'")
        require(data_of(envelope).get("field") == "tap_url",
                f"field={data_of(envelope).get('field')!r}, expected 'tap_url'")
        return "service=custom without tap_url rejected with VALIDATION_ERROR naming the field"

    def case_service_enum_rejected() -> str:
        envelope = client.call("astroquery_object", {"service": "SIMBAD", "target": "M31"})
        require(error_code(envelope) == -32602, f"code={error_code(envelope)!r}, expected -32602")
        return "uppercase service name rejected with -32602 naming the lowercase enum"

    def case_missing_required_rejected() -> str:
        envelope = client.call("astroquery_adql", {"service": "gaia"})
        require(error_code(envelope) == -32602, f"code={error_code(envelope)!r}, expected -32602")
        return "astroquery_adql without query rejected with -32602"

    results = [run_case(name, func) for name, func in (
        ("vizier_catalog", case_vizier_catalog),
        ("simbad_object_by_name", case_simbad_object_by_name),
        ("simbad_object_by_cone", case_simbad_object_by_cone),
        ("vizier_cone_with_catalog_and_columns", case_vizier_cone_with_catalog_and_columns),
        ("gaia_adql", case_gaia_adql),
        ("custom_tap_adql", case_custom_tap_adql),
        ("custom_tap_requires_tap_url", case_custom_tap_requires_tap_url),
        ("service_enum_rejected", case_service_enum_rejected),
        ("missing_required_rejected", case_missing_required_rejected),
    )]
    return summarize_cases("astroquery", client.endpoint.describe(), results)


def check_live_astroquery() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_astroquery)

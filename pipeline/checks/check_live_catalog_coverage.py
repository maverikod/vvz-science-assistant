"""Schema-driven catalog coverage report against the REAL deployed server.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

The packaging standard requires the live pipeline's coverage to be driven by
the server's OWN declared schema and to report what is DECLARED BUT
UNTESTED. This check walks the real ``help`` catalog, fetches every custom
command's declared schema, asserts the schemas are structurally usable, and
prints the coverage ledger: which commands the pipeline currently exercises
(kept in ``EXERCISED_COMMANDS``, maintained alongside the check files) and,
loudly and by name, every declared command and parameter no check exercises
yet. Structural defects fail the check; a coverage gap is REPORTED, not
hidden — closing each named gap means adding a check file and extending
``EXERCISED_COMMANDS``.
"""

from __future__ import annotations

from pipeline import registry
from pipeline.live.client import (
    LiveClient,
    LiveSchemaError,
    require,
    run_case,
    run_live_check,
    summarize_cases,
)
from pipeline.registry import CheckResult

CHECK_NAME = "live-catalog-coverage"
CHECK_DESCRIPTION = (
    "Fetch every custom command's declared schema from the REAL server's help "
    "catalog, assert the schemas are usable, and report DECLARED BUT UNTESTED "
    "commands and parameters by name.")

# The pipeline's current live-exercise ledger: command -> parameters the
# existing checks actually send. Extend this together with new check files.
EXERCISED_COMMANDS = {
    "health": set(),
    "info": {"include_markdown", "page_size", "block_position"},
    "help": {"cmdname"},
    "file_receive": {"part_index", "part_count", "filename", "data_base64_part",
                     "size_bytes", "sha256", "ttl_seconds"},
    "file_ls": {"page", "page_size", "name_pattern"},
    "file_get": {"file_id"},
    "file_delete": {"file_id"},
}


def _body(client: LiveClient) -> CheckResult:
    catalog = client.command_names()
    schemas, broken = {}, []
    for name in catalog:
        try:
            schemas[name] = client.command_schema(name)
        except LiveSchemaError as exc:
            broken.append(f"{name}: {exc}")
    custom = {name: schema for name, schema in schemas.items() if schema.command_type == "custom"}

    def case_catalog_is_populated() -> str:
        require(len(catalog) > 0, "help returned an empty catalog")
        require(len(custom) > 0, f"no custom commands among {len(catalog)} catalog entries")
        return f"catalog: {len(catalog)} commands, {len(custom)} custom"

    def case_every_schema_fetchable() -> str:
        require(not broken, f"{len(broken)} command(s) with unusable help schema: " + "; ".join(broken))
        return f"help(cmdname=...) yields a usable schema for all {len(catalog)} commands"

    def case_exercised_commands_still_exist() -> str:
        stale = [name for name in EXERCISED_COMMANDS if name not in schemas]
        require(not stale, f"exercise ledger names command(s) the server no longer declares: {stale}")
        return "every command in the exercise ledger is still declared by the server"

    def case_exercised_parameters_still_declared() -> str:
        unknown = []
        for name, sent in EXERCISED_COMMANDS.items():
            declared = set(schemas[name].parameters) if name in schemas else set()
            unknown.extend(f"{name}.{p}" for p in sorted(sent - declared) if declared)
        require(not unknown, f"exercise ledger sends undeclared parameter(s): {unknown}")
        return "every parameter the checks send is declared by the server schema"

    results = [run_case(name, func) for name, func in (
        ("catalog_is_populated", case_catalog_is_populated),
        ("every_schema_fetchable", case_every_schema_fetchable),
        ("exercised_commands_still_exist", case_exercised_commands_still_exist),
        ("exercised_parameters_still_declared", case_exercised_parameters_still_declared),
    )]

    ledger = []
    untested_commands = sorted(set(custom) - set(EXERCISED_COMMANDS))
    covered = len(custom) - len(untested_commands)
    ledger.append(f"coverage: {covered}/{len(custom)} custom commands exercised by the live pipeline")
    if untested_commands:
        ledger.append("DECLARED BUT UNTESTED commands: " + ", ".join(untested_commands))
    for name in sorted(set(custom) & set(EXERCISED_COMMANDS)):
        missing = sorted(set(custom[name].parameters) - EXERCISED_COMMANDS[name])
        if missing:
            ledger.append(f"DECLARED BUT UNTESTED parameters of {name}: " + ", ".join(missing))
    result = summarize_cases("catalog-coverage", client.endpoint.describe(), results,
                             extra_output=tuple(ledger))
    if result.passed:
        # The gap report must be visible on a PASSING run too: the CLI prints
        # captured output only for failures, so the ledger heads the message.
        return CheckResult.ok(message=f"{result.message}; {ledger[0]}"
                              + (f"; {len(untested_commands)} untested command(s) named in `pipeline live-catalog-coverage` output"
                                 if untested_commands else ""),
                              output=result.output)
    return result


def check_live_catalog_coverage() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_catalog_coverage)

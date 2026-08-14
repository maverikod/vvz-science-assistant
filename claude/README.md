# science-assistant Claude prompt package

`../CLAUDE.md` is the entrypoint. This directory contains the project-bound
Claude contract bundle.

Package version: `v1.6.19`

## Layout

- `modes.yaml`: mode router.
- `roles/common.yaml`, `roles/laws.yaml`, `roles/tooling.yaml`, `roles/orchestrator.yaml`: mandatory core read.
- `roles/*.yaml`: stage contracts.
- `ops/*.yaml`: lazily loaded operating cards.
- `VERSION`: bundle version marker.

## Project bindings

- Project: `science-assistant`
- Local checkout: `/home/vasilyvz/projects/tools/science-assistant`
- CAS project ID: `190d4d88-0555-4e98-a538-5b7a4cbbbebb`
- CAS server: `code-analysis-server-vvz`

## Notes

- This bundle is Claude-only.
- Codex prompt files remain outside this directory and are not modified by it.
- Relative bundle references resolve from `claude/`.

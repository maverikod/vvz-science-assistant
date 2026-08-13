# Scientific Provider Client Evaluation Methodology

## Purpose

Every scientific provider integration MUST begin with a documented client and transport evaluation. Provider implementation is forbidden until a provider-specific research file exists, has a dated decision record, and passes the gate in this document.

This methodology applies to official SDKs, popular third-party clients, and direct HTTP, TAP, FTP, or file-distribution interfaces. Its purpose is to preserve provenance, raw responses, observable progress, and resume capabilities instead of hiding them behind a convenient library.

## Required evidence sources

Use current sources and record the date on which each source was checked. Evidence MUST come from:

1. Official API documentation and service status pages.
2. The official organization repository, when one exists.
3. The package registry entry and release history for every candidate package.
4. The most popular maintained third-party client repositories relevant to the API.
5. Protocol documentation for the direct API, including HTTP, TAP, FTP, object storage, bulk archives, or asynchronous job endpoints.
6. Security advisories, release notes, issue trackers, and maintenance statements needed to evaluate operational risk.

Each provider research file MUST contain stable links to the exact pages used. A bare project homepage is insufficient when a release, protocol feature, or security property is being claimed.

## Candidate classes

Evaluate at least these candidate classes:

- `official_client`: a client explicitly published or endorsed by the service owner.
- `popular_client`: a maintained third-party client with meaningful adoption in the scientific community.
- `direct_api`: direct use of HTTP, TAP, FTP, object storage, archive download, or another documented protocol.
- `hybrid`: a deliberate combination, such as an official client for query construction and direct transport for observable downloads.

Do not assume that an official client is automatically preferred. Official status is one decision factor, not a waiver from transfer, provenance, security, or testability requirements.

## Mandatory candidate inventory

Create one row per candidate. Unknown values MUST be written as `unknown` and treated as unresolved risk rather than silently omitted.

| Field | Required evidence |
|---|---|
| Candidate class | `official_client`, `popular_client`, `direct_api`, or `hybrid` |
| Organization and ownership | Publisher, maintainer, service owner, and relationship to the API |
| Repository and package | Repository URL, package name, package registry URL |
| Latest release | Version, release date, and date checked |
| Supported Python | Declared Python versions and interpreter constraints |
| License | SPDX identifier or exact license name and compatibility conclusion |
| Official status | Exact evidence that the client is official, endorsed, community-maintained, or independent |
| Activity | Recent commits/releases, maintainer activity, issue response, deprecation notices |
| Issue health | Open critical issues, download corruption reports, authentication failures, stalled maintenance |
| API stability | Stable/public API, compatibility policy, semantic versioning, breaking-change history |
| Authentication | Supported authentication methods, token handling, credential storage, refresh behavior |
| Pagination | Page, offset, cursor, continuation token, and result-limit behavior |
| Asynchronous jobs | Job submission, polling, cancellation, expiry, and server-side result retention |
| Streaming | Whether bytes or rows can be consumed incrementally without buffering the complete result |
| `Content-Length` | Exposed, synthesized, hidden, or unavailable |
| `Range` / `Accept-Ranges` | Support for byte ranges and the behavior when the server ignores a range request |
| Offset control | Byte, row, page, cursor, or archive-member offset capabilities |
| Block-size control | Whether the caller can choose the transfer block size and its valid limits |
| Resume | Byte-range resume, cursor resume, checkpoint resume, client-managed resume, or unsupported |
| Progress callback | Bytes, rows, pages, jobs, or no progress callback |
| Retry behavior | Retryable errors, backoff, idempotency, retry budget, and caller override |
| Rate limiting | Headers, quotas, backoff instructions, and client handling |
| Checksums | Server checksum, manifest checksum, ETag, digest header, or none |
| Formats | Raw and normalized formats supported without information loss |
| Raw response access | Headers, status, body, job metadata, and original files available to the caller |
| Provenance access | Endpoint, query, request parameters, timestamps, versions, and identifiers preserved |
| Thread safety | Documented thread-safety or process-safety limits |
| Async safety | Native async, safe executor use, global state, event-loop restrictions |
| Dependencies and size | Direct and transitive dependencies, optional extras, import/runtime footprint |
| Security history | Relevant advisories, insecure defaults, credential leaks, unsafe deserialization history |
| Testability | Mocking seams, transport injection, deterministic fixtures, offline tests |

## Transfer capability inventory

Record capabilities independently from the selected implementation strategy. A client may be useful for queries while being unsuitable for transfers.

Required transfer fields:

- Progress support: `exact`, `indeterminate`, or `unsupported`.
- Resume support: `byte_range`, `cursor`, `client_managed`, or `unsupported`.
- Known total size or row count and the source of that value.
- `Content-Length` behavior for normal, compressed, redirected, and ranged responses.
- `Range` and `Accept-Ranges` behavior, including validation of `206 Partial Content` and `Content-Range`.
- Current offset and whether the caller may supply an offset.
- Requested block size and whether the caller may supply a block size.
- Checkpoint or resume-token format, lifetime, and invalidation rules.
- Partial-file handling and integrity validation before append.
- ETag, Last-Modified, checksum, or manifest validators used to prevent resuming against changed content.
- Retry semantics after a partial write.
- Whether redirects, decompression, caching, or automatic conversion hide raw byte accounting.

A declared capability MUST have either official documentation or a reproducible probe. Do not infer resume support merely because a client streams data.

## Provenance and observability gate

A client MUST NOT be selected when it prevents the provider from recording or exposing any required item below:

- Original endpoint and normalized request parameters.
- Raw response headers, status, service identifiers, and job metadata.
- Original downloaded bytes or source artifact before normalization.
- Exact progress inputs such as received bytes, total bytes, rows, pages, or jobs.
- Current and average speed, timestamps, last activity, and stall detection inputs.
- Resume token, byte offset, cursor, block size, attempt number, and validators.
- Checksums and the evidence used to verify integrity.

When a client hides these values and cannot be extended safely, choose `direct_api` or `hybrid`. Convenience wrappers may be used only above the common provider contract; they may not bypass its lifecycle, progress, provenance, or resume rules.

## Decision record

Each provider research file MUST contain a formal decision record using this template.

```yaml
decision_id: provider-client-<provider>-<YYYYMMDD>
provider: <provider name>
checked_at: <ISO-8601 date>
checked_by: <actor>
selected_strategy: official_client | popular_client | direct_api | hybrid
selected_components:
  - name: <package, repository, or protocol>
    version_or_revision: <version, commit, or protocol revision>
    role: <query, authentication, metadata, transfer, normalization>
arguments:
  - <evidence-backed reason>
risks:
  - risk: <risk>
    mitigation: <mitigation or explicit acceptance>
rejected_alternatives:
  - candidate: <candidate>
    reason: <evidence-backed rejection>
capabilities:
  progress: exact | indeterminate | unsupported
  resume: byte_range | cursor | client_managed | unsupported
  offset: supported | unsupported
  block_size: supported | unsupported
  raw_response: preserved | partial | unavailable
  provenance: preserved | partial | unavailable
review_due: <ISO-8601 date or triggering condition>
source_links:
  - <official documentation>
  - <repository>
  - <package registry>
```

The selected strategy MUST be one of `official_client`, `popular_client`, `direct_api`, or `hybrid`. The decision MUST explain rejected alternatives and unresolved risks. Empty statements such as “official is better” or “popular library” do not satisfy the gate.

## CERN-specific requirement

For every CERN-related provider, explicitly identify and evaluate the official CERN client, when one exists for the target service. Record:

- Which CERN organization or service publishes or endorses it.
- Whether it exposes raw HTTP responses and provenance.
- Whether downloads expose `Content-Length`, `Range`, offset, and block-size controls.
- Whether interrupted downloads support resume or checkpoint recovery.
- Whether progress callbacks report bytes or only high-level job state.
- Whether checksums, ETags, manifests, or other validators are available.
- Whether direct or hybrid transport is required to obtain safe download and resume behavior.

A CERN provider research file is incomplete until this assessment is present, even when the final decision rejects the official client.

## Provider implementation gate

Provider implementation may start only when all conditions are true:

1. A provider-specific file exists under `docs/research/providers/`.
2. All mandatory candidate classes were evaluated or explicitly marked not applicable with evidence.
3. Every inventory field is filled, including `unknown` where evidence is unavailable.
4. Transfer capabilities are explicitly declared, including unsupported capabilities.
5. The decision record is complete and dated.
6. Provenance, raw response, progress, and resume behavior are compatible with the common provider contract.
7. CERN-specific assessment is complete for CERN-related providers.
8. Source links are sufficient for an independent reviewer to reproduce the decision.

The provider registry and release verification SHOULD reject an implementation whose research file or decision digest is missing.

## Re-evaluation policy

Repeat the evaluation when any of the following occurs:

- The research is older than 180 days at implementation or release time.
- A selected client has a new major release or documented breaking change.
- The upstream API changes authentication, pagination, asynchronous jobs, formats, or download transport.
- A security advisory affects the selected package or a transitive dependency.
- The package becomes unmaintained, archived, deprecated, or incompatible with supported Python versions.
- Production evidence contradicts declared progress, resume, checksum, or provenance capabilities.
- A previously rejected official or popular client gains capabilities that could simplify the integration without losing observability.

Update `checked_at`, the decision ID or revision, source links, changed evidence, and the decision digest. Do not silently reuse a stale decision.

## Provider research file skeleton

```markdown
# <Provider> client evaluation

- Checked at:
- Checked by:
- Target APIs and datasets:

## Official sources

## Candidate inventory

## Transfer capability probes

## Security and maintenance findings

## Decision record

## Rejected alternatives

## Risks and mitigations

## Re-evaluation trigger
```

The finished research file is an auditable engineering input, not background reading. Every later implementation, review, and release check must be traceable to its decision record.

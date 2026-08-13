"""Live transport and schema helpers for pipeline checks.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Foundation for every check that talks to the REAL deployed science-assistant
server over mTLS; nothing here mocks, stubs, or replays. The default target
is the deployed server at ``https://192.168.254.26:18180``, reached with the
mTLS material under the repository's ``runtime/certs/`` (fetched from the
deploy host's ``/etc/science-assistant/mtls``). Everything is overridable
through ``SCIENCE_ASSISTANT_LIVE_{HOST,PORT,PROTOCOL,CERT,KEY,CA_CERT,TIMEOUT}``.

``LiveClient.call`` returns the JSON-RPC envelope verbatim: a rejection is a
RESULT, not an exception. Observed error shapes on this server (verified on
0.2.19 / adapter 8.10.25) differ by path and :func:`error_of` normalizes both:

* validation errors: ``result.error`` is an OBJECT, e.g.
  ``{"code": -32602, "message": "...", "data": {...}}``;
* domain errors: ``result.error`` is a STRING code, e.g. ``"FILE_ERROR"``,
  with the human text in ``result.data.message``.

``call`` raises :class:`LiveServerUnavailable` only when the server cannot be
reached, and :func:`run_live_check` turns that into a RED check — an
unreachable deployment is our own service failing, never a skip.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from pipeline.registry import CheckResult

ENV_PREFIX = "SCIENCE_ASSISTANT_LIVE_"
DEFAULT_HOST, DEFAULT_PORT = "192.168.254.26", 18180
DEFAULT_PROTOCOL, DEFAULT_TIMEOUT = "https", 60.0


def repository_root() -> Path:
    """The checkout owning this module: nearest ancestor holding pyproject.toml."""
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return here.parents[2]


_CERTS = repository_root() / "runtime" / "certs"
DEFAULT_CERT, DEFAULT_KEY = str(_CERTS / "client.crt"), str(_CERTS / "client.key")
DEFAULT_CA_CERT = str(_CERTS / "server.crt")


class LiveServerUnavailable(RuntimeError):
    """The deployed server could not be reached: a RED check, never a pass."""


class LiveSchemaError(RuntimeError):
    """The server answered, but its ``help`` reply was not a usable schema."""


@dataclasses.dataclass(frozen=True)
class LiveEndpoint:
    """Everything needed to open one mTLS JSON-RPC connection."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    protocol: str = DEFAULT_PROTOCOL
    cert: str = DEFAULT_CERT
    key: str = DEFAULT_KEY
    ca_cert: str = DEFAULT_CA_CERT
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "LiveEndpoint":
        """Read ``SCIENCE_ASSISTANT_LIVE_*`` overrides from the environment."""
        get = (os.environ if env is None else env).get
        raw = (get(ENV_PREFIX + "PORT", str(DEFAULT_PORT)), get(ENV_PREFIX + "TIMEOUT", str(DEFAULT_TIMEOUT)))
        try:
            port, timeout = int(raw[0]), float(raw[1])
        except ValueError as exc:
            raise LiveServerUnavailable(f"invalid {ENV_PREFIX}PORT/{ENV_PREFIX}TIMEOUT {raw!r}: {exc}") from exc
        return cls(
            get(ENV_PREFIX + "HOST", DEFAULT_HOST), port,
            get(ENV_PREFIX + "PROTOCOL", DEFAULT_PROTOCOL), get(ENV_PREFIX + "CERT", DEFAULT_CERT),
            get(ENV_PREFIX + "KEY", DEFAULT_KEY), get(ENV_PREFIX + "CA_CERT", DEFAULT_CA_CERT), timeout,
        )

    def describe(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"


def make_client(endpoint: Optional[LiveEndpoint] = None) -> Any:
    """Build the real ``JsonRpcClient``; missing adapter or certificate files
    raise :class:`LiveServerUnavailable`, which fails the calling check."""
    endpoint = endpoint if endpoint is not None else LiveEndpoint.from_env()
    try:
        from mcp_proxy_adapter.client.jsonrpc_client.client import JsonRpcClient
    except ImportError as exc:
        raise LiveServerUnavailable(f"mcp_proxy_adapter is not importable here ({exc})") from exc
    for label, path in (("client certificate", endpoint.cert), ("client key", endpoint.key),
                        ("CA certificate", endpoint.ca_cert)):
        if not os.path.isfile(path):
            raise LiveServerUnavailable(f"{label} for {endpoint.describe()} not found at {path}")
    return JsonRpcClient(host=endpoint.host, port=endpoint.port, protocol=endpoint.protocol,
                         check_hostname=False, timeout=endpoint.timeout, cert=endpoint.cert,
                         key=endpoint.key, ca=endpoint.ca_cert)


def result_of(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    """The envelope's ``result`` object, or ``{}``."""
    value = envelope.get("result")
    return dict(value) if isinstance(value, Mapping) else {}


def data_of(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    """``result.data``, or ``{}`` when absent or not an object."""
    value = result_of(envelope).get("data")
    return dict(value) if isinstance(value, Mapping) else {}


def is_success(envelope: Mapping[str, Any]) -> bool:
    """True only when the envelope carries ``result.success is True``."""
    return result_of(envelope).get("success") is True


def error_of(envelope: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The error normalized to an object with at least a ``code`` key.

    A string ``result.error`` (the domain-error shape, e.g. ``"FILE_ERROR"``)
    becomes ``{"code": <string>, "message": result.data.message}``.
    """
    for candidate in (result_of(envelope).get("error"), envelope.get("error"),
                      envelope.get("transport_error")):
        if isinstance(candidate, Mapping):
            return dict(candidate)
        if isinstance(candidate, str) and candidate:
            return {"code": candidate, "message": data_of(envelope).get("message")}
    return None


def error_code(envelope: Mapping[str, Any]) -> Any:
    """The error ``code`` — stable string or numeric JSON-RPC code — or None."""
    return (error_of(envelope) or {}).get("code")


class LiveClient:
    """Synchronous facade over the real asynchronous ``JsonRpcClient``.

    One private event loop per instance, reused for every call: the underlying
    ``httpx.AsyncClient`` is cached on the transport, bound to its creating loop.
    """

    def __init__(self, endpoint: Optional[LiveEndpoint] = None) -> None:
        self.endpoint = endpoint if endpoint is not None else LiveEndpoint.from_env()
        self.jsonrpc_client = make_client(self.endpoint)
        self._loop = asyncio.new_event_loop()
        self._schemas: Dict[str, "CommandSchema"] = {}

    def run(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    def call(self, command: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Envelope verbatim: a rejection is a RESULT, not an exception."""
        return self.run(self._call(command, dict(params or {})))

    async def _call(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        import httpx
        try:
            envelope = await self.jsonrpc_client.jsonrpc_call(command, params)
        except httpx.HTTPStatusError as exc:
            try:
                body = exc.response.json()
            except ValueError:
                body = None
            if isinstance(body, Mapping):
                return dict(body)
            return {"transport_error": {"http_status": exc.response.status_code,
                                        "body_text": exc.response.text, "command": command}}
        except (httpx.HTTPError, OSError) as exc:
            raise LiveServerUnavailable(
                f"cannot reach {self.endpoint.describe()}: {type(exc).__name__}: {exc}") from exc
        return dict(envelope) if isinstance(envelope, Mapping) else {"result": envelope}

    def call_completed(self, command: str, params: Optional[Mapping[str, Any]] = None,
                       *, poll_timeout: float = 120.0, poll_interval: float = 2.0) -> Dict[str, Any]:
        """Call a command and resolve queue indirection to the FINAL envelope.

        Queue-backed commands (``x-use-queue``, or any command whose sync
        budget is exceeded) reply with ``{"job_id", "poll_with"}`` instead of a
        result. This helper polls ``queue_get_job_status`` until the job
        completes and returns the INNER command envelope, so callers assert
        the same shapes for the sync and the queued path. A job that does not
        finish within ``poll_timeout`` raises :class:`LiveServerUnavailable`
        (a RED check), because a stuck queue on our own server is a defect.
        """
        import time
        envelope = self.call(command, params)
        job_id = envelope.get("result", {}).get("job_id") if isinstance(envelope.get("result"), Mapping) else None
        if not job_id:
            return envelope
        deadline = time.monotonic() + poll_timeout
        while time.monotonic() < deadline:
            status_envelope = self.call("queue_get_job_status", {"job_id": job_id})
            data = data_of(status_envelope)
            if data.get("status") in ("completed", "failed", "error"):
                inner = data.get("result", {})
                inner = inner.get("result") if isinstance(inner, Mapping) else None
                if isinstance(inner, Mapping):
                    return {"result": dict(inner), "job_id": job_id}
                return {"result": {"success": False, "error": {"code": "QUEUE_RESULT_MISSING",
                        "message": f"job {job_id} finished without an inner result"}}, "job_id": job_id}
            time.sleep(poll_interval)
        raise LiveServerUnavailable(f"queued {command} job {job_id} did not finish within {poll_timeout}s")

    def command_names(self) -> List[str]:
        """Every command name the deployed server advertises through ``help``."""
        commands = data_of(self.call("help", {})).get("commands")
        if not isinstance(commands, Mapping):
            raise LiveSchemaError("help returned no command catalog")
        return sorted(str(name) for name in commands)

    def command_schema(self, command: str) -> "CommandSchema":
        """Fetch (and cache) a command's declared schema via ``help(cmdname=...)``."""
        if command not in self._schemas:
            self._schemas[command] = parse_command_schema(command, self.call("help", {"cmdname": command}))
        return self._schemas[command]

    def close(self) -> None:
        try:  # teardown must never mask a verdict
            self._loop.run_until_complete(self.jsonrpc_client.close())
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._loop.close()


@dataclasses.dataclass(frozen=True)
class ParameterSpec:
    """One declared parameter, exactly as the server's JSON Schema describes it."""

    name: str
    type: str
    required: bool
    description: str = ""


@dataclasses.dataclass(frozen=True)
class CommandSchema:
    """A command's declared surface from ``help(cmdname=...)``.

    On this server the reply carries ``data.metadata`` (name, summary, type,
    category, version) and ``data.schema`` — a JSON Schema object with
    ``properties``, ``required``, and ``additionalProperties: false``.
    """

    name: str
    summary: str
    command_type: str
    category: str
    parameters: Mapping[str, ParameterSpec]
    metadata: Mapping[str, Any]

    def required_parameters(self) -> List[str]:
        return [n for n, p in self.parameters.items() if p.required]

    def format_declared_surface(self) -> str:
        lines = [f"{self.name}: {len(self.parameters)} declared parameter(s), "
                 f"type={self.command_type or '?'}, category={self.category or '?'}"]
        for name, spec in self.parameters.items():
            lines.append(f"  param {name}  type={spec.type}  "
                         + ("required" if spec.required else "optional"))
        return "\n".join(lines)


def parse_command_schema(command: str, envelope: Mapping[str, Any]) -> CommandSchema:
    """Turn a ``help(cmdname=...)`` envelope into a :class:`CommandSchema`."""
    if not is_success(envelope):
        raise LiveSchemaError(f"help(cmdname={command!r}) did not succeed: {error_of(envelope)!r}")
    data = data_of(envelope)
    metadata = data.get("metadata")
    if not isinstance(metadata, Mapping):
        raise LiveSchemaError(f"help(cmdname={command!r}) carried no metadata object")
    schema = data.get("schema")
    schema = schema if isinstance(schema, Mapping) else {}
    properties = schema.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    required = schema.get("required")
    required = set(required) if isinstance(required, (list, tuple)) else set()
    parameters = {}
    for name, spec in properties.items():
        spec = spec if isinstance(spec, Mapping) else {}
        parameters[str(name)] = ParameterSpec(
            name=str(name), type=str(spec.get("type", "")), required=str(name) in required,
            description=str(spec.get("description", "")))
    return CommandSchema(
        name=str(metadata.get("name", command)), summary=str(metadata.get("summary", "")),
        command_type=str(metadata.get("type", "")), category=str(metadata.get("category", "")),
        parameters=parameters, metadata=dict(metadata))


def run_live_check(body: Callable[[LiveClient], CheckResult],
                   endpoint: Optional[LiveEndpoint] = None) -> CheckResult:
    """Open a live client, run ``body``, always close it. An unreachable server —
    no adapter, no certificate, refused, TLS error, timeout — FAILS the check
    with the reason: the deployment is ours, so silence is a defect to fix."""
    client = None
    try:
        client = LiveClient(endpoint)
        return body(client)
    except LiveServerUnavailable as exc:
        return CheckResult.fail(message=f"live server unreachable: {exc}")
    finally:
        if client is not None:
            client.close()


@dataclasses.dataclass(frozen=True)
class CaseResult:
    """One named case inside a live check."""

    name: str
    passed: bool
    detail: str = ""

    def format(self) -> str:
        head = f"[{'PASS' if self.passed else 'FAIL'}] {self.name}"
        return f"{head} - {self.detail}" if self.detail else head


def run_case(name: str, func: Callable[[], str]) -> CaseResult:
    """Run one case; assertion text or the traceback becomes its detail."""
    import traceback
    try:
        return CaseResult(name, True, func() or "ok")
    except AssertionError as exc:
        return CaseResult(name, False, f"assertion failed: {exc}")
    except Exception:  # noqa: BLE001 - any real failure is this case's failure
        return CaseResult(name, False, f"unexpected exception:\n{traceback.format_exc()}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def summarize_cases(check_label: str, endpoint: str,
                    results: List[CaseResult], extra_output: Tuple[str, ...] = ()) -> CheckResult:
    """Fold case results into one CheckResult with per-case lines as output."""
    output_lines = [r.format() for r in results]
    output_lines.extend(extra_output)
    failed = [r.name for r in results if not r.passed]
    body_text = "\n".join(output_lines)
    if failed:
        return CheckResult.fail(
            message=f"{len(failed)}/{len(results)} {check_label} case(s) failed: " + ", ".join(failed),
            output=body_text)
    return CheckResult.ok(
        message=f"{len(results)}/{len(results)} {check_label} case(s) passed against {endpoint}",
        output=body_text)

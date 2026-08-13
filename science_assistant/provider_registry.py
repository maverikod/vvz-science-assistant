"""Thread-safe admission registry and deterministic provider discovery."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import pkgutil
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import RLock
from types import MappingProxyType, ModuleType
from typing import Any, Final, Never

from science_assistant.provider_contract import (  # type: ignore[import-not-found]
    BaseFileDatasetProvider,
    BaseHttpProvider,
    BaseLibraryProvider,
    BaseScientificProvider,
    BaseTapProvider,
    ProviderCommandSpec,
    ProviderDescriptor,
    ProviderUnsupportedError,
)

_DECISION_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_LINK_RE: Final[re.Pattern[str]] = re.compile(r"https?://[^\s)>\]}]+")
_REGISTRY_MODES: Final[frozenset[str]] = frozenset({"source", "runtime"})


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    """Stable provider admission or discovery diagnostic.

    Attributes:
        code: Stable machine-readable diagnostic code.
        message: Human-readable explanation.
        blocking: Whether the diagnostic prevents registration.
        provider: Optional provider name.
        module: Optional module name.
        exception_type: Optional isolated exception class name.
    """

    code: str
    message: str
    blocking: bool = True
    provider: str | None = None
    module: str | None = None
    exception_type: str | None = None

    def __post_init__(self) -> None:
        """Validate required diagnostic text.

        Returns:
            None.
        """
        if not self.code.strip():
            raise ValueError("diagnostic code must not be empty")
        if not self.message.strip():
            raise ValueError("diagnostic message must not be empty")

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible diagnostic document.

        Returns:
            Stable diagnostic fields.
        """
        return {
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
            "provider": self.provider,
            "module": self.module,
            "exception_type": self.exception_type,
        }


@dataclass(frozen=True, slots=True)
class ProviderDecisionMetadata:
    """Immutable client decision admitted with a provider.

    Attributes:
        decision_id: Stable decision identifier.
        decision_date: Date on which the decision was verified.
        decision_digest: SHA-256 of the exact source decision file.
        decision_record: Project-relative decision record path.
        client_strategy: official_client, popular_client, direct_api, or hybrid.
        client_name: Selected client or direct protocol name.
        client_version: Selected client version or protocol revision.
        repository_url: Repository or official implementation URL.
        license: Client or implementation license.
        official_status: Evidence-backed official status label.
    """

    decision_id: str
    decision_date: date
    decision_digest: str
    decision_record: str
    client_strategy: str
    client_name: str
    client_version: str
    repository_url: str
    license: str
    official_status: str

    def __post_init__(self) -> None:
        """Normalize and validate immutable decision metadata.

        Returns:
            None.
        """
        for field_name in (
            "decision_id",
            "decision_record",
            "client_strategy",
            "client_name",
            "client_version",
            "repository_url",
            "license",
            "official_status",
        ):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value)
        digest = self.decision_digest.strip().lower()
        if not _DECISION_DIGEST_RE.fullmatch(digest):
            raise ValueError("decision_digest must be a lowercase SHA-256 digest")
        object.__setattr__(self, "decision_digest", digest)
        object.__setattr__(self, "client_strategy", self.client_strategy.lower())
        object.__setattr__(
            self, "decision_record", Path(self.decision_record).as_posix()
        )

    @classmethod
    def from_value(
        cls,
        value: ProviderDecisionMetadata | Mapping[str, Any],
    ) -> ProviderDecisionMetadata:
        """Normalize a decision object or mapping.

        Args:
            value: Existing metadata or mapping with required fields.

        Returns:
            Immutable normalized decision metadata.
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("provider decision must be a mapping or metadata instance")
        raw_date = value.get("decision_date")
        if isinstance(raw_date, date):
            parsed_date = raw_date
        elif isinstance(raw_date, str):
            try:
                parsed_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise ValueError("decision_date must use ISO YYYY-MM-DD") from exc
        else:
            raise TypeError("decision_date must be a date or ISO string")
        return cls(
            decision_id=str(value.get("decision_id", "")),
            decision_date=parsed_date,
            decision_digest=str(value.get("decision_digest", "")),
            decision_record=str(value.get("decision_record", "")),
            client_strategy=str(value.get("client_strategy", "")),
            client_name=str(value.get("client_name", "")),
            client_version=str(value.get("client_version", "")),
            repository_url=str(value.get("repository_url", "")),
            license=str(value.get("license", "")),
            official_status=str(value.get("official_status", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible decision record.

        Returns:
            Immutable decision metadata as scalar JSON values.
        """
        return {
            "decision_id": self.decision_id,
            "decision_date": self.decision_date.isoformat(),
            "decision_digest": self.decision_digest,
            "decision_record": self.decision_record,
            "client_strategy": self.client_strategy,
            "client_name": self.client_name,
            "client_version": self.client_version,
            "repository_url": self.repository_url,
            "license": self.license,
            "official_status": self.official_status,
        }


class ProviderAdmissionError(ValueError):
    """Registration failure carrying one stable blocking diagnostic.

    Attributes:
        diagnostic: Blocking diagnostic that caused admission to fail.
    """

    def __init__(self, diagnostic: ProviderDiagnostic) -> None:
        """Initialize the admission error.

        Args:
            diagnostic: Stable blocking diagnostic.

        Returns:
            None.
        """
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class ProviderUnavailableError(RuntimeError):
    """Provider optional dependency or runtime environment is unavailable."""


@dataclass(frozen=True, slots=True)
class RegisteredProvider:
    """Immutable admitted provider envelope.

    Attributes:
        provider: Completed provider instance.
        decision: Verified client decision metadata.
        command_schemas: Closed schemas keyed by command name.
        module_name: Optional discovery module name.
    """

    provider: BaseScientificProvider
    decision: ProviderDecisionMetadata
    command_schemas: Mapping[str, Mapping[str, Any]]
    module_name: str | None

    def __post_init__(self) -> None:
        """Freeze nested schema mappings shallowly.

        Returns:
            None.
        """
        frozen = {
            name: MappingProxyType(dict(schema))
            for name, schema in self.command_schemas.items()
        }
        object.__setattr__(self, "command_schemas", MappingProxyType(frozen))


class ProviderRegistry:
    """Thread-safe registry enforcing provider and decision admission rules.

    Attributes:
        _mode: source preflight or installed runtime admission mode.
        _source_root: Project source root used only in source mode.
        _decision_catalog: Immutable packaged decision catalog.
        _providers: Admitted providers keyed by stable name.
        _diagnostics: Stable diagnostics accumulated during discovery.
        _discovered_modules: Module names already attempted by discovery.
        _lock: Re-entrant lock guarding registry state.
    """

    def __init__(
        self,
        *,
        mode: str = "runtime",
        source_root: str | Path | None = None,
        decision_catalog: (
            Mapping[
                str,
                ProviderDecisionMetadata | Mapping[str, Any],
            ]
            | None
        ) = None,
    ) -> None:
        """Initialize an empty registry without importing provider modules.

        Args:
            mode: source for file preflight or runtime for packaged catalog checks.
            source_root: Source-tree root required only in source mode.
            decision_catalog: Packaged immutable decisions keyed by provider name.

        Returns:
            None.
        """
        normalized_mode = mode.strip().lower()
        if normalized_mode not in _REGISTRY_MODES:
            raise ValueError(f"unsupported registry mode: {mode!r}")
        root = Path(source_root or ".").resolve()
        catalog: dict[str, ProviderDecisionMetadata] = {}
        for provider_name, value in (decision_catalog or {}).items():
            name = str(provider_name).strip()
            if not name:
                raise ValueError("decision catalog provider name must not be empty")
            catalog[name] = ProviderDecisionMetadata.from_value(value)
        self._mode = normalized_mode
        self._source_root = root
        self._decision_catalog = MappingProxyType(catalog)
        self._providers: dict[str, RegisteredProvider] = {}
        self._diagnostics: list[ProviderDiagnostic] = []
        self._discovered_modules: set[str] = set()
        self._lock = RLock()

    @property
    def mode(self) -> str:
        """Return the active admission mode.

        Returns:
            source or runtime.
        """
        return self._mode

    def register(
        self,
        provider: BaseScientificProvider,
        *,
        declared_provider_name: str | None = None,
        decision: ProviderDecisionMetadata | Mapping[str, Any] | None = None,
        command_schemas: Mapping[str, Mapping[str, Any]] | None = None,
        module_name: str | None = None,
    ) -> BaseScientificProvider:
        """Validate and register one completed provider instance.

        Args:
            provider: Completed BaseScientificProvider instance.
            declared_provider_name: Optional module PROVIDER_NAME value.
            decision: Optional module decision metadata.
            command_schemas: Closed JSON Schemas keyed by command name.
            module_name: Optional discovery module name.

        Returns:
            The admitted provider instance.
        """
        if not isinstance(provider, BaseScientificProvider):
            raise TypeError("provider must be a BaseScientificProvider instance")
        with self._lock:
            provider_name = self._validate_provider_object(
                provider,
                declared_provider_name=declared_provider_name,
                module_name=module_name,
            )
            if provider_name in self._providers:
                diagnostic = ProviderDiagnostic(
                    code="duplicate_provider_name",
                    message=f"provider {provider_name!r} is already registered",
                    provider=provider_name,
                    module=module_name,
                )
                self._reject(diagnostic)
            commands = self._validate_commands(provider, module_name=module_name)
            schemas = self._resolve_command_schemas(
                provider,
                commands,
                command_schemas,
                module_name=module_name,
            )
            admitted_decision = self._resolve_decision(
                provider,
                decision,
                module_name=module_name,
            )
            self._validate_descriptor_decision(
                provider.descriptor,
                admitted_decision,
                provider_name=provider_name,
                module_name=module_name,
            )
            self._validate_capabilities(provider, commands, module_name=module_name)
            self._validate_transport_base(provider, module_name=module_name)
            self._providers[provider_name] = RegisteredProvider(
                provider=provider,
                decision=admitted_decision,
                command_schemas=schemas,
                module_name=module_name,
            )
            return provider

    def get(self, provider_name: str) -> BaseScientificProvider:
        """Return one admitted provider by name.

        Args:
            provider_name: Stable provider name.

        Returns:
            Completed provider instance.
        """
        with self._lock:
            try:
                return self._providers[provider_name].provider
            except KeyError as exc:
                raise ProviderUnsupportedError(
                    f"unknown scientific provider: {provider_name}"
                ) from exc

    def get_registered(self, provider_name: str) -> RegisteredProvider:
        """Return the complete admitted provider envelope.

        Args:
            provider_name: Stable provider name.

        Returns:
            Provider, decision, schemas, and module metadata.
        """
        with self._lock:
            try:
                return self._providers[provider_name]
            except KeyError as exc:
                raise ProviderUnsupportedError(
                    f"unknown scientific provider: {provider_name}"
                ) from exc

    def names(self) -> tuple[str, ...]:
        """Return admitted provider names in deterministic order.

        Returns:
            Sorted provider-name tuple.
        """
        with self._lock:
            return tuple(sorted(self._providers))

    def diagnostics(self) -> tuple[ProviderDiagnostic, ...]:
        """Return immutable accumulated diagnostics.

        Returns:
            Diagnostics in discovery order.
        """
        with self._lock:
            return tuple(self._diagnostics)

    def clear_diagnostics(self) -> None:
        """Clear accumulated diagnostics without changing providers.

        Returns:
            None.
        """
        with self._lock:
            self._diagnostics.clear()

    def _validate_provider_object(
        self,
        provider: BaseScientificProvider,
        *,
        declared_provider_name: str | None,
        module_name: str | None,
    ) -> str:
        """Validate provider completion, identity, and lifecycle ownership.

        Args:
            provider: Provider instance to inspect.
            declared_provider_name: Optional module constant.
            module_name: Optional discovery module name.

        Returns:
            Stable provider name.
        """
        provider_type = type(provider)
        if inspect.isabstract(provider_type):
            self._reject(
                ProviderDiagnostic(
                    code="abstract_provider",
                    message=f"provider class {provider_type.__name__} is abstract",
                    module=module_name,
                )
            )
        if provider_type.execute is not BaseScientificProvider.execute:
            self._reject(
                ProviderDiagnostic(
                    code="execute_override_forbidden",
                    message=(
                        "provider classes must not override the common "
                        "execute lifecycle"
                    ),
                    module=module_name,
                )
            )
        provider_name = str(provider.name).strip()
        if not provider_name:
            self._reject(
                ProviderDiagnostic(
                    code="empty_provider_name",
                    message="provider name must not be empty",
                    module=module_name,
                )
            )
        if not isinstance(provider.descriptor, ProviderDescriptor):
            self._reject(
                ProviderDiagnostic(
                    code="invalid_provider_descriptor",
                    message="descriptor must be a ProviderDescriptor",
                    provider=provider_name,
                    module=module_name,
                )
            )
        if provider.descriptor.name != provider_name:
            self._reject(
                ProviderDiagnostic(
                    code="descriptor_name_mismatch",
                    message="descriptor.name does not match provider.name",
                    provider=provider_name,
                    module=module_name,
                )
            )
        if not provider.descriptor.description.strip():
            self._reject(
                ProviderDiagnostic(
                    code="empty_provider_descriptor",
                    message="provider descriptor description must not be empty",
                    provider=provider_name,
                    module=module_name,
                )
            )
        if declared_provider_name is not None:
            declared = declared_provider_name.strip()
            if not declared or declared != provider_name:
                self._reject(
                    ProviderDiagnostic(
                        code="provider_name_constant_mismatch",
                        message=(
                            "module PROVIDER_NAME does not match " "provider.name"
                        ),
                        provider=provider_name,
                        module=module_name,
                    )
                )
        return provider_name

    def _validate_commands(
        self,
        provider: BaseScientificProvider,
        *,
        module_name: str | None,
    ) -> tuple[ProviderCommandSpec, ...]:
        """Validate immutable non-empty unique command declarations.

        Args:
            provider: Admitted provider candidate.
            module_name: Optional discovery module name.

        Returns:
            Validated command tuple.
        """
        commands = tuple(provider.commands())
        if not commands:
            self._reject(
                ProviderDiagnostic(
                    code="empty_provider_commands",
                    message="provider must declare at least one command",
                    provider=provider.name,
                    module=module_name,
                )
            )
        if any(not isinstance(command, ProviderCommandSpec) for command in commands):
            self._reject(
                ProviderDiagnostic(
                    code="invalid_provider_command",
                    message="all commands must be ProviderCommandSpec instances",
                    provider=provider.name,
                    module=module_name,
                )
            )
        names = [command.name for command in commands]
        if len(names) != len(set(names)):
            self._reject(
                ProviderDiagnostic(
                    code="duplicate_provider_command",
                    message="provider command names must be unique",
                    provider=provider.name,
                    module=module_name,
                )
            )
        return commands

    def _resolve_command_schemas(
        self,
        provider: BaseScientificProvider,
        commands: Sequence[ProviderCommandSpec],
        supplied: Mapping[str, Mapping[str, Any]] | None,
        *,
        module_name: str | None,
    ) -> Mapping[str, Mapping[str, Any]]:
        """Resolve and validate closed JSON Schemas for every command.

        Args:
            provider: Provider candidate.
            commands: Validated command declarations.
            supplied: Optional module COMMAND_SCHEMAS mapping.
            module_name: Optional discovery module name.

        Returns:
            Validated schemas keyed by command name.
        """
        schemas: Any = supplied
        if schemas is None:
            provider_schemas = getattr(provider, "command_schemas", None)
            if callable(provider_schemas):
                schemas = provider_schemas()
        if not isinstance(schemas, Mapping):
            self._reject(
                ProviderDiagnostic(
                    code="missing_command_schemas",
                    message="provider module must supply COMMAND_SCHEMAS",
                    provider=provider.name,
                    module=module_name,
                )
            )
        command_names = {command.name for command in commands}
        schema_names = {str(name) for name in schemas}
        if schema_names != command_names:
            self._reject(
                ProviderDiagnostic(
                    code="command_schema_set_mismatch",
                    message="COMMAND_SCHEMAS keys must exactly match provider commands",
                    provider=provider.name,
                    module=module_name,
                )
            )
        validated: dict[str, Mapping[str, Any]] = {}
        for command_name in sorted(command_names):
            schema = schemas[command_name]
            if not isinstance(schema, Mapping):
                self._reject(
                    ProviderDiagnostic(
                        code="invalid_command_schema",
                        message=f"schema for {command_name!r} must be a mapping",
                        provider=provider.name,
                        module=module_name,
                    )
                )
            error = _closed_schema_error(schema, path=f"schema[{command_name!r}]")
            if error is not None:
                self._reject(
                    ProviderDiagnostic(
                        code="open_command_schema",
                        message=error,
                        provider=provider.name,
                        module=module_name,
                    )
                )
            validated[command_name] = dict(schema)
        return MappingProxyType(validated)

    def _resolve_decision(
        self,
        provider: BaseScientificProvider,
        supplied: ProviderDecisionMetadata | Mapping[str, Any] | None,
        *,
        module_name: str | None,
    ) -> ProviderDecisionMetadata:
        """Resolve source or packaged decision metadata and verify its digest.

        Args:
            provider: Provider candidate.
            supplied: Optional module decision metadata.
            module_name: Optional discovery module name.

        Returns:
            Verified immutable decision metadata.
        """
        provider_name = provider.name
        catalog_decision = self._decision_catalog.get(provider_name)
        supplied_decision = (
            ProviderDecisionMetadata.from_value(supplied)
            if supplied is not None
            else None
        )
        if self._mode == "runtime":
            if catalog_decision is None:
                self._reject(
                    ProviderDiagnostic(
                        code="missing_packaged_decision",
                        message=("runtime decision catalog has no provider entry"),
                        provider=provider_name,
                        module=module_name,
                    )
                )
            if supplied_decision is not None and supplied_decision != catalog_decision:
                self._reject(
                    ProviderDiagnostic(
                        code="packaged_decision_mismatch",
                        message=(
                            "module decision metadata differs from " "packaged catalog"
                        ),
                        provider=provider_name,
                        module=module_name,
                    )
                )
            return catalog_decision
        decision = supplied_decision or catalog_decision
        if decision is None:
            self._reject(
                ProviderDiagnostic(
                    code="missing_source_decision",
                    message=(
                        "source registration requires provider " "decision metadata"
                    ),
                    provider=provider_name,
                    module=module_name,
                )
            )
        self._verify_source_decision(
            provider,
            decision,
            module_name=module_name,
        )
        return decision

    def _verify_source_decision(
        self,
        provider: BaseScientificProvider,
        decision: ProviderDecisionMetadata,
        *,
        module_name: str | None,
    ) -> None:
        """Verify source-tree decision path, links, and exact file digest.

        Args:
            provider: Provider candidate.
            decision: Source decision metadata.
            module_name: Optional discovery module name.

        Returns:
            None.
        """
        relative = Path(decision.decision_record)
        expected_prefix = Path("docs/research/providers")
        if relative.is_absolute() or ".." in relative.parts:
            self._reject(
                ProviderDiagnostic(
                    code="invalid_decision_path",
                    message="decision_record must be a safe project-relative path",
                    provider=provider.name,
                    module=module_name,
                )
            )
        if (
            relative.suffix.lower() != ".md"
            or relative.parts[:3] != expected_prefix.parts
        ):
            self._reject(
                ProviderDiagnostic(
                    code="invalid_decision_location",
                    message=(
                        "decision_record must be under " "docs/research/providers/*.md"
                    ),
                    provider=provider.name,
                    module=module_name,
                )
            )
        path = (self._source_root / relative).resolve()
        if self._source_root != path and self._source_root not in path.parents:
            self._reject(
                ProviderDiagnostic(
                    code="decision_path_escape",
                    message="decision_record escapes the source root",
                    provider=provider.name,
                    module=module_name,
                )
            )
        if not path.is_file():
            self._reject(
                ProviderDiagnostic(
                    code="decision_file_missing",
                    message=f"decision file does not exist: {relative.as_posix()}",
                    provider=provider.name,
                    module=module_name,
                )
            )
        payload = path.read_bytes()
        text = payload.decode("utf-8")
        if _LINK_RE.search(text) is None:
            self._reject(
                ProviderDiagnostic(
                    code="decision_links_missing",
                    message="decision file must contain at least one source link",
                    provider=provider.name,
                    module=module_name,
                )
            )
        digest = hashlib.sha256(payload).hexdigest()
        if digest != decision.decision_digest:
            self._reject(
                ProviderDiagnostic(
                    code="decision_digest_mismatch",
                    message="decision file SHA-256 does not match decision metadata",
                    provider=provider.name,
                    module=module_name,
                )
            )

    def _validate_descriptor_decision(
        self,
        descriptor: ProviderDescriptor,
        decision: ProviderDecisionMetadata,
        *,
        provider_name: str,
        module_name: str | None,
    ) -> None:
        """Match immutable descriptor metadata to admitted decision metadata.

        Args:
            descriptor: Provider descriptor from the provider instance.
            decision: Verified decision metadata.
            provider_name: Stable provider name.
            module_name: Optional discovery module name.

        Returns:
            None.
        """
        expected = {
            "client_strategy": decision.client_strategy,
            "client_name": decision.client_name,
            "client_version": decision.client_version,
            "repository_url": decision.repository_url,
            "license": decision.license,
            "research_decision_ref": decision.decision_record,
        }
        for field_name, expected_value in expected.items():
            actual = getattr(descriptor, field_name, None)
            if not isinstance(actual, str) or actual.strip() != expected_value:
                self._reject(
                    ProviderDiagnostic(
                        code="descriptor_decision_mismatch",
                        message=(
                            f"descriptor {field_name} does not match "
                            "decision metadata"
                        ),
                        provider=provider_name,
                        module=module_name,
                    )
                )

    def _validate_capabilities(
        self,
        provider: BaseScientificProvider,
        commands: Sequence[ProviderCommandSpec],
        *,
        module_name: str | None,
    ) -> None:
        """Check derived progress, resume, offset, and block-size invariants.

        Args:
            provider: Provider candidate.
            commands: Validated command declarations.
            module_name: Optional discovery module name.

        Returns:
            None.
        """
        capabilities = [provider.descriptor.default_capabilities]
        capabilities.extend(command.transfer_capabilities for command in commands)
        for capability in capabilities:
            expected_percentage = capability.progress_support.value == "exact"
            if capability.supports_percentage != expected_percentage:
                self._reject(
                    ProviderDiagnostic(
                        code="progress_capability_mismatch",
                        message="supports_percentage conflicts with progress_support",
                        provider=provider.name,
                        module=module_name,
                    )
                )
            if (
                capability.resume_support.value == "byte_range"
                and not capability.supports_offset
            ):
                self._reject(
                    ProviderDiagnostic(
                        code="resume_offset_mismatch",
                        message="byte_range resume requires supports_offset",
                        provider=provider.name,
                        module=module_name,
                    )
                )
            if not capability.supports_block_size and any(
                value is not None
                for value in (
                    capability.min_block_size,
                    capability.max_block_size,
                    capability.default_block_size,
                )
            ):
                self._reject(
                    ProviderDiagnostic(
                        code="block_size_capability_mismatch",
                        message="block-size limits require supports_block_size",
                        provider=provider.name,
                        module=module_name,
                    )
                )

    def _validate_transport_base(
        self,
        provider: BaseScientificProvider,
        *,
        module_name: str | None,
    ) -> None:
        """Validate one transport base against the selected client strategy.

        Args:
            provider: Provider candidate.
            module_name: Optional discovery module name.

        Returns:
            None.
        """
        transport_types = (
            BaseHttpProvider,
            BaseTapProvider,
            BaseFileDatasetProvider,
            BaseLibraryProvider,
        )
        matched = [base for base in transport_types if isinstance(provider, base)]
        if len(matched) != 1:
            self._reject(
                ProviderDiagnostic(
                    code="transport_base_mismatch",
                    message=(
                        "provider must inherit exactly one supported " "transport base"
                    ),
                    provider=provider.name,
                    module=module_name,
                )
            )
        strategy = provider.descriptor.client_strategy
        if matched[0] is BaseLibraryProvider:
            allowed = {"official_client", "popular_client", "hybrid"}
        else:
            allowed = {"direct_api", "hybrid"}
        if strategy not in allowed:
            self._reject(
                ProviderDiagnostic(
                    code="transport_strategy_mismatch",
                    message="client_strategy is inconsistent with the transport base",
                    provider=provider.name,
                    module=module_name,
                )
            )

    def _reject(self, diagnostic: ProviderDiagnostic) -> Never:
        """Store and raise one blocking admission diagnostic.

        Args:
            diagnostic: Stable blocking diagnostic.

        Returns:
            Never; this method always raises ProviderAdmissionError.
        """
        self._diagnostics.append(diagnostic)
        raise ProviderAdmissionError(diagnostic)

    def _record_discovery_diagnostic(self, diagnostic: ProviderDiagnostic) -> None:
        """Store an isolated discovery diagnostic without raising.

        Args:
            diagnostic: Stable discovery diagnostic.

        Returns:
            None.
        """
        with self._lock:
            self._diagnostics.append(diagnostic)

    def _claim_module(self, module_name: str) -> bool:
        """Claim one module name for idempotent discovery.

        Args:
            module_name: Fully qualified module name.

        Returns:
            True only on the first discovery attempt.
        """
        with self._lock:
            if module_name in self._discovered_modules:
                return False
            self._discovered_modules.add(module_name)
            return True


def discover_providers(
    registry: ProviderRegistry,
    modules: str | Iterable[str],
) -> tuple[str, ...]:
    """Deterministically import and admit provider modules without network work.

    Args:
        registry: Destination ProviderRegistry.
        modules: Package name or iterable of fully qualified module names.

    Returns:
        Names of providers newly admitted by this call.
    """
    module_names = _resolve_module_names(modules)
    admitted: list[str] = []
    for module_name in module_names:
        if not registry._claim_module(module_name):
            continue
        try:
            module = importlib.import_module(module_name)
            provider_name = _required_module_text(module, "PROVIDER_NAME")
            factory = _provider_factory(module)
            provider = factory()
            decision = getattr(module, "PROVIDER_DECISION", None)
            command_schemas = getattr(module, "COMMAND_SCHEMAS", None)
            registry.register(
                provider,
                declared_provider_name=provider_name,
                decision=decision,
                command_schemas=command_schemas,
                module_name=module_name,
            )
            admitted.append(provider.name)
        except ImportError as exc:
            registry._record_discovery_diagnostic(
                ProviderDiagnostic(
                    code="provider_import_error",
                    message=f"provider module import failed: {exc}",
                    module=module_name,
                    exception_type=type(exc).__name__,
                )
            )
        except ProviderUnavailableError as exc:
            registry._record_discovery_diagnostic(
                ProviderDiagnostic(
                    code="provider_unavailable",
                    message=str(exc),
                    module=module_name,
                    exception_type=type(exc).__name__,
                )
            )
        except ProviderAdmissionError:
            continue
        except (TypeError, ValueError, AttributeError) as exc:
            registry._record_discovery_diagnostic(
                ProviderDiagnostic(
                    code="provider_discovery_invalid",
                    message=str(exc),
                    module=module_name,
                    exception_type=type(exc).__name__,
                )
            )
    return tuple(admitted)


def _resolve_module_names(modules: str | Iterable[str]) -> tuple[str, ...]:
    """Resolve a package or explicit module iterable deterministically.

    Args:
        modules: Package name or iterable of module names.

    Returns:
        Sorted unique fully qualified module names.
    """
    if isinstance(modules, str):
        package = importlib.import_module(modules)
        package_path = getattr(package, "__path__", None)
        if package_path is None:
            return (modules,)
        package_names = [
            item.name
            for item in pkgutil.iter_modules(
                package_path,
                prefix=f"{modules}.",
            )
            if not item.ispkg
        ]
        return tuple(sorted(set(package_names)))
    explicit_names = {str(name).strip() for name in modules if str(name).strip()}
    return tuple(sorted(explicit_names))


def _required_module_text(module: ModuleType, attribute: str) -> str:
    """Read one required non-empty module string.

    Args:
        module: Imported provider module.
        attribute: Required module attribute name.

    Returns:
        Stripped non-empty string.
    """
    value = getattr(module, attribute, None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"provider module must define non-empty {attribute}")
    return value.strip()


def _provider_factory(module: ModuleType) -> Any:
    """Validate and return a zero-argument create_provider factory.

    Args:
        module: Imported provider module.

    Returns:
        Callable zero-argument provider factory.
    """
    factory = getattr(module, "create_provider", None)
    if not callable(factory):
        raise TypeError("provider module must define create_provider()")
    signature = inspect.signature(factory)
    required = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        not in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }
    ]
    if required:
        raise TypeError("create_provider() must not require arguments")
    return factory


def _closed_schema_error(schema: Mapping[str, Any], *, path: str) -> str | None:
    """Return the first recursive closed-object JSON Schema violation.

    Args:
        schema: JSON Schema mapping to inspect.
        path: Human-readable schema path.

    Returns:
        Violation message or None when the schema is closed.
    """
    schema_type = schema.get("type")
    if schema_type == "object":
        if schema.get("additionalProperties") is not False:
            return f"{path} must set additionalProperties=false"
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return f"{path}.properties must be a mapping"
        required = schema.get("required", [])
        if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
            return f"{path}.required must be an array"
        if any(not isinstance(item, str) for item in required):
            return f"{path}.required entries must be strings"
        unknown_required = set(required) - {str(key) for key in properties}
        if unknown_required:
            return f"{path}.required contains unknown properties"
        for key, child in properties.items():
            if not isinstance(child, Mapping):
                return f"{path}.properties[{key!r}] must be a mapping"
            error = _closed_schema_error(child, path=f"{path}.{key}")
            if error is not None:
                return error
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, Mapping):
            return f"{path}.items must be a mapping"
        error = _closed_schema_error(items, path=f"{path}.items")
        if error is not None:
            return error
    for keyword in ("allOf", "anyOf", "oneOf"):
        variants = schema.get(keyword)
        if variants is None:
            continue
        if not isinstance(variants, Sequence) or isinstance(variants, (str, bytes)):
            return f"{path}.{keyword} must be an array"
        for index, variant in enumerate(variants):
            if not isinstance(variant, Mapping):
                return f"{path}.{keyword}[{index}] must be a mapping"
            error = _closed_schema_error(
                variant,
                path=f"{path}.{keyword}[{index}]",
            )
            if error is not None:
                return error
    return None


__all__ = [
    "ProviderAdmissionError",
    "ProviderDecisionMetadata",
    "ProviderDiagnostic",
    "ProviderRegistry",
    "ProviderUnavailableError",
    "RegisteredProvider",
    "discover_providers",
]

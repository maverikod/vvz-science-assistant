"""Import-safe public contracts for scientific provider modules.

Concrete provider modules must export ``PROVIDER_NAME`` and a zero-argument
``create_provider()`` factory. The factory must return a completed
``BaseScientificProvider`` implementation. A provider implementation is admitted
only after its client-evaluation decision has been published and verified.

This package intentionally imports no concrete providers or optional client
dependencies and performs no discovery, registration, or network activity.
"""

from science_assistant.progress import (  # type: ignore[import-not-found]
    DownloadProgress,
    ProgressSupport,
    ResumeSupport,
    TransferCapabilities,
)
from science_assistant.provider_contract import (  # type: ignore[import-not-found]
    BaseFileDatasetProvider,
    BaseHttpProvider,
    BaseLibraryProvider,
    BaseScientificProvider,
    BaseTapProvider,
    NormalizedDataset,
    ProviderCommandSpec,
    ProviderContext,
    ProviderDescriptor,
    ProviderOperationHandle,
    ProviderRequest,
    RawProviderResponse,
)

__all__ = [
    "BaseFileDatasetProvider",
    "BaseHttpProvider",
    "BaseLibraryProvider",
    "BaseScientificProvider",
    "BaseTapProvider",
    "DownloadProgress",
    "NormalizedDataset",
    "ProgressSupport",
    "ProviderCommandSpec",
    "ProviderContext",
    "ProviderDescriptor",
    "ProviderOperationHandle",
    "ProviderRequest",
    "RawProviderResponse",
    "ResumeSupport",
    "TransferCapabilities",
]

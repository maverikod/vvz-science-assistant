"""Public MCP command classes."""

from science_assistant.commands.astroquery_adql_command import AstroqueryAdqlCommand
from science_assistant.commands.astroquery_catalog_command import (
    AstroqueryCatalogCommand,
)
from science_assistant.commands.astroquery_object_command import AstroqueryObjectCommand
from science_assistant.commands.cern_open_data_commands import (
    CernOpenDataDownloadCommand,
    CernOpenDataRecordCommand,
    CernOpenDataSearchCommand,
)
from science_assistant.commands.data_transfer_commands import (
    DataDownloadBeginCommand,
    DataDownloadChunkCommand,
    DataDownloadStatusCommand,
    DataUploadBeginCommand,
    DataUploadChunkCommand,
    DataUploadCompleteCommand,
    DataUploadStatusCommand,
)
from science_assistant.commands.download_file_command import DownloadFileCommand
from science_assistant.commands.file_commands import (
    FileDeleteCommand,
    FileGetCommand,
    FileLsCommand,
    FileReceiveCommand,
)
from science_assistant.commands.info_command import InfoCommand
from science_assistant.commands.package_transfer_commands import (
    DataPackagePartCommand,
    DataPackageStatusCommand,
    DataPackageWaitCommand,
)

COMMAND_TYPES = [
    InfoCommand,
    AstroqueryCatalogCommand,
    AstroqueryObjectCommand,
    AstroqueryAdqlCommand,
    DownloadFileCommand,
    CernOpenDataSearchCommand,
    CernOpenDataRecordCommand,
    CernOpenDataDownloadCommand,
    DataUploadBeginCommand,
    DataUploadChunkCommand,
    DataUploadCompleteCommand,
    DataUploadStatusCommand,
    DataDownloadBeginCommand,
    DataDownloadChunkCommand,
    DataDownloadStatusCommand,
    DataPackagePartCommand,
    DataPackageWaitCommand,
    DataPackageStatusCommand,
    FileReceiveCommand,
    FileGetCommand,
    FileLsCommand,
    FileDeleteCommand,
]

__all__ = ["COMMAND_TYPES"]

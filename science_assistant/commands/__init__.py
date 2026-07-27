"""Public MCP command classes."""

from science_assistant.commands.info_command import InfoCommand
from science_assistant.commands.astroquery_catalog_command import AstroqueryCatalogCommand
from science_assistant.commands.astroquery_object_command import AstroqueryObjectCommand
from science_assistant.commands.astroquery_adql_command import AstroqueryAdqlCommand
from science_assistant.commands.download_file_command import DownloadFileCommand
from science_assistant.commands.data_transfer_commands import (
    DataUploadBeginCommand, DataUploadChunkCommand, DataUploadCompleteCommand, DataUploadStatusCommand,
    DataDownloadBeginCommand, DataDownloadChunkCommand, DataDownloadStatusCommand,
)
from science_assistant.commands.package_transfer_commands import (
    DataPackagePartCommand, DataPackageWaitCommand, DataPackageStatusCommand,
)
from science_assistant.commands.file_commands import (
    FileReceiveCommand, FileGetCommand, FileLsCommand, FileDeleteCommand,
)

COMMAND_TYPES = [
    InfoCommand,
    AstroqueryCatalogCommand,
    AstroqueryObjectCommand,
    AstroqueryAdqlCommand,
    DownloadFileCommand,
    DataUploadBeginCommand, DataUploadChunkCommand, DataUploadCompleteCommand, DataUploadStatusCommand,
    DataDownloadBeginCommand, DataDownloadChunkCommand, DataDownloadStatusCommand,
    DataPackagePartCommand, DataPackageWaitCommand, DataPackageStatusCommand,
    FileReceiveCommand, FileGetCommand, FileLsCommand, FileDeleteCommand,
]

__all__ = ["COMMAND_TYPES"]

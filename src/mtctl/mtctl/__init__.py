"""mtctl -- single-file SSH/SFTP transfer to/from RouterOS.

Public API
----------
- :func:`load_env`     -- parse a ``.env`` file into a :class:`Settings`
- :class:`Settings`    -- connection settings dataclass
- :func:`upload`       -- copy local file to remote path
- :func:`download`     -- copy remote file to local path
- :func:`create_backup`-- snapshot router into ``backups/<timestamp>/``
- :func:`run_import`   -- run ``/import file-name=<remote>`` on the router
- :class:`DeployError` -- raised on deploy-orchestrator input/validation
- :class:`BackupError` -- raised when the router rejects a backup command
- :class:`ImportError` -- raised when /import fails on the router
- :class:`SshSession`  -- context-manager SSH connection (paramiko wrapper)
- :class:`SshError`    -- raised on SSH connect / channel-open / exec failures
- :class:`SftpClient`  -- context-manager SFTP channel wrapper
- :class:`SftpError`   -- raised on SFTP transfer / remote-FS failures
"""

from .backup import BackupError, create_backup
from .config import Settings, load_env
from .deployer import DeployError, download, upload
from .importer import ImportError, run_import
from .sftp import SftpClient, SftpError
from .ssh import SshError, SshSession

__version__ = "0.1.0"

__all__ = [
    "BackupError",
    "DeployError",
    "ImportError",
    "Settings",
    "SftpClient",
    "SftpError",
    "SshError",
    "SshSession",
    "__version__",
    "create_backup",
    "download",
    "load_env",
    "run_import",
    "upload",
]

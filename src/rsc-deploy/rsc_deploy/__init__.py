"""rsc-deploy -- single-file SSH/SFTP transfer to/from RouterOS.

Public API
----------
- :func:`load_env`    -- parse a ``.env`` file into a :class:`Settings`
- :class:`Settings`   -- connection settings dataclass
- :func:`upload`      -- copy local file to remote path
- :func:`download`    -- copy remote file to local path
- :class:`DeployError`-- raised on deploy-orchestrator input/validation
- :class:`SshSession` -- context-manager SSH connection (paramiko wrapper)
- :class:`SshError`   -- raised on SSH connect / channel-open failures
- :class:`SftpClient` -- context-manager SFTP channel wrapper
- :class:`SftpError`  -- raised on SFTP transfer / remote-FS failures
"""

from .config import Settings, load_env
from .deployer import DeployError, download, upload
from .sftp import SftpClient, SftpError
from .ssh import SshError, SshSession

__version__ = "0.1.0"

__all__ = [
    "DeployError",
    "Settings",
    "SftpClient",
    "SftpError",
    "SshError",
    "SshSession",
    "__version__",
    "download",
    "load_env",
    "upload",
]

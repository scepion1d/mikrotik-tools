"""rsc-deploy -- upload RouterOS ``.rsc`` files over SSH/SFTP.

Public API
----------
- :func:`load_env` -- parse a ``.env`` file into a :class:`Settings`
- :class:`Settings` -- connection settings dataclass
- :func:`deploy` -- connect, optionally clean, upload
- :class:`DeployError` -- raised on connection / transfer failures
"""

from .config import Settings, load_env
from .deployer import DeployError, deploy

__version__ = "0.1.0"

__all__ = [
    "DeployError",
    "Settings",
    "__version__",
    "deploy",
    "load_env",
]

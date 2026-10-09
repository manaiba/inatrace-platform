"""`inatrace deploy …`: INATrace on a server you reach with ssh.

An instance is a directory, deploy/instances/<name>/ (git-ignored), holding its
.env (every setting, secrets included; see deploy/.env.example),
backend.local.properties and anything else you add (a compose.override.yaml...).
`up` syncs deploy/server/ (compose.yaml, the Caddyfile, the backup scripts) with
the instance's files on top to ~/inatrace on the server, with rsync, and runs
Compose there. The CLI only saves typing: everything it does over ssh is what
docs/deploy.md shows by hand. The server needs Docker and rsync; `prepare`
installs them where a profile in deploy/prereqs/ fits the distribution.

The parts: instance (the settings), remote (the server over ssh), wizard (init),
lifecycle (prepare, up, down), backups, checks (status, logs, smoke), users (admin);
common holds what they share.
"""

from .backups import create, delete, download, listing, restore
from .checks import logs, smoke, status
from .common import DeployError
from .instance import CDN_HEADERS, FRONTS
from .lifecycle import destroy, down, prepare, up
from .users import admin
from .wizard import init

__all__ = ["CDN_HEADERS", "FRONTS", "DeployError", "admin", "create", "delete", "destroy", "down", "download", "init", "listing", "logs",
           "prepare", "restore", "smoke", "status", "up"]

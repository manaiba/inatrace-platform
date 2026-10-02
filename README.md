# inatrace-platform

Dev environment for [**INATrace**](https://github.com/agstack/inatrace)
([AgStack](https://github.com/agstack)): one container with every toolchain (Java 17 + Maven,
Node 14 for Angular 10, MySQL via nested Docker), the INATrace repos cloned side by side, and a
local gateway that routes like production.

## Prerequisites

- **Linux** host with [**Docker**](https://docs.docker.com/engine/install/) usable without `sudo`.
- [**Dev Containers CLI**](https://github.com/devcontainers/cli): `npm install -g @devcontainers/cli`.
- An **SSH key on your GitHub account**, loaded in your host's agent (`ssh -T git@github.com`
  greets you). Repos are cloned over SSH, public ones included.
- Optional: a **Wayland** session with PipeWire/PulseAudio, for a headed browser, clipboard
  paste and Claude's `/voice`.

## Quick start

```bash
git clone git@github.com:agstack/inatrace-platform.git
cd inatrace-platform
cp .env.example .env && chmod 600 .env       # optional, see Settings
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . bash  # lands in /src
```

The first `up` builds the image and runs `/src/setup.sh`: clones the repos in `src/repos.txt`,
installs Node 14, Claude Code and Playwright in `$HOME`, starts the gateway, and prints a report
of what is available. `setup.sh` is idempotent — re-run it whenever something looks off.

After a reboot or a new login, run the same `devcontainer up`.

## Settings

Everything you may want to change goes in **`.env`** at the repo root (git-ignored).
[`.env.example`](.env.example) documents each key. Only this file counts — shell variables are
ignored — and it is read on every `devcontainer up`, which recreates the container when needed.

| Key | For |
|-----|-----|
| `INATRACE_GATEWAY_PORT`, `_BACKEND_PORT`, `_FRONTEND_PORT` | host ports (default 8000, 9000, 9080) |
| `INATRACE_BIND_ADDRESS` | `127.0.0.1` (default) or `0.0.0.0` to reach it from the LAN |
| `INATRACE_GH_TOKEN` | GitHub token for `gh` / HTTPS; mounted as a file, never an env var |
| `INATRACE_FORK_<REPO>` | your fork of a repo — see [Forks](#forks) |
| `PLAYWRIGHT_HEADLESS` | force the browser headless |

## Running INATrace

Inside the container (details in each repo's README):

```bash
# MySQL 8.4, nested container
docker run -d --name inatrace-mysql --restart unless-stopped \
  -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=inatrace \
  -e MYSQL_USER=inatrace -e MYSQL_PASSWORD=inatrace \
  -p 3306:3306 mysql:8.4

# Backend (:8080) — from application.properties.template
cd /src/inatrace-backend && mvn spring-boot:run

# Frontend (:4200) — environment.dev.ts per the frontend's README
cd /src/inatrace-frontend && nvm use 14 && npm install && npm run dev
```

Then open **<http://127.0.0.1:8000>** on the host (`127.0.0.1`, not `localhost`: IPv4 only).

| Host | Container | |
|------|-----------|---|
| 8000 | 8000 | **gateway** (`src/gateway`): `/api`, `/v3/api-docs`, `/swagger-ui*` → backend, rest → frontend |
| 9000 | 8080 | backend, direct |
| 9080 | 4200 | frontend, direct |

MySQL is reachable from inside the container only. A gateway upstream that is not running answers
`502`.

Known upstream issues, not fixed here:
- **Frontend**: `npm ci` fails (lockfile out of sync) — use `npm install`, don't commit the lockfile.
- **Backend tests**: Testcontainers' default Docker API is too old for Docker 29+ —
  `mvn verify -Dapi.version=1.44`.
- **File storage** (`INATrace.fileStorage.root`, `INATrace.documents.root`): use a path under
  `/home/dev`; `/tmp` does not survive a rebuild.

## Git

- Your host's SSH agent is used through a relay; keys never enter the container. Supported:
  OpenSSH (`~/.ssh/agent`) and any agent socket in `$XDG_RUNTIME_DIR` (systemd, GNOME,
  gpg-agent). An agent in `/tmp` is not visible: start it with
  `ssh-agent -a "$XDG_RUNTIME_DIR/ssh-agent.socket"`.
- Git identity comes from the host's `~/.gitconfig`.

### Forks

Without push access to agstack, point a repo at your fork in `.env` — the key is the repo's
directory upper-cased, `-` as `_`:

```bash
INATRACE_FORK_INATRACE_BACKEND=your-org/inatrace-backend
```

`setup.sh` then makes the fork `origin` and agstack `upstream` (push disabled); a plain
`git push` goes to the fork. Existing clones switch over only if their `origin` is still
agstack's. After editing, run `devcontainer up`, then `/src/setup.sh`.

Removing the line does **not** switch back. To do it by hand, inside the repo:

```bash
git remote remove origin && git remote rename upstream origin   # also drops pushDefault
git config --unset remote.origin.pushurl
```

## What persists

| Where | Holds |
|-------|-------|
| volume `inatrace-platform-home` → `/home/dev` | Claude Code login and sessions, shell history, `~/.m2`, `~/.npm`, Node 14, `gh` login, IDE backends |
| volume `inatrace-platform-docker` → `/var/lib/docker` | nested Docker: images, MySQL data |
| `./src` → `/src` | `setup.sh`, the gateway and the cloned repos |

Start from scratch (keeps `src/` and `.env`):

```bash
.devcontainer/cleanup.sh --purge --dry-run   # list
.devcontainer/cleanup.sh --purge             # delete
```

## Claude Code

Run `claude` from `/src`, where it sees every repo. It runs without permission prompts — the
container is the sandbox — and has the Playwright MCP server for a real Chromium.
[`src/CLAUDE.md`](src/CLAUDE.md) holds the workspace rules. Note the container is
`privileged` (it runs its own Docker daemon) and sees your host session's runtime dir: a sandbox
against accidents, not a security boundary.

## IDEs

Tested with the Dev Containers CLI. VS Code works with these caveats:

- `devcontainer.json` turns off port auto-forwarding (it forwarded stray ports, even MySQL).
  Claude's `/login` and manual "Add Port" still work. VS Code applies `customizations.vscode`
  only once per home volume — after changing it,
  `rm ~/.vscode-server/data/Machine/.writeMachineSettingsMarker` and rebuild.
- Set `"dev.containers.dockerCredentialHelper": false`: its helper breaks `docker pull` outside
  VS Code terminals.
- Set `"java.autobuild.enabled": false`: the Java extension breaks `mvn verify`.
- In VS Code terminals, `ssh-add -l` asks VS Code's agent; `ssh` itself still uses yours.
- Claude's `/ide` only finds the editor in terminals opened after the extension activated.

JetBrains: untested.

## Troubleshooting

- **`port is already allocated`**: `.devcontainer/cleanup.sh` lists leftovers and who holds the
  port; or change the ports in `.env`.
- **Docker daemon not answering**: `/usr/local/share/container-init.sh`, then
  `/var/log/dockerd.log`.
- **ssh cannot reach the agent**: `ssh-agent-connect < /dev/null` tells whether any host agent
  answers; `/usr/local/share/container-init.sh` restarts the relay.
- **MySQL `EXPKEYSIG` while building**: bump the year of `RPM-GPG-KEY-mysql-*` in the Dockerfile.
- **Coming from inatrace-devcontainer**: stop its stack first. To keep your Claude login:
  ```bash
  docker run --rm -v inatrace-claude:/from:ro -v inatrace-platform-home:/home ubuntu:24.04 \
    sh -c 'cp -a /from/. /home/.claude/ && chown -R "$(stat -c %u:%g /home)" /home/.claude'
  ```

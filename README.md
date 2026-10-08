# inatrace-platform

Dev environment for [INATrace](https://github.com/agstack/inatrace): the INATrace repos cloned
side by side, a dev stack routed like production (gateway, MySQL, Mailpit, and the backend and
frontend images), and `bin/inatrace`, the CLI that drives them. Inside a dev container or on your
own Linux machine.

## Quick start

On a Linux host with Docker and the [Dev Containers CLI](https://github.com/devcontainers/cli)
(or VS Code), and an SSH key for GitHub in your agent:

```bash
git clone git@github.com:agstack/inatrace-platform.git
cd inatrace-platform
devcontainer up --workspace-folder .            # build, set up, clone the repos
devcontainer exec --workspace-folder . bash     # a shell in /workspace

inatrace stack up --mode images                 # INATrace at http://127.0.0.1:8000
inatrace smoke                                  # check it end to end
```

Every requirement and option: [Getting started](docs/getting-started.md#with-the-dev-container-recommended).

### Running without the dev container

On your own Linux machine, with Docker, [uv](https://docs.astral.sh/uv/) and git, the same CLI
runs as `bin/inatrace`: [Getting started → On your machine](docs/getting-started.md#on-your-machine).

## Documentation

| Page | What it covers |
|---|---|
| [Getting started](docs/getting-started.md) | Requirements and first run, with the dev container or on your machine |
| [Configuration](docs/configuration.md) | Every setting in `.env`: modes, images, forks, the dev container |
| [Dev stack](docs/dev-stack.md) | Modes, running from your checkout, ports and links, data |
| [Dev container](docs/dev-container.md) | Provisioning, ports, SSH agent, what persists, IDEs, troubleshooting |
| [Smoke tests](docs/smoke-tests.md) | End-to-end checks of the running stack |
| [Premises](docs/premises.md) | Settled choices, and what they rule out |

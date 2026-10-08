# Getting started

Setting up the INATrace platform on a development machine: inside the dev container, or directly
on your machine. Settings are covered separately in [Configuration](configuration.md).

## With the dev container (recommended)

The container brings every toolchain (Java 17, Maven, Node 14, Docker) and sets itself up.

### Requirements
* Linux host with [Docker](https://docs.docker.com/engine/install/), usable without `sudo`
* The [Dev Containers CLI](https://github.com/devcontainers/cli)
  (`npm install -g @devcontainers/cli`), or VS Code with the Dev Containers extension
* Python `3.10` or higher on the host, for `.devcontainer/initialize.py`, which runs before
  every `up`
* An SSH key on your GitHub account, loaded in your agent: `ssh -T git@github.com` greets you

### Optional
* A Wayland session with PipeWire or PulseAudio, for a headed browser, clipboard paste and
  Claude's `/voice`

### How to run
1. Clone the repository

   ```
   git clone git@github.com:agstack/inatrace-platform.git
   cd inatrace-platform
   ```

2. (*OPTIONAL*) Create your settings: `cp .env.example .env && chmod 600 .env`, see
   [Configuration](configuration.md)

3. Create the container, or in VS Code run *Dev Containers: Reopen in Container*

   ```
   devcontainer up --workspace-folder .
   ```

4. Open a shell in it, in `/workspace`

   ```
   devcontainer exec --workspace-folder . bash
   ```

The first `up` builds the image and [provisions](dev-container.md#provisioning) the container: it
clones the repos and ends with a report of what is available (`inatrace doctor`). After a reboot
or a new login, run the same `up`.

## On your machine

The platform works the same without the container; you install the toolchains.

### Requirements
* Linux, with Docker and its Compose plugin usable without `sudo`
* [uv](https://docs.astral.sh/uv/), which brings Python and the CLI's dependencies
* git, and an SSH key on your GitHub account

### Optional
* Chromium's system libraries, for `inatrace smoke`
* Java `17` and Maven, to run the backend from your checkout
* Node `14`, to run the frontend from your checkout

Neither toolchain is needed for a part that runs from its image, see
[modes](dev-stack.md#modes).

### Installing the requirements
* **Docker**: [Docker's guide](https://docs.docker.com/engine/install/) for your distro (it
  includes Compose), then
  [add yourself to the `docker` group](https://docs.docker.com/engine/install/linux-postinstall/)
  and log in again.
* **uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh`, then open a new terminal.
* **SSH key**: [add one to GitHub](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)
  and load it in your agent.
* **Chromium's system libraries**: on Debian or Ubuntu, from the cloned repository,
  `uv run --group smoke playwright install-deps chromium` (asks for sudo). On other distros,
  install them with your package manager: a smoke run names the first missing library, and
  [Playwright's system requirements](https://playwright.dev/docs/intro#system-requirements)
  list them.
* **Java 17, Maven, Node 14**: your distro's packages, or a version manager:
  [SDKMAN!](https://sdkman.io/) for Java and Maven, [nvm](https://github.com/nvm-sh/nvm) for
  Node (`nvm install 14`).

### How to run
1. Clone the repository, as above

2. (*OPTIONAL*) Create your settings: `cp .env.example .env && chmod 600 .env`

3. Clone the repos into `repos/`

   ```
   bin/inatrace repos sync
   ```

4. Check what is available

   ```
   bin/inatrace doctor
   ```

On your machine, `inatrace` in these pages is `bin/inatrace`. `.devcontainer/provision.py`
refuses to run here: it would rewrite your own `~/.bashrc` and git config.

## Next steps
* Start INATrace: [Dev stack](dev-stack.md)
* Work from your own forks: [Configuration](configuration.md#forks)

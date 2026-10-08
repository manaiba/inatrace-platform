# syntax=docker/dockerfile:1

# Ubuntu 24.04 LTS. Plain Ubuntu rather than a language base image: INATrace
# spans Java and Node, so no single runtime deserves to be the base. Each is
# installed explicitly below.
FROM ubuntu:24.04

# ============================================================================
# SYSTEM PACKAGES ONLY. Nothing is written to /home/dev here: $HOME is a named
# volume (see compose.yaml) and would hide it after the first create. Tools and
# config that live in $HOME — nvm/Node 14, Claude Code, the Playwright browser,
# dotfiles — are installed by `.devcontainer/provision.py`, idempotently.
#
# LAYER ORDER IS DELIBERATE — least likely to change first, so editing a block
# rebuilds as little as possible. Add interactive tools to the LAST apt block.
#
# The apt caches are BuildKit cache mounts. To pick up newer packages, bypass
# the layer cache:  devcontainer build --workspace-folder . --no-cache
# ============================================================================

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive

# Ubuntu images delete every .deb right after install (docker-clean), which
# would leave the /var/cache/apt cache mounts below always empty.
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' \
      > /etc/apt/apt.conf.d/keep-cache

# ---- Build toolchain and essentials ----
# xz-utils: nvm downloads Node as .tar.xz. openssh-client + socat: git over SSH
# through the host agent (see the relay below). procps: ps/pgrep.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      curl \
      git \
      gnupg \
      locales \
      openssh-client \
      procps \
      socat \
      sudo \
      unzip \
      xz-utils

# ---- Non-root user `dev` with passwordless sudo ----
# Ubuntu 24.04 ships a default `ubuntu` user with uid/gid 1000. It is removed so
# it cannot collide with `dev` — neither here nor when the devcontainer tooling
# rewrites dev's uid/gid to match the host user (updateRemoteUserUID).
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000
RUN if id ubuntu >/dev/null 2>&1; then userdel -r ubuntu; fi \
    && if getent group ubuntu >/dev/null; then groupdel ubuntu; fi \
    && groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} -m -s /bin/bash ${USERNAME} \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

# Mount point of the workspace (the whole repo).
RUN mkdir -p /workspace && chown ${USERNAME}:${USERNAME} /workspace

# A private XDG_RUNTIME_DIR (pointed at by compose.yaml): Wayland clients expect
# one, mode 0700 and owned by the running user, and a container has no logind
# session to create it.
RUN mkdir -p /tmp/xdg-runtime \
    && chown ${USERNAME}:${USERNAME} /tmp/xdg-runtime \
    && chmod 700 /tmp/xdg-runtime

# ---- Host desktop bridges: microphone, clipboard, headed browser ----
# The container has no /dev/snd and no display. These talk to the HOST desktop
# through the sockets bind-mounted in compose.yaml. libsox-fmt-pulse makes
# SoX's `rec` (Claude's /voice) work without an ALSA card; libwayland-client0 is
# what Chromium dlopen()s for --ozone-platform=wayland.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      alsa-utils \
      libasound2-plugins \
      libsox-fmt-pulse \
      libwayland-client0 \
      pulseaudio-utils \
      sox \
      wl-clipboard \
      xclip

# Route ALSA through the PulseAudio socket; otherwise `arecord` fails with
# "cannot find card '0'".
RUN printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' > /etc/asound.conf

# ---- GitHub CLI (gh) ----
# Keys are piped to tee rather than written by `curl -o`: hosts with an
# AppArmor profile for /usr/bin/curl (openSUSE) deny its writes outside /tmp
# and $HOME when the build runs unconfined, as in the nested Docker.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | tee /usr/share/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh

# ---- MySQL 8.4 client (mysql, mysqldump…) ----
# From MySQL's own repo: the distro client cannot always authenticate against
# MySQL 8.4's default caching_sha2_password, and INATrace runs 8.4.
# The key file is year-stamped and Oracle renews it under a new name: -2025 is
# valid until 2027-10-23. When the build fails with EXPKEYSIG, bump the year.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    curl -fsSL https://repo.mysql.com/RPM-GPG-KEY-mysql-2025 \
      | gpg --dearmor -o /usr/share/keyrings/mysql-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/mysql-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/mysql-archive-keyring.gpg] https://repo.mysql.com/apt/ubuntu/ $(. /etc/os-release && echo "${VERSION_CODENAME}") mysql-8.4-lts" \
      > /etc/apt/sources.list.d/mysql.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends mysql-community-client

# ---- Node 24 LTS (system) ----
# The system Node runs global tooling (the Playwright MCP server) and is the
# default `node`. The frontend's Node 14 comes from nvm, installed in $HOME by
# `.devcontainer/provision.py` (`nvm use 14`).
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs

# ---- Playwright MCP server + Chromium system libraries ----
# The MCP server decides which Playwright version everything must use (it
# resolves playwright-core from its own dependency tree), so the global
# playwright is pinned to the version it declares. The browser BINARY is not
# downloaded here: it lives in $HOME (~/.cache/ms-playwright) and `.devcontainer/provision.py`
# installs the matching build. `install-deps` only apt-installs the ~40 system
# libraries Chromium needs.
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    MCP_PW_VERSION="$(npm view @playwright/mcp@latest dependencies.playwright)" \
    && echo "@playwright/mcp pins playwright ${MCP_PW_VERSION}" \
    && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install -g \
         "playwright@${MCP_PW_VERSION}" \
         "@playwright/mcp@latest" \
    && playwright install-deps chromium

# ---- Java 17 + Maven ----
# inatrace-backend asks for Java 17 and has no Maven wrapper. The JAVA_HOME
# symlink is arch-agnostic (not everyone is on amd64).
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      openjdk-17-jdk-headless \
    && ln -sfn "/usr/lib/jvm/java-17-openjdk-$(dpkg --print-architecture)" \
         /usr/lib/jvm/default-java
ENV JAVA_HOME=/usr/lib/jvm/default-java

# Maven from Apache's tarball, pinned, so every developer builds with the same
# version. Fetched from Maven Central, a CDN that keeps every release:
# archive.apache.org throttles downloads.
ARG MAVEN_VERSION=3.9.16
RUN curl -fsSL "https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/${MAVEN_VERSION}/apache-maven-${MAVEN_VERSION}-bin.tar.gz" \
      -o /tmp/maven.tar.gz \
    && mkdir -p /opt/maven \
    && tar -xzf /tmp/maven.tar.gz -C /opt/maven --strip-components=1 \
    && rm /tmp/maven.tar.gz \
    && ln -sfn /opt/maven/bin/mvn /usr/local/bin/mvn

# ---- uv ----
# Runs the platform's CLI (bin/inatrace) with the dependencies in pyproject.toml.
# Pinned, from Astral's image.
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /uvx /usr/local/bin/

# ---- Docker engine, for docker-IN-docker ----
# A full daemon inside the dev container, NOT the host socket. Nested
# containers publish their ports on this container's own network namespace, so
# `docker run -p 3306:3306 mysql` is reachable on localhost from the backend
# running here; bind mounts of /workspace paths resolve inside this container; and
# the host's Docker stays untouched. Requires `privileged: true` and the
# /var/lib/docker volume, both in compose.yaml.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
         | tee /etc/apt/keyrings/docker.asc > /dev/null \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
      > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
         containerd.io \
         docker-buildx-plugin \
         docker-ce \
         docker-ce-cli \
         docker-compose-plugin \
    && usermod -aG docker ${USERNAME}

# ---- Container init: nested dockerd + SSH agent relay ----
# container-init.sh is the Compose entrypoint (it then execs `sleep infinity`)
# and, with no arguments, the postStartCommand. Both parts are idempotent.
#
# dockerd: the check is "is a dockerd process running", not "does the socket
# exist" — a restarted container keeps /var/run from its previous run, so a stale
# docker.sock would otherwise skip the start and a stale docker.pid would make
# dockerd refuse to start.
#
# SSH agent relay: the host's agent socket changes name every session (OpenSSH
# >= 10.1 creates ~/.ssh/agent/s.<random>), so no single socket can be bind-mounted.
# compose.yaml mounts the stable DIRECTORIES instead (~/.ssh/agent and
# $XDG_RUNTIME_DIR), and socat listens on a fixed path (SSH_AUTH_SOCK). For EVERY
# connection, ssh-agent-connect picks the agent socket that answers right now —
# so long-running processes keep working across host logins, with no container
# recreation.
RUN cat > /usr/local/bin/ssh-agent-connect <<'EOS'
#!/usr/bin/env bash
# Connects stdin/stdout to the host SSH agent that is answering now. Candidates:
# OpenSSH >= 10.1 (~/.ssh/agent/s.*), then the usual fixed sockets in
# $XDG_RUNTIME_DIR: systemd/Debian openssh units, GNOME gcr and keyring,
# gpg-agent.
#
# A host often runs more than one agent (say OpenSSH's plus GNOME's), and only
# some hold keys. So an agent WITH identities wins over one that merely answers;
# among equals, the most recently created socket wins.
shopt -s nullglob
candidates=(
  /tmp/host-ssh-agent/s.*
  /tmp/host-runtime/openssh_agent
  /tmp/host-runtime/ssh-agent.socket
  /tmp/host-runtime/gcr/ssh
  /tmp/host-runtime/keyring/ssh
  /tmp/host-runtime/gnupg/S.gpg-agent.ssh
)
[ ${#candidates[@]} -gt 0 ] || { echo "ssh-agent-connect: no host SSH agent socket found" >&2; exit 1; }

empty=""
for sock in $(ls -t "${candidates[@]}" 2>/dev/null); do
  [ -S "${sock}" ] || continue
  # ssh-add -l: 0 = has keys, 1 = answers but empty, 2 = no agent there.
  SSH_AUTH_SOCK="${sock}" ssh-add -l > /dev/null 2>&1
  case $? in
    0) exec socat - "UNIX-CONNECT:${sock}" ;;
    1) [ -n "${empty}" ] || empty="${sock}" ;;
  esac
done
[ -n "${empty}" ] && exec socat - "UNIX-CONNECT:${empty}"
echo "ssh-agent-connect: no host SSH agent is answering" >&2
exit 1
EOS

RUN cat > /usr/local/share/container-init.sh <<'EOS'
#!/usr/bin/env bash
set -u

# --- nested Docker daemon ---
if ! pgrep -x dockerd > /dev/null; then
  sudo rm -f /var/run/docker.sock /var/run/docker.pid
  sudo sh -c 'dockerd > /var/log/dockerd.log 2>&1 &'
fi
for _ in $(seq 1 30); do
  docker info > /dev/null 2>&1 && break
  sleep 1
done
docker info > /dev/null 2>&1 \
  || echo "container-init: dockerd is not answering - see /var/log/dockerd.log" >&2

# --- SSH agent relay on SSH_AUTH_SOCK ---
relay="${SSH_AUTH_SOCK:-/tmp/ssh-agent.sock}"
if ! pgrep -f "UNIX-LISTEN:${relay}," > /dev/null; then
  rm -f "${relay}"
  nohup socat "UNIX-LISTEN:${relay},fork,mode=600" EXEC:/usr/local/bin/ssh-agent-connect \
    > /dev/null 2>&1 &
fi

if [ $# -gt 0 ]; then
  exec "$@"
fi
EOS
RUN chmod +x /usr/local/bin/ssh-agent-connect /usr/local/share/container-init.sh

# ---- Convenience CLI tools (THE BLOCK YOU ARE MOST LIKELY TO EDIT) ----
# Last on purpose: adding a tool rebuilds this layer alone.
# iproute2: ip, ss. xvfb: a virtual X server for WebGL/MapLibre rendering in a
# headless browser.
# python3: node-gyp shells out to it when an npm dependency builds from source.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      bash-completion \
      btop \
      htop \
      iproute2 \
      jq \
      less \
      python3 \
      sqlite3 \
      tmux \
      tree \
      vim \
      xvfb

# ---- Environment for tools that live in $HOME ----
# CLAUDE_CONFIG_DIR keeps Claude's settings AND its .claude.json (account,
# onboarding flag, MCP registrations) in one directory. GIT_CONFIG_GLOBAL is a
# writable config that includes the read-only host ~/.gitconfig (mounted at
# ~/.gitconfig.host). NG_CLI_ANALYTICS stops the Angular CLI from prompting.
# UV_PROJECT_ENVIRONMENT keeps the CLI's virtualenv on the home volume, next to
# uv's cache, instead of .venv/ in the checkout, which the host may use too.
# INATRACE_DEVCONTAINER tells `.devcontainer/provision.py` it runs here, where it may change $HOME.
ENV INATRACE_DEVCONTAINER=1 \
    CLAUDE_CONFIG_DIR=/home/${USERNAME}/.claude \
    GIT_CONFIG_GLOBAL=/home/${USERNAME}/.gitconfig.local \
    NVM_DIR=/home/${USERNAME}/.nvm \
    NG_CLI_ANALYTICS=false \
    PATH=/home/${USERNAME}/.local/bin:${PATH} \
    UV_PROJECT_ENVIRONMENT=/home/${USERNAME}/.local/share/inatrace-platform/venv

# The platform's CLI, on every process's PATH (the repo is always at /workspace).
RUN ln -s /workspace/bin/inatrace /usr/local/bin/inatrace

USER ${USERNAME}
WORKDIR /workspace

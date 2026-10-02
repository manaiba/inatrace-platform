#!/usr/bin/env bash
#
# setup.sh — bring $HOME and /src to their expected state. Idempotent.
#
# Runs as the dev container's postCreateCommand. Safe to re-run by hand at any
# time:   ./setup.sh
#
# The image carries system packages only. $HOME is a persistent volume, and
# everything that lives there is installed or refreshed here: shell and git
# config, ~/.ssh/known_hosts, Node 14 (nvm), Claude Code, the Playwright browser
# and MCP server — plus the cloned repositories under /src. Each step checks
# before acting, so a second run changes nothing and finishes quickly.
#
# A failing step never aborts the rest: it is reported and listed in the
# summary at the end.
#
set -uo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOS_FILE="${SRC_DIR}/repos.txt"
TOKEN_PATH="/run/secrets/gh_token"
FORKS_FILE="/run/inatrace/forks.env"

NVM_VERSION="v0.40.8"
# inatrace-frontend (Angular 10) does not run on newer Node majors.
NODE_FRONTEND="14"

BASHRC_BEGIN="# >>> inatrace-platform >>>"
BASHRC_END="# <<< inatrace-platform <<<"

failed=()

step() { # step "<title>" <function>
  local title="$1"; shift
  echo "==> ${title}"
  if ! "$@"; then
    failed+=("${title}")
    echo "    !! ${title}: failed — continuing" >&2
  fi
}

have_token() { [ -r "${TOKEN_PATH}" ] && [ -s "${TOKEN_PATH}" ]; }

# --- Steps --------------------------------------------------------------------

# $HOME is a volume created with the uid/gid dev had at the time. If the host
# user's uid changed since (another machine, another account), give it back.
fix_home_ownership() {
  local uid gid
  uid="$(id -u)"; gid="$(id -g)"
  [ "$(stat -c %u:%g "${HOME}")" = "${uid}:${gid}" ] && return 0
  echo "    ${HOME} belongs to $(stat -c %u:%g "${HOME}"); handing it to ${uid}:${gid}"
  # -xdev: stay on the volume; the read-only bind mounts inside it are skipped.
  sudo find "${HOME}" -xdev \( ! -user "${uid}" -o ! -group "${gid}" \) \
    -exec chown -h "${uid}:${gid}" {} +
}

# A managed block at the end of ~/.bashrc, rewritten on every run: changes to
# it here reach existing volumes, and a second run leaves the file untouched.
configure_shell() {
  local rc="${HOME}/.bashrc" tmp
  touch "${rc}"
  tmp="$(mktemp)"
  awk -v begin="${BASHRC_BEGIN}" -v end="${BASHRC_END}" '
    index($0, begin) == 1 { skip = 1 }
    !skip { print }
    index($0, end) == 1 { skip = 0 }
  ' "${rc}" > "${tmp}"
  cat >> "${tmp}" <<EOF
${BASHRC_BEGIN} (managed by /src/setup.sh; edits inside are overwritten)
[ -f /usr/share/bash-completion/bash_completion ] && . /usr/share/bash-completion/bash_completion
export HISTSIZE=100000 HISTFILESIZE=200000
PROMPT_COMMAND="history -a\${PROMPT_COMMAND:+; \$PROMPT_COMMAND}"
[ -s "\$NVM_DIR/nvm.sh" ] && . "\$NVM_DIR/nvm.sh"
[ -s "\$NVM_DIR/bash_completion" ] && . "\$NVM_DIR/bash_completion"
# Chromium for tools that look for Chrome, such as the frontend's Karma tests.
export CHROME_BIN="\$HOME/.local/bin/chrome"
${BASHRC_END}
EOF
  if cmp -s "${tmp}" "${rc}"; then
    rm -f "${tmp}"
  else
    mv "${tmp}" "${rc}"
    echo "    updated ~/.bashrc"
  fi
}

# Writable global git config that includes the read-only host ~/.gitconfig.
# GitHub is reached over SSH through the host agent by default; the optional
# token only adds `gh` login and an HTTPS credential helper.
configure_git() {
  local cfg="${GIT_CONFIG_GLOBAL:-${HOME}/.gitconfig.local}"
  local host_cfg="${HOME}/.gitconfig.host"
  touch "${cfg}"
  if ! git config --file "${cfg}" --get-all include.path | grep -qxF "${host_cfg}"; then
    git config --file "${cfg}" --add include.path "${host_cfg}"
    echo "    included the host git config"
  fi
  if have_token; then
    if ! gh auth status >/dev/null 2>&1; then
      tr -d '\r\n' < "${TOKEN_PATH}" | gh auth login --with-token || return 1
      echo "    gh logged in with the token"
    fi
    gh auth setup-git || return 1
  fi
}

# known_hosts lives on the volume, so this runs once. Keys never do: they stay
# on the host, behind the agent.
#
# ~/.ssh/config gets a managed block pointing every ssh at the agent relay
# (container-init in the Dockerfile). IdentityAgent overrides SSH_AUTH_SOCK
# (ssh_config(5)), which matters because IDEs inject their own forwarded agent
# socket into the processes they start — VS Code does, and it cannot be turned
# off (microsoft/vscode-remote-release#11413) — and that socket follows the
# IDE's host environment, which may point at an agent without your keys.
configure_ssh() {
  local known="${HOME}/.ssh/known_hosts" config="${HOME}/.ssh/config" tmp
  mkdir -p "${HOME}/.ssh" && chmod 700 "${HOME}/.ssh"
  touch "${known}" && chmod 600 "${known}"
  if ! ssh-keygen -F github.com -f "${known}" >/dev/null; then
    ssh-keyscan -t ed25519,ecdsa,rsa github.com 2>/dev/null >> "${known}" || return 1
    echo "    added github.com to ~/.ssh/known_hosts"
  fi

  touch "${config}" && chmod 600 "${config}"
  tmp="$(mktemp)"
  # The managed block goes FIRST: ssh uses the first value it finds per option.
  {
    echo "${BASHRC_BEGIN} (managed by /src/setup.sh; edits inside are overwritten)"
    echo "Host *"
    echo "    IdentityAgent /tmp/ssh-agent.sock"
    echo "${BASHRC_END}"
    awk -v begin="${BASHRC_BEGIN}" -v end="${BASHRC_END}" '
      index($0, begin) == 1 { skip = 1 }
      !skip { print }
      index($0, end) == 1 { skip = 0 }
    ' "${config}"
  } > "${tmp}"
  if cmp -s "${tmp}" "${config}"; then
    rm -f "${tmp}"
  else
    mv "${tmp}" "${config}" && chmod 600 "${config}"
    echo "    ~/.ssh/config: every ssh uses the agent relay"
  fi
}

# Forks from the root .env (INATRACE_FORK_<REPO>=owner/name), passed in by
# initialize.sh. Parsed, never sourced. Prints the fork's SSH URL, if any.
fork_url() { # fork_url <target-dir>
  local key="${1^^}" line
  key="INATRACE_FORK_${key//[^A-Z0-9]/_}"
  [ -r "${FORKS_FILE}" ] || return 0
  while IFS= read -r line; do
    [ "${line%%=*}" = "${key}" ] && printf 'git@github.com:%s.git' "${line#*=}"
  done < "${FORKS_FILE}"
  return 0
}

# With a fork: origin = the fork, upstream = the repos.txt URL. Pushing to
# upstream is disabled (pushurl), and a bare `git push` goes to the fork
# (remote.pushDefault) even from branches that track upstream.
set_upstream() { # set_upstream <dest> <upstream-url>
  git -C "$1" config --get remote.upstream.url > /dev/null \
    || git -C "$1" remote add upstream "$2" || return 1
  git -C "$1" config remote.upstream.pushurl DISABLED
  git -C "$1" config remote.pushDefault origin
}

# Clones missing repos, fetches existing ones. Never merges or pulls, so local
# branches stay as you left them. Remotes are only changed while origin is still
# exactly what setup.sh would have set; anything else is yours and left alone.
clone_repos() {
  local rc=0 line url target dest fork origin
  [ -f "${REPOS_FILE}" ] || { echo "    ${REPOS_FILE} not found" >&2; return 1; }
  while IFS= read -r line || [ -n "${line}" ]; do
    line="$(printf '%s' "${line}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "${line}" ] && continue
    case "${line}" in \#*) continue ;; esac
    url="$(printf '%s' "${line}" | awk '{print $1}')"
    target="$(printf '%s' "${line}" | awk '{print $2}')"
    if [ -z "${target}" ]; then
      target="$(basename "${url}")"
      target="${target%.git}"
    fi
    dest="${SRC_DIR}/${target}"
    fork="$(fork_url "${target}")"
    if [ -d "${dest}/.git" ]; then
      origin="$(git -C "${dest}" config --get remote.origin.url)"
      if [ -n "${fork}" ] && [ "${origin}" = "${url}" ]; then
        echo "    ${target}: origin -> fork, upstream -> ${url}"
        { git -C "${dest}" remote rename origin upstream \
            && git -C "${dest}" remote add origin "${fork}" \
            && set_upstream "${dest}" "${url}"; } || rc=1
      elif [ -n "${fork}" ] && [ "${origin}" = "${fork}" ]; then
        set_upstream "${dest}" "${url}" || rc=1
      elif [ -n "${fork}" ]; then
        echo "    ${target}: origin is ${origin}, not the fork — left as is" >&2
      fi
      git -C "${dest}" fetch --all --prune --quiet || { echo "    fetch failed: ${target}" >&2; rc=1; }
    elif [ -e "${dest}" ]; then
      echo "    ${target} exists but is not a git repo — skipped" >&2; rc=1
    elif [ -n "${fork}" ]; then
      echo "    cloning ${target} from the fork ${fork}"
      { git clone --quiet "${fork}" "${dest}" \
          && set_upstream "${dest}" "${url}" \
          && git -C "${dest}" fetch --quiet upstream; } || rc=1
    else
      echo "    cloning ${target}"
      git clone --quiet "${url}" "${dest}" || rc=1
    fi
  done < "${REPOS_FILE}"
  return "${rc}"
}

# nvm and Node 14 in $HOME. The system Node from the image stays the default;
# the frontend opts in with `nvm use 14`.
#
# The body runs in a SUBSHELL ( … ): sourcing nvm.sh and `nvm install` switch
# the current shell's PATH to Node 14, which would leak into the later steps —
# the Playwright CLI, run by the system Node, refuses Node < 20.
install_node() (
  export NVM_DIR="${NVM_DIR:-${HOME}/.nvm}"
  local current=""
  # nvm is not written for `set -u`.
  set +u
  # shellcheck disable=SC1091
  [ -s "${NVM_DIR}/nvm.sh" ] && . "${NVM_DIR}/nvm.sh" && current="v$(nvm --version)"
  # Install, or upgrade in place to the declared version (install.sh does both).
  if [ "${current}" != "${NVM_VERSION}" ]; then
    mkdir -p "${NVM_DIR}"
    curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" \
      | PROFILE=/dev/null bash >/dev/null 2>&1 || return 1
    echo "    nvm ${current:-not installed} -> ${NVM_VERSION}"
  fi
  # shellcheck disable=SC1091
  . "${NVM_DIR}/nvm.sh" || return 1
  if ! nvm ls "${NODE_FRONTEND}" >/dev/null 2>&1; then
    nvm install "${NODE_FRONTEND}" >/dev/null 2>&1 || return 1
    echo "    installed Node ${NODE_FRONTEND}"
  fi
  [ "$(cat "${NVM_DIR}/alias/default" 2>/dev/null)" = "system" ] \
    || nvm alias default system >/dev/null
)

# Claude Code itself (native installer, ~/.local), then defaults that are only
# SET IF ABSENT, so your own changes survive re-runs.
install_claude() {
  if ! command -v claude >/dev/null; then
    curl -fsSL https://claude.ai/install.sh | bash >/dev/null || return 1
    echo "    installed Claude Code"
  fi
  mkdir -p "${CLAUDE_CONFIG_DIR}"
  python3 - "${CLAUDE_CONFIG_DIR}" <<'PY'
import json, os, sys

config_dir = sys.argv[1]

def update(name, defaults, mode=None):
    path = os.path.join(config_dir, name)
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    before = json.dumps(data, sort_keys=True)
    for key, value in defaults.items():
        if isinstance(value, dict):
            data.setdefault(key, {})
            for k, v in value.items():
                data[key].setdefault(k, v)
        else:
            data.setdefault(key, value)
    if json.dumps(data, sort_keys=True) == before:
        return
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    if mode:
        os.chmod(tmp, mode)
    os.replace(tmp, path)
    print(f"    updated {name}")

# The container is the sandbox: Claude runs without permission prompts. Voice
# input is on; it needs the (best-effort) microphone bridge.
update("settings.json", {
    "permissions": {"defaultMode": "bypassPermissions",
                    "skipDangerousModePermissionPrompt": True},
    "voice": {"enabled": True, "mode": "hold"},
})
# Without the flag a fresh volume replays the onboarding wizard, which looks
# like being asked to log in again. .claude.json holds the account: owner-only.
update(".claude.json", {"hasCompletedOnboarding": True}, mode=0o600)
PY
}

# The Chromium build matching the global Playwright in the image (a no-op when
# already installed), the MCP config, and its registration with Claude.
setup_playwright() {
  local config="${HOME}/.config/playwright-mcp.json" headless="${PLAYWRIGHT_HEADLESS:-true}" args tmp
  playwright install chromium >/dev/null || return 1

  # --no-sandbox: Chrome's sandbox needs privileges the container lacks.
  # --disable-dev-shm-usage: Docker's 64 MB /dev/shm is too small for Chromium.
  # Headed mode renders on the host's Wayland compositor (software only; the
  # D-Bus/DRM errors Chromium logs at startup are noise).
  args='"--no-sandbox", "--disable-dev-shm-usage"'
  if [ "${headless}" != "true" ]; then
    args="${args}, \"--ozone-platform=wayland\", \"--enable-features=UseOzonePlatform\""
  fi
  mkdir -p "$(dirname "${config}")"
  tmp="$(mktemp)"
  cat > "${tmp}" <<EOF
{
  "browser": {
    "browserName": "chromium",
    "launchOptions": {
      "headless": ${headless},
      "args": [${args}]
    },
    "contextOptions": {
      "viewport": { "width": 1280, "height": 820 }
    }
  },
  "outputDir": "${HOME}/.cache/playwright-mcp/output"
}
EOF
  if cmp -s "${tmp}" "${config}"; then
    rm -f "${tmp}"
  else
    mv "${tmp}" "${config}"
    echo "    wrote ${config} (headless: ${headless})"
  fi

  if ! claude mcp get playwright >/dev/null 2>&1; then
    claude mcp add -s user playwright -- playwright-mcp --config "${config}" >/dev/null || return 1
    echo "    registered the playwright MCP server with Claude"
  fi

  setup_chrome_launcher
}

# ~/.local/bin/chrome: the same Chromium for other tools, such as the frontend's
# Karma tests (CHROME_BIN in ~/.bashrc points here). It adds the container flags and,
# with a window, uses the host's Wayland: the X11 display VS Code forwards needs
# credentials Chromium does not have.
setup_chrome_launcher() {
  local launcher="${HOME}/.local/bin/chrome" tmp
  mkdir -p "$(dirname "${launcher}")"
  tmp="$(mktemp)"
  cat > "${tmp}" <<'EOF'
#!/bin/sh
# Playwright's Chromium with the flags this container needs (managed by /src/setup.sh).
chrome="$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1)"
[ -n "$chrome" ] || { echo "Playwright's Chromium is missing; run /src/setup.sh" >&2; exit 1; }
case " $* " in
  *" --headless"*) ;;
  *) [ -n "$WAYLAND_DISPLAY" ] && set -- --ozone-platform=wayland "$@" ;;
esac
exec "$chrome" --no-sandbox --disable-dev-shm-usage "$@"
EOF
  chmod 755 "${tmp}"
  if cmp -s "${tmp}" "${launcher}"; then
    rm -f "${tmp}"
  else
    mv "${tmp}" "${launcher}"
    echo "    wrote ${launcher}"
  fi
}

# The dev gateway (src/gateway): a nested nginx container routing / and /api.
# `up -d` is a no-op when it is already running with the current config.
start_gateway() {
  docker info >/dev/null 2>&1 || { echo "    docker is not answering" >&2; return 1; }
  docker compose -f "${SRC_DIR}/gateway/compose.yaml" up -d --quiet-pull 2>&1 \
    | grep -v -e 'Running' -e '^$' | sed 's/^/    /'
  return "${PIPESTATUS[0]}"
}

# --- Report -------------------------------------------------------------------

check() { # check "<label>" <command...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '  %-12s OK\n' "${label}"
  else
    printf '  %-12s UNAVAILABLE\n' "${label}"
  fi
}

ssh_agent_usable() {
  # ssh-add -l: 0 = keys listed, 1 = agent reachable but empty, 2 = no agent.
  ssh-add -l >/dev/null 2>&1
  [ $? -ne 2 ]
}

node_frontend_available() (
  set +u
  . "${NVM_DIR}/nvm.sh" && nvm ls "${NODE_FRONTEND}"
)

report() {
  echo
  echo "──────────────── environment ────────────────"
  check "docker"      docker info
  check "gateway"     sh -c 'docker compose -f "$1" ps --status running -q | grep -q .' _ "${SRC_DIR}/gateway/compose.yaml"
  check "java 17"     java -version
  check "maven"       mvn -v
  check "node ${NODE_FRONTEND}"     node_frontend_available
  check "claude"      claude --version
  check "ssh agent"   ssh_agent_usable
  check "gh (token)"  gh auth status
  check "audio"       pactl info
  check "wayland"     test -S "${WAYLAND_DISPLAY:-/nonexistent}"
  printf '  %-12s %s\n' "browser" "$([ "${PLAYWRIGHT_HEADLESS:-true}" = "true" ] && echo headless || echo headed)"
  echo "─────────────────────────────────────────────"
  echo "UNAVAILABLE is expected for optional features the host does not provide"
  echo "(token, agent, audio, wayland). See README."
}

# --- Main ---------------------------------------------------------------------

step "home directory ownership"   fix_home_ownership
step "shell"                      configure_shell
step "git"                        configure_git
step "ssh"                        configure_ssh
step "repositories"               clone_repos
step "node ${NODE_FRONTEND} (nvm)"  install_node
step "claude code"                install_claude
step "playwright mcp"             setup_playwright
step "dev gateway"                start_gateway

report

if [ ${#failed[@]} -gt 0 ]; then
  echo
  echo "Steps that failed (re-run ./setup.sh after fixing):"
  printf '  - %s\n' "${failed[@]}"
fi

# Never fail the container create: every step above is recoverable by re-running.
exit 0

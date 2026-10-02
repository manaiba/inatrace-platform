#!/usr/bin/env bash
#
# initialize.sh — runs on the HOST (devcontainer.json `initializeCommand`),
# before the Compose stack is created.
#
# Resolves every host path that compose.yaml bind-mounts and writes them to
# .devcontainer/.env. Anything optional the host lacks (a git config, the
# GitHub token, an SSH agent, a desktop session) is replaced by an empty
# placeholder under .devcontainer/.state/, so:
#
#   * the mount stays valid and the container starts without that feature,
#     instead of failing;
#   * Docker never creates a root-owned DIRECTORY in place of a missing source
#     (which it does silently, shadowing the file or socket for good);
#   * nothing is created in your home or runtime directory on the host.
#
# It also keeps an EXISTING container in sync. `devcontainer up` reuses a
# container that already exists (`compose up --no-recreate`), with the mounts
# and ports it was created with. Sockets are mounted through stable directories,
# so session changes never require that — but when a value in .env does change
# (the ports, or a host that gained a desktop session), the old container is
# removed and this same `up` creates a fresh one. Nothing is lost: $HOME and the
# nested Docker live in volumes, /src is a bind mount, and setup.sh is idempotent.
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
state="${here}/.state"
mkdir -p "${state}"

placeholder() {
  local path="${state}/$1"
  [ -e "${path}" ] || : > "${path}"
  printf '%s' "${path}"
}
placeholder_dir() {
  local path="${state}/$1.d"
  mkdir -p "${path}"
  printf '%s' "${path}"
}
note() { echo "initialize.sh: $*" >&2; }

# --- User settings: the repo root's .env (git-ignored) ------------------------
# KEY=VALUE lines, see .env.example. Parsed, never sourced, and the ONLY source
# of these settings: variables exported in the host shell are ignored.
user_env="$(dirname "${here}")/.env"
declare -A cfg=()
if [ -f "${user_env}" ]; then
  while IFS= read -r line || [ -n "${line}" ]; do
    line="${line%$'\r'}"
    case "${line}" in ''|\#*) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    case "${value}" in \"*\"|\'*\') value="${value:1:${#value}-2}" ;; esac
    case "${key}" in
      INATRACE_BIND_ADDRESS|INATRACE_GATEWAY_PORT|INATRACE_BACKEND_PORT|INATRACE_FRONTEND_PORT|\
      INATRACE_GH_TOKEN|PLAYWRIGHT_HEADLESS|INATRACE_FORK_*)
        cfg["${key}"]="${value}" ;;
      *) note ".env: unknown setting '${key}' — ignored (see .env.example)." ;;
    esac
  done < "${user_env}"
fi
setting() { printf '%s' "${cfg[$1]:-$2}"; } # setting KEY default

# --- Git identity -------------------------------------------------------------
gitconfig="${HOME}/.gitconfig"
if [ ! -f "${gitconfig}" ]; then
  gitconfig="$(placeholder gitconfig)"
  note "no ~/.gitconfig on the host — set user.name/user.email inside the container."
fi

# --- GitHub token (optional) --------------------------------------------------
# Not needed to clone or push: git talks to GitHub over SSH, through the agent.
# Provide one to use `gh` or push over HTTPS.
#
# INATRACE_GH_TOKEN goes into a file the container mounts, never into its
# environment (visible to `docker inspect` and every process). The file is
# rewritten IN PLACE: a single-file bind mount is pinned to its inode, so a new
# token reaches a running container without recreating it.
gh_token="$(placeholder gh_token)"
chmod 600 "${gh_token}"
printf '%s' "$(setting INATRACE_GH_TOKEN '')" > "${gh_token}"
if [ -s "${gh_token}" ] && [ -n "$(find "${user_env}" -perm /044 2>/dev/null)" ]; then
  note ".env holds a token but is readable by others — chmod 600 .env"
fi

# --- Forks (optional) ---------------------------------------------------------
# INATRACE_FORK_<REPO>=owner/name makes your fork the `origin` of that repo and
# the repos.txt URL its `upstream` (see setup.sh). <REPO> is the repo's directory
# under src/, upper-cased, with every other character than A-Z/0-9 as "_":
# inatrace-backend -> INATRACE_FORK_INATRACE_BACKEND. Only these lines reach the
# container, as a file rewritten in place (same reason as the token).
fork_key() { local k="${1^^}"; printf 'INATRACE_FORK_%s' "${k//[^A-Z0-9]/_}"; }
declare -A repo_keys=()
while read -r url target _; do
  case "${url}" in ''|\#*) continue ;; esac
  [ -n "${target:-}" ] || { target="$(basename "${url}")"; target="${target%.git}"; }
  repo_keys["$(fork_key "${target}")"]=1
done < "$(dirname "${here}")/src/repos.txt"
forks="$(placeholder forks.env)"
for key in "${!cfg[@]}"; do
  case "${key}" in INATRACE_FORK_*) ;; *) continue ;; esac
  if [ -z "${repo_keys[${key}]:-}" ]; then
    note ".env: ${key} matches no repo in src/repos.txt — ignored."
  elif ! [[ "${cfg[${key}]}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    note ".env: ${key}=${cfg[${key}]} is not owner/name — ignored."
  else
    printf '%s=%s\n' "${key}" "${cfg[${key}]}"
  fi
done | sort > "${forks}"

# --- SSH agent ----------------------------------------------------------------
# The DIRECTORY is mounted, not the socket: OpenSSH >= 10.1 names the socket
# ~/.ssh/agent/s.<random>, a new one every session. Agents with a fixed socket in
# $XDG_RUNTIME_DIR (systemd units, GNOME, gpg-agent) come in with the runtime dir
# below. Inside the container, a relay on SSH_AUTH_SOCK finds the live one.
ssh_agent_dir="${HOME}/.ssh/agent"
if [ ! -d "${ssh_agent_dir}" ]; then
  ssh_agent_dir="$(placeholder_dir ssh-agent)"
fi
case "${SSH_AUTH_SOCK:-}" in
  "") note "no SSH agent (SSH_AUTH_SOCK) — setup.sh cannot clone the repos over SSH. See README → Prerequisites." ;;
  "${HOME}/.ssh/agent/"*|"${XDG_RUNTIME_DIR:-/nonexistent}/"*) ;;
  *) note "SSH_AUTH_SOCK=${SSH_AUTH_SOCK} is outside ~/.ssh/agent and \$XDG_RUNTIME_DIR;" \
          "the container will not see it. See README → Credentials." ;;
esac

# --- Desktop session (best-effort) --------------------------------------------
# The whole runtime dir is mounted, so sockets recreated by a new login (Wayland,
# PipeWire/PulseAudio, agents) show up in the container without recreating it.
runtime_dir="${XDG_RUNTIME_DIR:-}"
if [ -z "${runtime_dir}" ] || [ ! -d "${runtime_dir}" ]; then
  runtime_dir="$(placeholder_dir runtime)"
  note "no XDG_RUNTIME_DIR — no microphone, clipboard or headed browser."
elif [ ! -S "${runtime_dir}/pulse/native" ]; then
  note "no PulseAudio/PipeWire socket — no microphone for Claude's /voice."
fi

wayland_display="${WAYLAND_DISPLAY:-wayland-0}"
wayland_display="${wayland_display##*/}"
headless="$(setting PLAYWRIGHT_HEADLESS '')"
if [ -S "${runtime_dir}/${wayland_display}" ]; then
  headless="${headless:-false}"
else
  headless="${headless:-true}"
  note "no Wayland session — no clipboard paste; the Playwright browser runs headless."
fi

# --- .env consumed by compose.yaml (generated; do not edit by hand) -----------
env_file="${here}/.env"
new_env="$(mktemp)"
trap 'rm -f "${new_env}"' EXIT
{
  printf '# Generated by .devcontainer/initialize.sh — do not edit.\n'
  printf 'HOST_GITCONFIG=%s\n' "${gitconfig}"
  printf 'HOST_GH_TOKEN=%s\n' "${gh_token}"
  printf 'HOST_FORKS=%s\n' "${forks}"
  printf 'HOST_SSH_AGENT_DIR=%s\n' "${ssh_agent_dir}"
  printf 'HOST_RUNTIME_DIR=%s\n' "${runtime_dir}"
  printf 'HOST_WAYLAND_DISPLAY=%s\n' "${wayland_display}"
  printf 'PLAYWRIGHT_HEADLESS=%s\n' "${headless}"
  printf 'INATRACE_BIND_ADDRESS=%s\n' "$(setting INATRACE_BIND_ADDRESS 127.0.0.1)"
  printf 'INATRACE_GATEWAY_PORT=%s\n' "$(setting INATRACE_GATEWAY_PORT 8000)"
  printf 'INATRACE_BACKEND_PORT=%s\n' "$(setting INATRACE_BACKEND_PORT 9000)"
  printf 'INATRACE_FRONTEND_PORT=%s\n' "$(setting INATRACE_FRONTEND_PORT 9080)"
} > "${new_env}"

# --- Recreate the container if the host side changed --------------------------
if [ -f "${env_file}" ] && ! cmp -s "${new_env}" "${env_file}"; then
  project="$(awk '/^name:[[:space:]]/ { print $2; exit }' "${here}/compose.yaml")"
  stale="$(docker ps -aq \
    --filter "label=com.docker.compose.project=${project}" \
    --filter "label=com.docker.compose.service=app" 2>/dev/null || true)"
  if [ -n "${stale}" ]; then
    note "host paths or ports changed since the container was created:"
    diff <(grep -v '^#' "${env_file}") <(grep -v '^#' "${new_env}") \
      | sed -n 's/^> /    now: /p' >&2 || true
    note "removing the old container so this 'up' recreates it (volumes are kept)."
    # shellcheck disable=SC2086
    docker rm -f ${stale} > /dev/null
  fi
fi

mv "${new_env}" "${env_file}"
trap - EXIT

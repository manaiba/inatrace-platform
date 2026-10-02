#!/usr/bin/env bash
#
# cleanup.sh — runs on the HOST. Two jobs:
#
# 1. (default) Stop dev containers this project left behind.
#
#    Every dev container ever created for this folder is labelled
#    `devcontainer.local_folder=<repo root>`, whatever the devcontainer.json of
#    the day looked like. The containers of the CURRENT stack carry a second
#    label, `com.docker.compose.project=<name from compose.yaml>`, because they
#    are created by Compose. Anything with the first label but not the second is
#    therefore an instance from an older configuration.
#
#    Those leftovers matter because they keep their published ports, and the new
#    stack then fails with "port is already allocated". Stopping is the default
#    and is reversible (`docker start <name>`); --remove deletes them too. That
#    is safe for the data this project cares about — /src is a host bind mount
#    and the named volumes outlive any container — but it discards each
#    container's writable layer. Images and volumes are not touched.
#
# 2. (--purge) Delete EVERYTHING the dev container owns, to start from scratch:
#    every container of this project (the current one included), its named
#    volumes — your $HOME (Claude login, caches, nvm, known_hosts…) and the
#    nested Docker (images, containers, databases) —, its images and the files
#    initialize.sh generates. The cloned repos under src/ are NOT touched: they
#    are your work. Asks for confirmation unless --yes.
#
#   ./cleanup.sh                  stop stale containers
#   ./cleanup.sh --remove         stop AND delete them
#   ./cleanup.sh --purge          delete everything the dev container owns
#   ./cleanup.sh --dry-run ...    list what would happen, change nothing
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "${here}")"

usage() {
  cat <<'EOF'
usage: cleanup.sh [--remove | --purge [--yes]] [--dry-run]

  -r, --remove    delete the stale containers after stopping them
  -p, --purge     delete ALL containers, volumes, images and generated files of
                  this dev container (the cloned repos under src/ are kept)
  -y, --yes       with --purge: do not ask for confirmation
  -n, --dry-run   only report what would be done
  -h, --help      this message
EOF
}

remove=0
purge=0
assume_yes=0
dry_run=0
while [ $# -gt 0 ]; do
  case "$1" in
    -r|--remove)  remove=1 ;;
    -p|--purge)   purge=1 ;;
    -y|--yes)     assume_yes=1 ;;
    -n|--dry-run) dry_run=1 ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "cleanup.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if ! docker info >/dev/null 2>&1; then
  echo "cleanup.sh: cannot talk to the Docker daemon — is it running?" >&2
  exit 1
fi

# The Compose project name, read from compose.yaml rather than hard-coded here,
# so renaming the stack in one place does not silently make this script treat
# the live containers as stale. `^name:` is anchored to column 0 on purpose:
# the `name:` keys under `volumes:` are indented and must not match.
project="$(awk '/^name:[[:space:]]/ { print $2; exit }' "${here}/compose.yaml")"
: "${project:=inatrace-platform}"

# --- --purge: delete everything the dev container owns ---------------------
if [ "${purge}" -eq 1 ]; then
  mapfile -t containers < <(
    { docker ps -aq --filter "label=com.docker.compose.project=${project}"
      docker ps -aq --filter "label=devcontainer.local_folder=${root}"; } | sort -u
  )
  # Named volumes: the `name:` keys under the top-level `volumes:` of compose.yaml.
  mapfile -t volumes < <(
    awk '/^volumes:/ { v = 1; next } /^[^[:space:]#]/ { v = 0 }
         v && $1 == "name:" { print $2 }' "${here}/compose.yaml" \
      | while read -r v; do docker volume inspect "${v}" >/dev/null 2>&1 && echo "${v}"; done
  )
  # The image Compose builds (<project>-app) and the ones the devcontainer
  # tooling derives from it (e.g. vsc-<project>-…-uid).
  mapfile -t images < <(
    docker images --format '{{.Repository}}:{{.Tag}}' | grep -F "${project}" || true
  )
  generated=()
  for f in "${here}/.env" "${here}/.state"; do
    [ -e "${f}" ] && generated+=("${f}")
  done

  echo "cleanup.sh: --purge will delete:"
  for c in ${containers[@]+"${containers[@]}"}; do
    printf '  container  %s\n' "$(docker inspect "${c}" --format '{{.Name}}' | sed 's|^/||')"
  done
  for v in ${volumes[@]+"${volumes[@]}"};     do printf '  volume     %s\n' "${v}"; done
  for i in ${images[@]+"${images[@]}"};       do printf '  image      %s\n' "${i}"; done
  for f in ${generated[@]+"${generated[@]}"}; do printf '  file       %s\n' "${f}"; done
  echo "  (kept: the cloned repos under ${root}/src)"

  if [ "${dry_run}" -eq 1 ]; then
    echo "cleanup.sh: --dry-run, nothing deleted."
    exit 0
  fi
  if [ "${assume_yes}" -ne 1 ]; then
    printf 'Type the project name (%s) to confirm: ' "${project}"
    read -r answer
    [ "${answer}" = "${project}" ] || { echo "cleanup.sh: aborted."; exit 1; }
  fi

  [ ${#containers[@]} -gt 0 ] && docker rm -f "${containers[@]}" >/dev/null
  [ ${#volumes[@]} -gt 0 ] && docker volume rm "${volumes[@]}" >/dev/null
  [ ${#images[@]} -gt 0 ] && docker image rm "${images[@]}" >/dev/null
  [ ${#generated[@]} -gt 0 ] && rm -rf "${generated[@]}"
  echo "cleanup.sh: purged. The next \`devcontainer up\` starts from scratch."
  exit 0
fi

# --- Select the leftovers -----------------------------------------------
# --filter matches the label value exactly, so a checkout of this repo at a
# different path (a git worktree, a second clone) is left alone.
mapfile -t candidates < <(
  docker ps -aq --filter "label=devcontainer.local_folder=${root}"
)

stale=()
for id in "${candidates[@]}"; do
  belongs_to="$(docker inspect "${id}" \
    --format '{{index .Config.Labels "com.docker.compose.project"}}')"
  [ "${belongs_to}" = "${project}" ] || stale+=("${id}")
done

if [ ${#stale[@]} -eq 0 ]; then
  echo "cleanup.sh: no stale dev containers for ${root}."
else
  echo "cleanup.sh: stale dev containers for ${root}:"
  for id in "${stale[@]}"; do
    # .Name carries a leading slash; PortBindings is empty for a stopped
    # container, which is exactly the state we are trying to reach.
    name="$(docker inspect "${id}" --format '{{.Name}}')"
    status="$(docker inspect "${id}" --format '{{.State.Status}}')"
    ports="$(docker inspect "${id}" \
      --format '{{range $p, $_ := .HostConfig.PortBindings}}{{$p}} {{end}}')"
    printf '  %-24s %-10s %s\n' "${name#/}" "${status}" "${ports:-(no published ports)}"
  done

  if [ "${dry_run}" -eq 1 ]; then
    echo "cleanup.sh: --dry-run, nothing stopped."
  else
    docker stop "${stale[@]}" >/dev/null
    echo "cleanup.sh: stopped ${#stale[@]} container(s)."
    if [ "${remove}" -eq 1 ]; then
      docker rm "${stale[@]}" >/dev/null
      echo "cleanup.sh: removed ${#stale[@]} container(s)."
    else
      echo "cleanup.sh: run with --remove to delete them, or restart one with" \
           "\`docker start <name>\`."
    fi
  fi
fi

# --- Warn about ports held by containers from OTHER projects ------------
# Out of scope to touch — they belong to somebody else's stack — but they
# produce the identical "port is already allocated" failure, so naming them
# here saves the next round of detective work.
# The host ports this stack publishes, from the .env initialize.sh writes.
mapfile -t wanted < <(
  grep -E '^INATRACE_[A-Z]+_PORT=' "${here}/.env" 2>/dev/null | cut -d= -f2 | sort -un
)

mapfile -t ours < <(
  docker ps --filter "label=com.docker.compose.project=${project}" --format '{{.Names}}'
)

conflicts=()
while IFS='|' read -r name portmap; do
  for own in ${ours[@]+"${ours[@]}"}; do
    [ "${name}" = "${own}" ] && continue 2
  done
  for port in ${wanted[@]+"${wanted[@]}"}; do
    case "${portmap}" in
      *":${port}->"*) conflicts+=("${name} holds ${port}") ;;
    esac
  done
done < <(docker ps --format '{{.Names}}|{{.Ports}}')

if [ ${#conflicts[@]} -gt 0 ]; then
  echo "cleanup.sh: WARNING — ports this stack publishes are taken by containers" \
       "from other projects:" >&2
  printf '  %s\n' "${conflicts[@]}" >&2
  echo "cleanup.sh: stop them by hand if the stack fails to start." >&2
fi

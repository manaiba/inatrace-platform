#!/bin/sh
# What an INATrace server needs, on Debian, Ubuntu and their derivatives: Docker Engine
# with its Compose plugin, from Docker's repository (the distribution's own packages are
# often too old), rsync and cron (the scheduled backup). Run it there as the user that
# deploys, who must be able to use sudo; `inatrace deploy prepare <name>` does, or by hand:
#
#   scp deploy/prereqs/debian.sh <server>: && ssh -t <server> sh debian.sh
#
# Safe to run again. Log out and in afterwards: the docker group applies to new sessions.
set -eu
step() { printf '    · %s\n' "$1"; }
# apt's own output only when it fails. A server just created is often updating itself
# (unattended-upgrades), holding apt's lock for a few minutes: then say so. No terminal on
# its input: inside $(...) it is not the terminal's foreground job, and touching the
# terminal would stop it for good (sudo still asks its password on /dev/tty).
quiet_apt() {
    if ! log=$(sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 \
            apt-get -qq -o=Dpkg::Use-Pty=0 "$@" </dev/null 2>&1); then
        case "$log" in
            *"Could not get lock"*|*"Unable to acquire"*|*"Unable to lock"*)
                echo "apt is busy: something else is installing (a server just created often" \
                     "updates itself for a few minutes). Try again in a while." >&2 ;;
            *) printf '%s\n' "$log" >&2 ;;
        esac
        return 1
    fi
}

step "finishing any install left halfway"
sudo DEBIAN_FRONTEND=noninteractive dpkg --configure -a </dev/null >/dev/null
step "updating the package lists"
quiet_apt update
step "installing ca-certificates, curl, rsync and cron"
quiet_apt install -y ca-certificates curl rsync cron

# Docker publishes for debian and ubuntu: a derivative uses its base, and the Ubuntu
# release it is built on (UBUNTU_CODENAME) when it says.
. /etc/os-release
case "$ID ${ID_LIKE:-}" in
    ubuntu*|*" ubuntu"*) base=ubuntu ;;
    *) base=debian ;;
esac
release=${UBUNTU_CODENAME:-$VERSION_CODENAME}
step "adding Docker's repository ($base $release)"
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL "https://download.docker.com/linux/$base/gpg" -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$base $release stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
quiet_apt update
step "installing Docker Engine and its Compose plugin"
quiet_apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
step "letting $(id -un) run docker"
sudo usermod -aG docker "$(id -un)"

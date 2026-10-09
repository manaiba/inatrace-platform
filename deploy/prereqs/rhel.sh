#!/bin/sh
# What an INATrace server needs, on Red Hat Enterprise Linux and its rebuilds (Rocky Linux,
# AlmaLinux, CentOS Stream): Docker Engine with its Compose plugin, from Docker's repository
# (these distributions ship Podman instead), rsync and cron (the scheduled backup). Run it
# there as the user that deploys, who must be able to use sudo; `inatrace deploy prepare
# <name>` does, or by hand:
#
#   scp deploy/prereqs/rhel.sh <server>: && ssh -t <server> sh rhel.sh
#
# Safe to run again. Log out and in afterwards: the docker group applies to new sessions.
# SELinux stays as it is: Docker Engine runs its containers without SELinux labels unless
# told to, so the mounted files need none. With firewalld running, it opens HTTP and HTTPS.
set -eu
step() { printf '    · %s\n' "$1"; }
# dnf's own output only when it fails. dnf waits for another dnf rather than fail (a server
# just created often refreshes its metadata): say so instead of waiting. No terminal on its
# input: inside $(...) it is not the terminal's foreground job (sudo still asks its
# password on /dev/tty).
quiet_dnf() {
    if pgrep -x dnf >/dev/null || pgrep -x dnf5 >/dev/null; then
        echo "dnf is busy: something else is installing (a server just created often" \
             "updates itself for a few minutes). Try again in a while." >&2
        return 1
    fi
    if ! log=$(sudo dnf -y -q "$@" </dev/null 2>&1); then
        printf '%s\n' "$log" >&2
        return 1
    fi
}

step "installing ca-certificates, rsync and cron"
quiet_dnf install ca-certificates rsync cronie
# curl-minimal, already there, is enough; the full curl would conflict with it.
command -v curl >/dev/null || quiet_dnf install curl-minimal
sudo systemctl enable --now crond </dev/null >/dev/null 2>&1

# Docker's network rules need iptables' addrtype match, whose module is in
# kernel-modules-extra, which minimal and cloud images leave out. The one for the running
# kernel: another version only works after a reboot into its kernel.
if ! modinfo xt_addrtype >/dev/null 2>&1; then
    step "installing the running kernel's extra modules (Docker's network rules need them)"
    if ! quiet_dnf install "kernel-modules-extra-$(uname -r)"; then
        echo "the extra modules of the running kernel ($(uname -r)) are no longer in the repositories:" \
             "update and reboot into the newest kernel (sudo dnf update -y && sudo reboot), then run this again" >&2
        exit 1
    fi
fi

# Docker publishes one repository for RHEL and its rebuilds, by major version.
step "adding Docker's repository (rhel $(. /etc/os-release; echo "${VERSION_ID%%.*}"))"
sudo curl -fsSL https://download.docker.com/linux/rhel/docker-ce.repo -o /etc/yum.repos.d/docker-ce.repo
step "installing Docker Engine and its Compose plugin"
quiet_dnf install docker-ce docker-ce-cli containerd.io docker-compose-plugin
step "starting Docker"
sudo systemctl reset-failed docker.service </dev/null >/dev/null 2>&1 || true
if ! sudo systemctl enable --now docker </dev/null >/dev/null 2>&1; then
    echo "Docker did not start; the end of its log:" >&2
    sudo journalctl -u docker --no-pager -n 5 -o cat </dev/null >&2
    exit 1
fi
if systemctl is-active --quiet firewalld; then
    step "opening HTTP and HTTPS in firewalld"
    sudo firewall-cmd --quiet --permanent --add-service=http --add-service=https
    sudo firewall-cmd --quiet --reload
fi
step "letting $(id -un) run docker"
sudo usermod -aG docker "$(id -un)"

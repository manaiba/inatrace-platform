# Premises

Settled choices the platform is built on, and what they rule out. Revisit one only with a new
reason.

- **Linux only.** The gateway runs with `network_mode: host`, so an image publishes on the port
  its checkout would use and the gateway config never changes per mode. Not macOS/Windows: use a
  Linux VM.
- **The platform works without the dev container.** `bin/inatrace` is the same everywhere;
  `.devcontainer/` holds only what the container needs, in standard-library Python, because
  `initialize.py` runs on the host before anything is installed.
- **uv only**, no poetry or poe: one binary, no install step, brings Python; the CLI is the task
  runner.
- **One `.env` at the root**, the only source of settings. Shell variables are ignored, so a
  stray export never changes the setup.
- **SSH keys stay on the host.** The container reaches the host agent through a relay, and
  `IdentityAgent` pins it, because IDEs inject their own `SSH_AUTH_SOCK`.
- **`repos sync` never pulls or merges.** It clones and fetches; branches are the developer's.
- **Smoke tests run against the stack that is up**, in its mode, not a throwaway one. Checks that
  need an image skip otherwise; what they create is named `smoke-…` and cleaned up best effort.
- **Deploys: we ship the scheme, operators decide.** Image versions, registry, domain, mail and
  what sits in front are theirs; nothing picks a version or updates on its own. One server over
  ssh with Docker Compose; the CLI only runs what the docs show by hand, and the server needs
  nothing from us but Docker, rsync and cron. A profile per distribution family installs them
  (`deploy/prereqs/`: Debian and Ubuntu, the RHEL family): the common case is automated, the
  rest stays in the docs.
- **The edge always serves HTTPS and names the client itself.** Caddy (certificates without
  extra tools) replaces `X-Forwarded-For` with the one address it trusts, since the backend reads
  the first one; behind a CDN, a secret header rather than its address ranges ties the server to
  it. Let's Encrypt for a domain, self-signed for an IP address.
- **Monitoring is Beszel, optional, and only through ssh.** Open source (MIT), widely used, about
  20 MB, a dashboard ready to use without setup: of the server and its containers, not of the
  application (no custom metrics; Prometheus and Grafana could join for that). Never published:
  `deploy dashboard` forwards a port through ssh, the trust boundary of the whole deploy. Optional
  because its agent reads Docker's socket, which is as good as root. Checking from outside (is
  the server up at all) is out of scope.
- **Every question the CLI asks has a flag.** A flag skips its question, checked the same way;
  `--auto-approve` skips only the final confirmation (never another question's answer); without
  a terminal, a missing answer stops the command naming its flag. Commands ask everything first,
  show what they will do, and act only once confirmed; with `--dry-run`, any command that
  changes something only looks and says what it would do. So the CLI can run unattended, in a
  script or CI, as well as by hand. For programs (an AI agent, a script), every command takes
  `--json`: the same steps as JSON Lines events, plans and results as data, nothing asked.
- **Chromium's system libraries are documented, not installed**, on your machine: too many
  distros to cover.

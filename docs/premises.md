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
- **Chromium's system libraries are documented, not installed**, on your machine: too many
  distros to cover.

# Configuration

Every setting the platform reads, grouped by what it configures.

Settings live in `.env` at the repository root, which git ignores. A template is provided:

```
cp .env.example .env && chmod 600 .env
```

A server you deploy to has settings of its own, in its instance: see [Deploy](deploy.md).

`.env` is the only source: variables exported in your shell are ignored, so a stray export never
changes the setup. Every key is optional; the values below are the defaults unless noted.

## Dev stack

Read by `inatrace stack up`, see [Dev stack](dev-stack.md).

- `INATRACE_MODE`: `fullstack-dev`. What runs from an image instead of your checkout:
  `fullstack-dev` (nothing), `back-dev` (the frontend), `front-dev` (the backend) or `images`
  (both). `inatrace stack up --mode <mode>` overrides it for one run.
- `INATRACE_BACKEND_IMAGE`: `ghcr.io/agstack/inatrace-backend`
- `INATRACE_BACKEND_VERSION`: `latest`. Pin a release (`2.40.3`) to stay on it.
- `INATRACE_FRONTEND_IMAGE`: `ghcr.io/agstack/inatrace-frontend`
- `INATRACE_FRONTEND_VERSION`: `latest`

## Forks

Read by `inatrace repos sync`. Without push access to agstack, point a repo at your fork:

- `INATRACE_FORK_<REPO>`: `your-org/inatrace-backend` (no default). `<REPO>` is the repo's
  directory in `repos/`, upper-cased, `-` as `_`: `INATRACE_FORK_INATRACE_BACKEND`.

The next `repos sync` makes the fork `origin` and agstack `upstream`, with push to `upstream`
disabled, so a plain `git push` goes to the fork. An existing clone switches over only if its
`origin` is still agstack's.

Removing the line does **not** switch back. To do it by hand, inside the repo:

```
git remote remove origin && git remote rename upstream origin
git config --unset remote.origin.pushurl
```

## Dev container

Read on the host on every `devcontainer up`; a change recreates the container, keeping its
volumes. See [Dev container](dev-container.md).

- `INATRACE_INSTANCE`: `inatrace-platform`. Names the container and its volumes
  (`<instance>-home`, `<instance>-docker`).
- `INATRACE_BIND_ADDRESS`: `127.0.0.1`. `0.0.0.0` exposes the ports to the LAN, e.g. to test
  from a phone.
- `INATRACE_GATEWAY_PORT`: `8000`
- `INATRACE_BACKEND_PORT`: `9000`
- `INATRACE_FRONTEND_PORT`: `9080`
- `PLAYWRIGHT_HEADLESS`: headed when the host has a Wayland session, headless otherwise; `true`
  forces headless.

A second checkout of this repository, to try a branch of the platform, needs its own
`INATRACE_INSTANCE` and ports, or it takes over this one's container and volumes.

## GitHub

Read by `.devcontainer/provision.py`.

- `INATRACE_GH_TOKEN`: `github_pat_xxx` (no default). Logs `gh` in and lets git push over
  HTTPS. Never exported as an environment variable. Repos are cloned over SSH without it.

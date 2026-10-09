# Smoke tests

Checking that the running dev stack works end to end, in whatever mode it was started: the
images, the database, the API through the gateway, registration with its confirmation e-mail,
uploads, the site and the app in a real browser. About 15 seconds.

## How to run

```
inatrace stack up --mode images      # or any mode; the tests adapt
inatrace smoke                       # a report with the result of each check
inatrace smoke -v                    # each check as it runs, and what it does
inatrace smoke --lifecycle           # also stop and start what runs from images
inatrace smoke -- -k api             # extra pytest options after --
```

The first run installs the test dependencies (the `smoke` group in `pyproject.toml`) and the
Chromium build Playwright drives, about 150 MB in `~/.cache/ms-playwright`. Its system libraries
come with the dev container; on your machine they are a
[requirement](getting-started.md#installing-the-requirements). The exit code is 0 only when
every check passes.

## What it checks

| Area | Checks | When |
|---|---|---|
| Images | non-root users; no configuration baked in; runtime files; embedded version (when the tag is a version); OCI labels from CI | per part running from an image |
| Database | Flyway migrations recorded; 249 countries seeded | always |
| API | OpenAPI public (closed on a deployment with Swagger off); anonymous request rejected; registration sends the confirmation e-mail (Mailpit) and its link confirms the address; login; authenticated profile; no unexpected `ERROR` in the backend log | always (log: image) |
| File storage | upload; download returns the same bytes; file on the storage volume | always (volume: image) |
| Web | `index.html`; deep links; bundles; `env.js` generated from the image's environment; nginx non-root, no nginx errors | always (env.js, nginx: image) |
| Browser | login page without JavaScript errors; runtime settings; login through the UI | always |
| Lifecycle | stop on SIGTERM, start again; login and uploaded file after the restart; not killed for memory | `--lifecycle`, image only |
| Deployment | the certificate visitors get valid (behind a CDN, the CDN's), and the server's own valid and not expiring (with a domain; not with Let's Encrypt's staging); HTTP redirects to HTTPS; without the secret header the site refuses (with one); Caddy's admin API off; backend and frontend run the images in `.env`; every container not root, capabilities dropped, no new privileges, healthy; a backup newer than `INATRACE_BACKUP_DAYS` (a deployment less than a day old may have none yet), and one scheduled (`INATRACE_BACKUP_SCHEDULE`); disk under 90% | `deploy smoke` only |

A part running from your checkout skips the image checks, with the reason in the report. Its
confirmation e-mail check needs [mail sent to Mailpit](dev-stack.md#running-from-your-checkout).

## Against a deployment

```
inatrace deploy smoke <name>                              # at https://<site>
inatrace deploy smoke vm --url https://127.0.0.1:10443    # through a forwarded port
```

The same checks against a server set up with [`inatrace deploy`](deploy.md), only reading: the
site, the API, the bundles and the browser from outside, at its address, as a visitor reaches
it; the images, the database and the logs over ssh, with docker on the server. The checks that
create data (a user, an upload) or restart services do not run, the database is queried in a
read-only session, and there is no cleanup: nothing is made to clean. With a domain the
certificate must be valid; with an IP address (self-signed) it is not checked. The secret
header, when the instance has one, goes with every request. A deployment also gets checks of its
own (Deployment, above): the edge, the containers' hardening, the versions, backups and room.

## What it leaves behind

Nothing, as far as it can: everything the tests create is named `smoke-…` (users
`smoke-<hex>@example.com`, uploads `smoke-<hex>.bin`), and a best-effort cleanup removes it
before and after each run, so a run that stopped halfway is cleaned up by the next one. It
removes the user's tokens, audit rows and the user, the uploads' rows and files (files only on
the image's volume), and their e-mails in Mailpit. Your own data is never touched.

Activation is the one step outside the API, because it needs an admin: the tests set the user
`ACTIVE` in the database after confirming the e-mail.

## Layout

| Path | Purpose |
|---|---|
| `smoke-tests/conftest.py` | options, fixtures (stack, user, upload, browser), the cleanup and the report |
| `smoke-tests/common/stack.py` | what the tests run against, the dev stack or a deployment: its address, its containers (docker here or over ssh), SQL |
| `smoke-tests/common/mailpit.py` | Mailpit's API: find an e-mail, read it, delete them |
| `smoke-tests/tests/NN_area.py` | the checks, one file per area, in order; lifecycle last |

The backend logs `ERROR` lines for optional integrations without credentials (exchange rate API,
GeoLite) and a Hibernate warning (`HHH015007`); `KNOWN_ERRORS` in `tests/03_api.py` lists them.

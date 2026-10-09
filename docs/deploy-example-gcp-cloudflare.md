# Example: Google Cloud behind Cloudflare

One deploy from start to end, as it was done and tested: a VM on Google Cloud, a name on
Cloudflare, Let's Encrypt, then Cloudflare's proxy in front. [Deploy](deploy.md) explains each
part; this is the path through them, with what showed up on the way. The values here are
examples: `inatrace.example.org` for the name, `203.0.113.10` for the server's address.

## 1. The VM

In the console, *Compute Engine → VM instances → Create instance*:

| Setting | Value | Why |
|---|---|---|
| Machine type | `e2-medium` (2 vCPU, 4 GB) | the stack takes about 1.2 GB; `e2-small` (2 GB) is tight |
| Region | the one near your users (`southamerica-east1`, São Paulo) | |
| Boot disk | Debian 13 or Ubuntu 24.04, 20 GB or more | [a profile](deploy.md#the-server) installs Docker on both |
| Firewall | *Allow HTTP traffic* and *Allow HTTPS traffic* | Let's Encrypt and the visitors reach ports 80 and 443 |
| IP forwarding | off | Docker forwards inside the VM by itself |
| External IP | ephemeral, or a reserved one to keep it | the DNS record points at it |

The ssh user comes from the key, not from the image: under *Security → Manage access → Add
item*, paste a public key with the user's name in front, and Google's agent makes that user,
with `sudo` without a password:

```
inatrace:ssh-ed25519 AAAA... inatrace
```

Pasted without the prefix, the user is the key's comment before an `@` (`you@laptop` makes
`you`). Leave the users to that agent: one renamed by hand on the VM is fought over. With OS
Login on in the project, these keys are ignored: `gcloud compute os-login ssh-keys add`.

On your machine, `~/.ssh/config`, so `inatrace deploy` logs in without a question:

```
Host inatrace-gcp
    HostName 203.0.113.10
    User inatrace
    IdentityFile ~/.ssh/inatrace-gcp
    IdentitiesOnly yes
```

## 2. The name, without the proxy

On Cloudflare, *DNS → Records → Add record*: type `A`, the name (`inatrace` makes
`inatrace.example.org`), the VM's external IP, and **DNS only** (grey cloud). Without the proxy
first, Let's Encrypt and you reach the server straight, and the first certificate comes
without anything in between.

## 3. Install and start

```
inatrace deploy init gcp --ssh inatrace-gcp --install --domain inatrace.example.org \
  --front direct --no-swagger --no-mail --monitoring \
  --backend-image ghcr.io/agstack/inatrace-backend --backend-version 2.40.3 \
  --frontend-image ghcr.io/agstack/inatrace-frontend --frontend-version 2.34.0 \
  --auto-approve
inatrace deploy up gcp
inatrace deploy admin gcp you@example.org --company "Your organization" --create
inatrace deploy smoke gcp
```

- `init` finds Debian, installs Docker, rsync and cron with `deploy/prereqs/debian.sh` (about
  35 s), and saves `deploy/instances/gcp/.env`: keep a copy, it holds the passwords.
- `up` starts the containers one at a time (the backend took about 2 minutes on an
  `e2-medium`). Caddy gets the certificate from Let's Encrypt as it starts, in seconds.
- `admin --create` registers the first admin, asks a password, and makes a first company.
- `smoke` checks it from outside, the certificate included: `Let's Encrypt, 89 days left`.

`inatrace deploy status gcp` then shows the certificate on the site's line, and turns yellow
if a renewal ever fails.

## 4. Cloudflare in front

`init` again, in CDN mode, with a secret header, then `up`:

```
inatrace deploy init gcp --ssh inatrace-gcp --domain inatrace.example.org \
  --front cdn --cdn cloudflare --origin-secret --no-swagger --no-mail --monitoring \
  --backend-image ghcr.io/agstack/inatrace-backend --backend-version 2.40.3 \
  --frontend-image ghcr.io/agstack/inatrace-frontend --frontend-version 2.34.0 \
  --auto-approve
inatrace deploy up gcp
grep ORIGIN_SECRET deploy/instances/gcp/.env
```

From then on the server answers 403 to whatever lacks the secret, so the site is down until
Cloudflare sends it. On Cloudflare, for the same hostname:

1. *Rules → Configuration Rules*: Hostname equals `inatrace.example.org`, then **SSL: Full
   (strict)**. The mode under *SSL/TLS → Overview* is the whole zone's, other names included:
   the rule sets it for this one alone.
2. *Rules → Transform Rules → Modify Request Header*: Hostname equals `inatrace.example.org`,
   **Set static** `X-Origin-Secret` to the value from `.env`.
3. *SSL/TLS → Edge Certificates*: **Always Use HTTPS** off (it is the zone's: check it). Caddy
   redirects to HTTPS itself; with it on, Let's Encrypt's challenge cannot reach Caddy.
4. *DNS*: turn the record to **Proxied** (orange cloud).

Then check:

```
inatrace deploy status gcp     # the site answers; the server's own certificate, Let's Encrypt
inatrace deploy smoke gcp      # through Cloudflare, with the deployment's checks
```

The smoke tells the two certificates apart: the one visitors get is Cloudflare's, the
server's own is Let's Encrypt's, and that is the one Caddy renews. It also checks that the
server, reached around Cloudflare, answers 403 without the secret. The backend records each
visitor's own address (from `CF-Connecting-IP`), not Cloudflare's, nor a forged
`X-Forwarded-For`.

## What showed up

- **The certificate behind the proxy.** Let's Encrypt has two challenges: TLS-ALPN-01 on 443,
  which Cloudflare cannot pass on (it ends the TLS), and HTTP-01 on 80, which it does. Caddy
  tries the first, sees it fail and uses the second, for the first certificate and the
  renewals. That needs Always Use HTTPS off (step 4.3).
- **The zone's settings.** SSL/TLS mode and Always Use HTTPS apply to every name in the zone;
  Configuration Rules set the mode per hostname, on the free plan too.
- **Let's Encrypt's limits.** 5 certificates alike a week for one name. Caddy keeps its
  certificate in a volume, so `up`, a recreated Caddy or a reboot reuse it; what makes a new
  one is losing that volume. For tests that must obtain them again and again,
  `INATRACE_TLS=acme-staging` uses Let's Encrypt's staging (an untrusted CA, far higher limits);
  back to `acme`, the production certificate in the volume serves again.
- **Ports.** With the proxy on, the server only needs 80 and 443 open to Cloudflare; without
  the secret, limiting them to Cloudflare's ranges is the other way (see [Behind a
  CDN](deploy.md#behind-a-cdn)), weaker.

## 5. Taking it down

- the VM: *Compute Engine → VM instances → Delete* (a reserved IP is billed until released);
- on Cloudflare: the DNS record, the Configuration Rule and the Transform Rule;
- on your machine: `deploy/instances/gcp/` (and the key, `~/.ssh/inatrace-gcp*`, if only for
  this), after copying its backups if you want them (`inatrace deploy backup download gcp`).

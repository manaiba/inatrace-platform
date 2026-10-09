# CLI

`inatrace` (`bin/inatrace` without the dev container) runs everything the platform does: the
repos, the dev stack, the smoke tests and the deploys. `inatrace --help`, and `--help` on any
command, lists what it takes.

## Conventions

- **Every question has a flag.** A flag skips its question, checked the same way. Without a
  terminal, a missing answer stops the command naming its flag.
- **It asks first, then acts.** A command asks everything (only looking meanwhile), shows what it
  will do and acts once confirmed; `--auto-approve` skips only that last confirmation.
- **`--dry-run` (`-n`)** on anything that changes something: it only looks, and says what it
  would do.
- **Short forms** for the common options: `-n` dry run, `-w [seconds]` watch, `-f` follow.
- **Waits have a timeout**, and Ctrl+C stops any command; every `deploy` command is safe to
  repeat after one.

## JSON

`--json`, anywhere on the line (`inatrace deploy status vm --json`), turns any command's output
into JSON Lines on stdout: one event a line, for a program to read (an AI agent, a script).
Nothing is asked: a missing answer is an `error` event naming its flag, and a confirmation needs
`--auto-approve` (or `--dry-run`). Whatever another program prints along the way (git, Compose,
pytest) goes to stderr. The exit code is the same as without it.

```
$ inatrace deploy up vm --json
{"event": "section", "title": "Deploying vm on inatrace-vm"}
{"event": "start", "task": "pulling the images"}
{"event": "end", "task": "pulling the images", "ok": true, "seconds": 1.7}
...
{"event": "up", "url": "https://127.0.0.1"}
```

Every command:

| Event | Fields |
|-------|--------|
| `section` | `title` |
| `info`, `ok`, `step`, `warn`, `fail` | `text` |
| `start`, `end` | `task`; `end` also `ok`, `seconds` |
| `output` | `text`: a line another program printed (Compose, an install script) |
| `plan` | a dry run: `actions` (words), `note`; for `deploy up`, `files`, `containers`, `restarts`, `backup`, `schedule`; for `fix-permissions`, `paths` |
| `error` | `message`, and `flag` when an answer is missing |

Some commands, also:

| Command | Event | Fields |
|---------|-------|--------|
| `doctor` | `doctor` | `checks` (`name`, `ok`), `browser`, `permissions`, `hints` |
| `fix-permissions` | `permissions` | `fixed`, `not_yours`, `umask`, `umask_lets_others_write` |
| `repos sync` | `repo` | `name`, `action` (`cloned`, `fetched`), `status` (the branch) |
| `stack up`, `stack status` | `status` | `containers`: `service`, `state`, `health`, `image`, `status`, `ports` |
| `stack logs`, `deploy logs` | `log` | `service`, `text` |
| `smoke`, `deploy smoke` | `test`, `report` | `area`, `check`, `outcome`, `detail`, `seconds`; at the end `target`, `counts`, `ok`, `lifecycle` |
| `deploy init` | `answer`, `versions`, `summary`, `next` | an answer taken from a flag; the image's versions (newest first) before asking for one; `settings`, `changed`, `will_do`, `then`; the command to run next |
| `deploy status` | `status` | `site`, `certificate` (`issuer`, `expires`, `days`, `trusted`, `state`: `ok`, `renew`, `bad`), `server`, `containers`, `backups` (`newest`, `count`, `days`, `schedule`, `zone`), `ok` (with `-w`, one each time) |
| `deploy backup list` | `backups` | `backups` (`time`, `date`, `database_bytes`, `uploads_bytes`, `server`, `here`, newest first), `schedule`, `zone`, `days`, `here` (the local directory) |
| `deploy backup download` | `downloaded` | `times`, `into` |
| `deploy backup create` (and before changes) | `backup` | `time` |
| `deploy backup delete` | `plan` | `actions`, `backups` (those that go), `left` |
| `deploy up` | `up` | `url` (and with `--restore`, the restore's events) |
| `deploy destroy` | `plan` | `actions`, `note` (what stays) |
| `deploy admin --create --generate-password` | `password` | `email`, `password`: shown once |
| `deploy dashboard` | `dashboard` | `url`: where the dashboard is, until the command stops |

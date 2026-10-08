# Multi-repo workspace

This is a **workspace root**: the `inatrace-platform` repo (the dev container,
the dev stack and this file). The repositories you work on live under
`/workspace/repos` — each is an **independent Git repository** with its own
history, its own remote, and often its own `CLAUDE.md`. `repos/` is git-ignored
by the platform repo.

## Rules

1. **Know the repo before you edit.** Every file under `/workspace/repos/<repo>/`
   belongs to that one repo; everything else belongs to the platform. Before
   editing a file, confirm which repo its path lives in.
2. **Respect per-repo instructions.** The first time you enter a repo in a
   session, read its `/workspace/repos/<repo>/CLAUDE.md` (if present) and follow
   it. Repo-level instructions take precedence over this file when they conflict.
3. **Commit in the correct repo.** Run `git` commands from inside the specific
   repo directory (`/workspace/repos/<repo>/`, or `/workspace` for the platform).
   A commit must contain files from exactly one repo.
4. **Never create cross-repo commits.** Do not stage or commit files spanning
   more than one repo. If a change touches several repos, make a separate commit
   in each.
5. **Don't auto-merge.** `inatrace repos sync` only fetches; do not `git pull`, merge, or
   rebase shared branches unless explicitly asked.
6. **No AI attribution.** Never add `Co-Authored-By: Claude …` trailers to commits, nor
   "Generated with Claude Code" (or similar) to pull requests, issues or docs.
7. **Conventional Commits.** Commit subjects and pull request titles use
   `<type>: <summary>`, lowercase, imperative, no trailing period: `fix:`, `feat:`,
   `docs:`, `ci:`, `build:`, `test:`, `refactor:`, `chore:`. Example:
   `fix: sync package-lock.json with package.json`.
8. **Docs go with the change.** Before committing, update the documentation the change makes
   wrong, in the same commit: for the platform, the matching page in `docs/` (commands,
   settings, ports, behavior), the README's table if a page is added, and `docs/premises.md`
   when a settled choice changes. A change that needs no doc update is fine; a stale doc is not.

## Layout

```
/workspace          the inatrace-platform repo — the single mounted directory
├── .devcontainer/  the dev container: Dockerfile, compose, initialize.py and cleanup.py (host),
│                   provision.py (inside); tests in .devcontainer/tests/
├── bin/inatrace    the platform's CLI (python), with or without the dev container: repos sync, stack, smoke, doctor
├── inatrace/       its code (typer, rich; run through uv); tests in inatrace/tests/
├── pyproject.toml  the CLI's dependencies, and pytest for both test dirs (`uv run pytest`)
├── dev-stack/      gateway, MySQL, Mailpit, and per mode the backend/frontend images (`inatrace stack up`)
├── smoke-tests/    end-to-end checks of the running dev stack (`inatrace smoke`)
├── docs/           the platform's documentation; premises.md holds the settled choices
├── repos/
│   ├── repos.txt   list of repos to clone
│   └── <repo>      cloned repos, each an independent git repo (git-ignored)
├── CLAUDE.md       this file
└── README.md
```

## Stack (varies per repo)

| Repo | Stack |
|------|-------|
| `inatrace-backend` | Java 17 · Spring Boot 3.3 · Maven (no wrapper — use the image's `mvn`) · MySQL 8.4 |
| `inatrace-frontend` | Angular 10 · TypeScript · npm · **Node 14** (`nvm use 14`) |
| `inatrace` | upstream docs / reference |

Check each repo's own README/CLAUDE.md for specifics.

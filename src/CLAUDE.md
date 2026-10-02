# Multi-repo workspace

This is a **workspace root**, not a single project. The repositories you work on
live under `/src` — each is an **independent Git repository** with its own
history, its own remote, and often its own `CLAUDE.md`.

## Rules

1. **Know the repo before you edit.** Every file under `/src/<repo>/` belongs to
   that one repo. Before editing a file, confirm which repo its path lives in.
2. **Respect per-repo instructions.** The first time you enter a repo in a
   session, read its `/src/<repo>/CLAUDE.md` (if present) and follow it. Repo-level
   instructions take precedence over this file when they conflict.
3. **Commit in the correct repo.** Run `git` commands from inside the specific
   repo directory (`/src/<repo>/`). A commit must contain files from exactly one
   repo.
4. **Never create cross-repo commits.** Do not stage or commit files spanning
   more than one repo. If a change touches several repos, make a separate commit
   in each.
5. **Don't auto-merge.** `setup.sh` only fetches; do not `git pull`, merge, or
   rebase shared branches unless explicitly asked.
6. **No AI attribution.** Never add `Co-Authored-By: Claude …` trailers to commits, nor
   "Generated with Claude Code" (or similar) to pull requests, issues or docs.
7. **Conventional Commits.** Commit subjects and pull request titles use
   `<type>: <summary>`, lowercase, imperative, no trailing period: `fix:`, `feat:`,
   `docs:`, `ci:`, `build:`, `test:`, `refactor:`, `chore:`. Example:
   `fix: sync package-lock.json with package.json`.

## Layout

```
/src            workspace root — the single mounted directory
├── setup.sh    clones/fetches the repos and configures $HOME (idempotent)
├── gateway/    dev nginx: `/api` → backend :8080, rest → frontend :4200 (host :8000)
├── repos.txt   list of repos to clone
├── CLAUDE.md   this file
├── <repo>      cloned repos, each an independent git repo (git-ignored)
└── ...
```

## Stack (varies per repo)

| Repo | Stack |
|------|-------|
| `inatrace-backend` | Java 17 · Spring Boot 3.3 · Maven (no wrapper — use the image's `mvn`) · MySQL 8.4 |
| `inatrace-frontend` | Angular 10 · TypeScript · npm · **Node 14** (`nvm use 14`) |
| `inatrace` | upstream docs / reference |

Check each repo's own README/CLAUDE.md for specifics.

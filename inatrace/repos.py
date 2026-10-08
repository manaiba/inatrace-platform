"""The INATrace repos listed in repos/repos.txt, cloned next to it."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import shell, ui


@dataclass(frozen=True)
class Repo:
    url: str
    name: str  # directory under repos/


def parse(text: str) -> list[Repo]:
    """One `<git-url> [target-dir]` per line; blank lines and # comments skipped."""
    repos = []
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        url = fields[0]
        name = fields[1] if len(fields) > 1 else url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        repos.append(Repo(url, name.removesuffix(".git")))
    return repos


def load(path: Path) -> list[Repo]:
    if not path.is_file():
        raise ui.StepError(f"{path} not found")
    return parse(path.read_text())


def fork_key(name: str) -> str:
    """The .env key of a repo's fork: inatrace-backend -> INATRACE_FORK_INATRACE_BACKEND."""
    return "INATRACE_FORK_" + re.sub(r"[^A-Z0-9]", "_", name.upper())


def fork_url(fork: str) -> str:
    return f"git@github.com:{fork}.git"


def _git(dest: Path, *args: str) -> None:
    shell.run(["git", "-C", dest, *args])


def _config(dest: Path, key: str) -> str:
    return shell.output(["git", "-C", dest, "config", "--get", key], check=False)


def _set_upstream(dest: Path, url: str) -> None:
    """With a fork: upstream = the repos.txt URL, push to it disabled, and a bare
    `git push` goes to the fork even from branches that track upstream."""
    if not _config(dest, "remote.upstream.url"):
        _git(dest, "remote", "add", "upstream", url)
    _git(dest, "config", "remote.upstream.pushurl", "DISABLED")
    _git(dest, "config", "remote.pushDefault", "origin")


def status(dest: Path) -> str:
    """The checked-out branch, and how it compares with the branch it tracks."""
    branch = shell.output(["git", "-C", dest, "branch", "--show-current"], check=False)
    if not branch:
        return "detached HEAD"
    tracked = shell.output(["git", "-C", dest, "rev-parse", "--abbrev-ref", "@{upstream}"], check=False)
    if not tracked:
        return f"on {branch} (no upstream branch)"
    counts = shell.output(["git", "-C", dest, "rev-list", "--left-right", "--count",
                           "@{upstream}...HEAD"], check=False).split()
    behind, ahead = (int(c) for c in counts) if len(counts) == 2 else (0, 0)
    parts = [f"{behind} behind" if behind else "", f"{ahead} ahead" if ahead else ""]
    return f"on {branch} → {tracked}: {', '.join(p for p in parts if p) or 'up to date'}"


def sync(repos: list[Repo], repos_dir: Path, forks: dict[str, str]) -> None:
    """Clones missing repos and fetches existing ones. Never merges or pulls, so
    local branches stay as they were. Remotes change only while origin is still
    exactly what this would have set; anything else is the user's and left alone."""
    failed = []
    for repo in repos:
        dest = repos_dir / repo.name
        fork = fork_url(forks[repo.name]) if repo.name in forks else ""
        try:
            if (dest / ".git").exists():
                origin = _config(dest, "remote.origin.url")
                if fork and origin == repo.url:
                    ui.info(f"{repo.name}: origin -> fork, upstream -> {repo.url}")
                    _git(dest, "remote", "rename", "origin", "upstream")
                    _git(dest, "remote", "add", "origin", fork)
                    _set_upstream(dest, repo.url)
                elif fork and origin == fork:
                    _set_upstream(dest, repo.url)
                elif fork:
                    ui.warn(f"{repo.name}: origin is {origin}, not the fork — left as is")
                remotes = shell.output(["git", "-C", dest, "remote"], check=False).split()
                ui.info(f"{repo.name}: fetching {', '.join(remotes)}")
                _git(dest, "fetch", "--all", "--prune", "--quiet")
                ui.info(f"{repo.name}: {status(dest)}")
            elif dest.exists():
                raise ui.StepError(f"{dest} exists but is not a git repo")
            elif fork:
                ui.info(f"cloning {repo.name} from the fork {fork}")
                shell.run(["git", "clone", "--quiet", fork, dest])
                _set_upstream(dest, repo.url)
                _git(dest, "fetch", "--quiet", "upstream")
            else:
                ui.info(f"cloning {repo.name}")
                shell.run(["git", "clone", "--quiet", repo.url, dest])
        except (ui.StepError, OSError, subprocess.CalledProcessError) as error:
            ui.warn(f"{repo.name}: {error}")
            failed.append(repo.name)
    if failed:
        raise ui.StepError(f"failed for {', '.join(failed)}")

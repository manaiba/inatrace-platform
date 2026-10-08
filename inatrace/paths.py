"""Where things live in the platform repo, wherever it is checked out."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_ENV = ROOT / ".env"
REPOS_DIR = ROOT / "repos"
REPOS_FILE = REPOS_DIR / "repos.txt"
DEVCONTAINER_DIR = ROOT / ".devcontainer"
DEV_STACK_COMPOSE = ROOT / "dev-stack" / "compose.yaml"
SMOKE_TESTS = ROOT / "smoke-tests"

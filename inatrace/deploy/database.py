"""Database engine selection shared by deployment operations.

The default keeps instances created before database selection on MySQL.
Selection alone does not configure or start a database.
"""

from .common import DeployError

ENGINES = ("mysql", "postgresql")
SETTING = "INATRACE_DATABASE"
POSTGRES_MAJOR = "18"


def engine(values: dict[str, str]) -> str:
    """Return the configured engine, rejecting unknown or ambiguous values."""
    selected = values.get(SETTING) or "mysql"
    if selected not in ENGINES:
        raise DeployError(f"{SETTING}={selected} is not one of {', '.join(ENGINES)}")
    return selected


def require_same_engine(configured: str, deployed: str) -> None:
    """Refuse an engine change: switching engines does not migrate data."""
    if configured != deployed:
        raise DeployError(f"database engine changed from {deployed} to {configured}; "
                          "switching engines does not migrate data")

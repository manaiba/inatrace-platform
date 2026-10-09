"""What every deploy command shares: its error, a dry run's plan, sizes and ages in words."""

import datetime

from .. import ui


class DeployError(Exception):
    """Something to fix before going on; the message says what. `flag`: the one that
    gives a missing answer."""

    def __init__(self, message: str, flag: str | None = None) -> None:
        super().__init__(message)
        self.flag = flag


def would(*actions: str, note: str = "") -> None:
    """A dry run's plan, in words (rich markup allowed), and a note on it; then that
    nothing changed."""
    def render() -> None:
        if len(actions) == 1:
            ui.out.print(f"  [bold]Would do:[/] {actions[0]}")
        else:
            ui.out.print("  [bold]Would do:[/]")
            for action in actions:
                ui.out.print(f"   {action}")
        if note:
            ui.info(note)

    ui.show("plan", {"actions": [ui.plain(a) for a in actions], **({"note": note} if note else {})}, render)
    ui.info("dry run: nothing was changed")


def size(count: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}".replace(".0 ", " ")
        count /= 1024
    return ""


def age(when: datetime.datetime, now: datetime.datetime) -> str:
    minutes = int((now - when).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"

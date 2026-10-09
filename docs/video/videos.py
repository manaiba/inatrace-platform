"""The videos: their clips in order, each with its caption. The terminal's part of a clip is
tapes/<video>/<clip>.tape; a browser clip is a flow in browser.py; a card is text on the
background. Change a caption here and `make.py <video>` redoes only the joining.

A terminal clip is recorded once for real (make.py --record: the commands run, asciinema keeps the
session in a .cast), and the video replays that: captions, speed, holds and colors change without
the server.

A clip may have:
  caption   (label, text): on top while it plays; `code` in backticks
  speed     fast-forward the clip's last command, from its output on, this many times (a badge says so)
  hold      seconds its last screen stays (4)
  idle      the longest pause kept in it (1.2 s)
  setup     shell commands run first, off camera, so the clip can be recorded again on its own
  browser   the flow in browser.py, with args and the url its address bar shows
  card      (kicker, title, subtitle, command), for seconds
  cast      replay another clip's session (it records nothing itself), with:
  since     from this text on (what came before already on screen)
  until     up to this text
  fast_from where the fast part starts (without it: the last command's output)
Clips in a row with the same caption show it once, across them.
"""

from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parent.parent.parent  # this checkout, which the tutorial clones
CHECKOUT = HOME / "inatrace-platform"  # the tutorial runs from a fresh clone, as a newcomer would
SITE = "https://127.0.0.1:10443"
ADMIN = ("ana@example.org", "Demo-Coop-2026")

_instance = CHECKOUT / "deploy" / "instances" / "demo"
_cli = f"{CHECKOUT}/bin/inatrace"
# No VM demo and no image downloaded yet, so `vm create` shows it all.
_fresh_vm = [f"{REPO}/bin/inatrace vm destroy demo --auto-approve >/dev/null 2>&1; true",
             "rm -f ~/.cache/inatrace/images/ubuntu-26.04-server-cloudimg-amd64.img"]


def _help(name: str, caption: tuple[str, str]) -> dict:
    """A command's --help, before the command itself, under the same caption."""
    return {"name": f"help-{name}", "caption": caption, "hold": 5}


_server = ("A server", "Any server you reach with ssh. To try it, `inatrace vm` makes one here, as a cloud would")
_init = ("Create an instance", "`init` asks everything first, shows a summary, and changes nothing until you confirm")
_prepare = ("The server", "`prepare` installs Docker, rsync and cron there, from Docker's own repository")
_up = ("Start it", "`up` copies the files, pulls the images, starts each container once the one before is healthy")
_status = ("Look at it", "`status`: the site, its certificate, the server, the containers and the backups")
_admin = ("The first admin", "A new site has no admin and no company: `admin --create` makes both")
_monitoring = ("Monitoring", "`dashboard` opens Beszel through ssh: the server and each container, with their history")
_backup = ("Backups", "The database and the uploads: daily on their own, kept 7 days; or now, with `backup create`")
_smoke = ("Check it", "`smoke` tests the deployment, only reading: the site, the API, the images, the database")

TUTORIAL = {
    # From nothing: a fresh clone of the platform (with this checkout's changes), no VM, no instance.
    "setup": [*_fresh_vm, f"rm -rf {CHECKOUT} && git clone -q {REPO} {CHECKOUT} && rsync -a --exclude .git "
              "--exclude .venv --exclude repos/ --exclude deploy/instances/ --exclude docs/video/out/ "
              f"{REPO}/ {CHECKOUT}/ && {_cli} --help >/dev/null"],
    "clips": [
        {"name": "title", "card": ("INATrace platform", "Deploy INATrace", "to your own server, step by step",
                                   "inatrace deploy"), "seconds": 3},
        {"name": "overview", "caption": ("The CLI", "In the platform's checkout, one command does the deploy: "
                                                    "`inatrace deploy`")},
        _help("vm", _server),
        {"name": "vm-create", "caption": _server, "setup": _fresh_vm, "speed": 8, "fast_from": "downloading",
         "idle": 5},
        _help("init", _init),
        {"name": "init", "caption": _init, "idle": 2, "setup": [
            f"rm -rf {_instance}",
            "ssh demo 'while sudo fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; "
            "do sleep 2; done'"]},
        _help("prepare", _prepare),
        {"name": "prepare", "caption": _prepare, "speed": 8},
        _help("up", _up),
        {"name": "up", "caption": _up, "speed": 8, "setup": [f"{_cli} deploy down demo >/dev/null 2>&1; true"]},
        _help("status", _status),
        {"name": "status", "caption": _status},
        _help("admin", _admin),
        {"name": "admin", "caption": _admin},
        {"name": "login", "browser": "login", "url": SITE,
         "args": {"site": SITE, "user": ADMIN[0], "password": ADMIN[1]},
         "caption": ("Use it", "The site, at the forwarded port. No domain here, so the certificate is "
                               "self-signed; with one, Let's Encrypt")},
        _help("dashboard", _monitoring),
        {"name": "dashboard", "caption": _monitoring, "until": "Ctrl+C closes it", "hold": 2},
        {"name": "beszel", "browser": "beszel", "url": "http://localhost:8090",
         "args": {"cli": _cli, "instance": "demo"}, "caption": _monitoring},
        {"name": "dashboard-close", "cast": "dashboard", "since": "^C", "caption": _monitoring, "hold": 3},
        _help("backup", _backup),
        {"name": "backup", "caption": _backup},
        _help("smoke", _smoke),
        {"name": "smoke", "caption": _smoke, "speed": 8},
        {"name": "end", "card": ("That's it", "INATrace is up", "docs/deploy.md: domains, CDNs, updates, backups",
                                 "inatrace deploy --help"), "seconds": 4},
    ],
}

VIDEOS = {"tutorial": TUTORIAL}

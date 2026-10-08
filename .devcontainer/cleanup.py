#!/usr/bin/env python3
"""Leftovers of the dev container, on the HOST. Python 3.10+, standard library only.

Two jobs:

1. (default) Stop dev containers this checkout left behind. Every dev
   container ever created for this folder is labelled
   devcontainer.local_folder=<repo root>; those of the current stack also
   carry com.docker.compose.project=<instance>. Anything with the first label
   but not the second is from an older configuration, and keeps its published
   ports, so the new stack fails with "port is already allocated". Stopping
   is reversible (`docker start <name>`); --remove deletes them too.

2. (--purge) Delete EVERYTHING this dev container owns, to start from
   scratch: its containers (the current one included), its volumes — your
   $HOME and the nested Docker —, its images and the generated files. The
   cloned repos under repos/ are NOT touched. Asks unless --yes.
"""

import argparse
import re
import shutil
import sys

if sys.version_info < (3, 10):
    sys.exit("cleanup.py needs Python 3.10 or newer.")

from common import env, shell  # noqa: E402


def note(message: str) -> None:
    print(f"cleanup: {message}", file=sys.stderr, flush=True)


def _containers(*filters: str) -> list[str]:
    cmd = ["docker", "ps", "-aq"]
    for f in filters:
        cmd += ["--filter", f]
    return shell.output(cmd, check=False).split()


def _inspect(container: str, template: str) -> str:
    return shell.output(["docker", "inspect", container, "--format", template], check=False)


def purge(project: str, assume_yes: bool, dry_run: bool) -> int:
    here = env.DEVCONTAINER_DIR
    containers = sorted(set(_containers(f"label=com.docker.compose.project={project}")
                            + _containers(f"label=devcontainer.local_folder={env.ROOT}")))
    volumes = [v for v in (f"{project}-home", f"{project}-docker")
               if shell.ok(["docker", "volume", "inspect", v])]
    # The image Compose builds (<project>-app) and the ones the containers run:
    # the devcontainer tooling derives vsc-<folder>-<hash>-uid from it.
    images = sorted({i for i in shell.output(["docker", "images", "--format",
                                              "{{.Repository}}:{{.Tag}}"], check=False).split()
                     if i.startswith(f"{project}-")}
                    | {_inspect(c, "{{.Config.Image}}") for c in containers} - {""})
    generated = [p for p in (here / ".env", here / ".state") if p.exists()]

    print("cleanup --purge will delete:")
    for c in containers:
        print(f"  container  {_inspect(c, '{{.Name}}').lstrip('/')}")
    for v in volumes:
        print(f"  volume     {v}")
    for i in images:
        print(f"  image      {i}")
    for p in generated:
        print(f"  file       {p}")
    print(f"  (kept: the cloned repos under {env.ROOT / 'repos'})")
    if dry_run:
        print("--dry-run: nothing deleted.")
        return 0
    if not assume_yes:
        try:
            answer = input(f"Type the instance name ({project}) to confirm: ")
        except EOFError:
            answer = ""
        if answer.strip() != project:
            print("Aborted.")
            return 1
    if containers:
        shell.run(["docker", "rm", "-f", *containers], quiet=True)
    if volumes:
        shell.run(["docker", "volume", "rm", *volumes], quiet=True)
    if images:
        shell.run(["docker", "image", "rm", *images], quiet=True)
    for p in generated:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    print("Purged. The next `devcontainer up` starts from scratch.")
    return 0


def stale(project: str, remove: bool, dry_run: bool) -> int:
    root = env.ROOT
    # --filter matches the label value exactly, so a checkout of this repo at a
    # different path (a second clone) is left alone.
    found = [c for c in _containers(f"label=devcontainer.local_folder={root}")
             if _inspect(c, '{{index .Config.Labels "com.docker.compose.project"}}') != project]
    if not found:
        print(f"No stale dev containers for {root}.")
    else:
        print(f"Stale dev containers for {root}:")
        for c in found:
            ports = _inspect(c, "{{range $p, $_ := .HostConfig.PortBindings}}{{$p}} {{end}}")
            print(f"  {_inspect(c, '{{.Name}}').lstrip('/'):<24} "
                  f"{_inspect(c, '{{.State.Status}}'):<10} {ports or '(no published ports)'}")
        if dry_run:
            print("--dry-run: nothing stopped.")
        else:
            shell.run(["docker", "stop", *found], quiet=True)
            print(f"Stopped {len(found)} container(s).")
            if remove:
                shell.run(["docker", "rm", *found], quiet=True)
                print(f"Removed {len(found)} container(s).")
            else:
                print("Run with --remove to delete them, or restart one with `docker start <name>`.")

    # Ports this stack publishes, held by containers from OTHER projects: out of
    # scope to touch, but they cause the same "port is already allocated".
    env_file = env.DEVCONTAINER_DIR / ".env"
    wanted = set()
    if env_file.exists():
        wanted = {v for k, v in env.parse(env_file.read_text()).items()
                  if re.fullmatch(r"DC_[A-Z]+_PORT", k)}
    ours = set(shell.output(["docker", "ps", "--filter",
                             f"label=com.docker.compose.project={project}",
                             "--format", "{{.Names}}"], check=False).split())
    conflicts = []
    for line in shell.output(["docker", "ps", "--format", "{{.Names}}|{{.Ports}}"],
                             check=False).splitlines():
        name, _, portmap = line.partition("|")
        if name in ours:
            continue
        conflicts += [f"{name} holds {p}" for p in sorted(wanted) if f":{p}->" in portmap]
    if conflicts:
        note("WARNING — ports this stack publishes are taken by other projects' containers:")
        for c in conflicts:
            print(f"  {c}", file=sys.stderr)
        note("stop them by hand if the stack fails to start.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog=".devcontainer/cleanup.py",
                                description="Stop dev containers this checkout left behind; "
                                            "--purge deletes everything it owns.")
    p.add_argument("-r", "--remove", action="store_true", help="delete the stale containers too")
    p.add_argument("-p", "--purge", action="store_true",
                   help="delete ALL containers, volumes, images and generated files of this "
                        "dev container (the repos are kept)")
    p.add_argument("-y", "--yes", action="store_true", help="with --purge: do not ask")
    p.add_argument("-n", "--dry-run", action="store_true", help="only report what would be done")
    args = p.parse_args(argv)
    if not shell.ok(["docker", "info"]):
        note("cannot talk to the Docker daemon — is it running?")
        return 1
    project = env.load().instance
    if args.purge:
        return purge(project, args.yes, args.dry_run)
    return stale(project, args.remove, args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

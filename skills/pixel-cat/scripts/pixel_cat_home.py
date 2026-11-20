"""Install, find and update the pixel-cat checkout that the pixel-cat skill drives.

    uv run --no-project pixel_cat_home.py status
    uv run --no-project pixel_cat_home.py install [--home DIR]
    uv run --no-project pixel_cat_home.py update

The checkout is the "home". Its location is stored outside the skill folder,
in ~/.config/pixel-cat/config.json, so it survives `npx skills update`.
Standard library only.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/pyros-projects/pixel-cat.git"


def config_file():
    override = os.environ.get("PIXEL_CAT_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "pixel-cat" / "config.json"


def default_home():
    override = os.environ.get("PIXEL_CAT_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "pixel-cat" / "app"


def recorded_home():
    try:
        return Path(json.loads(config_file().read_text(encoding="utf-8"))["home"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_home(home):
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"home": str(home)}, indent=2) + "\n", encoding="utf-8")


def is_checkout(path):
    return (path / "pyproject.toml").is_file() and (path / "pixel_cat" / "cli.py").is_file()


def run(command, cwd):
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def output(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def require_tools():
    missing = [tool for tool in ("git", "uv") if not shutil.which(tool)]
    if missing:
        sys.exit(
            "missing: " + ", ".join(missing) + ". Install them first: "
            "git from https://git-scm.com, uv from https://docs.astral.sh/uv/"
        )


def status(_args=None):
    home = recorded_home()
    if home is None:
        print("status: not installed")
        print(f"default home: {default_home()}")
        return
    if not is_checkout(home):
        print(f"status: home missing ({home})")
        return
    print("status: ok")
    print(f"home: {home}")
    version = output(["git", "log", "-1", "--format=%h %cs %s"], home)
    print(f"version: {version or 'unknown'}")
    ffmpeg = shutil.which("ffmpeg")
    print(f"ffmpeg: {ffmpeg or 'missing (needed for video input, MP4, WebM and films)'}")


def install(args):
    require_tools()
    if args.home:
        home = Path(args.home).expanduser().resolve()
    else:
        home = recorded_home() or default_home()
    if is_checkout(home):
        print(f"using the pixel-cat checkout in {home}")
    elif home.exists() and any(home.iterdir()):
        sys.exit(f"{home} is not empty and not a pixel-cat checkout. Pass --home with a new or empty folder.")
    else:
        home.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", REPO, str(home)], home.parent)
    run(["uv", "sync", "--locked"], home)
    save_home(home)
    print()
    status()


def update(_args):
    """Move the home to the published version. The home is an install, not a workspace: local edits to
    tracked files are replaced. Folders the user added, such as output/, stay."""
    require_tools()
    home = recorded_home()
    if home is None or not is_checkout(home):
        sys.exit("pixel-cat is not installed here. Run install first.")
    run(["git", "fetch", "origin"], home)
    run(["git", "reset", "--hard", "origin/main"], home)
    run(["uv", "sync", "--locked"], home)
    print()
    status()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show the home, version and prerequisites")
    setup = commands.add_parser("install", help="clone and set up the home, or adopt a checkout")
    setup.add_argument("--home", help="folder for the checkout, or an existing checkout")
    commands.add_parser("update", help="fetch the latest version and refresh the environment")
    args = parser.parse_args()
    try:
        {"status": status, "install": install, "update": update}[args.command](args)
    except subprocess.CalledProcessError as error:
        sys.exit(f"failed: {' '.join(map(str, error.cmd))} (exit {error.returncode})")


if __name__ == "__main__":
    main()

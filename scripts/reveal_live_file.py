#!/usr/bin/env python3
"""Reveal a Codex long-run live.md file in the platform file manager."""

from __future__ import annotations

import argparse
import os
import pathlib
import platform
import shutil
import subprocess
import sys
from typing import Optional


def is_open_by_process(path: pathlib.Path) -> bool:
    if platform.system() == "Windows":
        return is_open_by_process_windows(path)

    lsof = shutil.which("lsof")
    if not lsof:
        return False
    try:
        result = subprocess.run(
            [lsof, "-F", "n", "--", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and str(path) in result.stdout


def is_open_by_process_windows(path: pathlib.Path) -> bool:
    handle = shutil.which("handle.exe") or shutil.which("handle64.exe")
    if not handle:
        return False
    try:
        result = subprocess.run(
            [handle, "-nobanner", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and str(path).lower() in result.stdout.lower()


def is_wsl() -> bool:
    if "WSL_DISTRO_NAME" in os.environ:
        return True
    try:
        return "microsoft" in pathlib.Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False


def wsl_windows_path(path: pathlib.Path) -> Optional[str]:
    wslpath = shutil.which("wslpath")
    if not wslpath:
        return None
    try:
        result = subprocess.run(
            [wslpath, "-w", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def reveal_in_file_manager(path: pathlib.Path) -> None:
    system = platform.system()

    if system == "Darwin":
        subprocess.run(["open", "-R", str(path)], check=True)
        print(f"revealed live file in Finder: {path}")
        return

    if system == "Windows":
        subprocess.run(["explorer.exe", f"/select,{path}"], check=True)
        print(f"revealed live file in File Explorer: {path}")
        return

    if is_wsl():
        windows_path = wsl_windows_path(path)
        if windows_path:
            subprocess.run(["explorer.exe", f"/select,{windows_path}"], check=True)
            print(f"revealed live file in Windows File Explorer: {windows_path}")
            return

    opener = shutil.which("xdg-open")
    if opener:
        subprocess.run([opener, str(path.parent)], check=True)
        print(f"opened containing folder: {path.parent}")
        return

    print(f"no supported file manager opener found; live file path: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("live_file", help="live.md file to reveal")
    parser.add_argument("--skip-if-open", action="store_true", help="skip reveal if the file appears open")
    args = parser.parse_args()

    path = pathlib.Path(args.live_file).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"live file does not exist: {path}")

    if args.skip_if_open and is_open_by_process(path):
        print(f"live file already appears open; not revealing it: {path}")
        return 0

    reveal_in_file_manager(path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nreveal cancelled", file=sys.stderr)
        raise SystemExit(130)

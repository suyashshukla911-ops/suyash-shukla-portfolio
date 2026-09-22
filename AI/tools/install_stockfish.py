from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = os.environ.get("CHESSMIND_STOCKFISH_VERSION", "19")
BASE = "https://github.com/official-stockfish/Stockfish/releases/download/sf_19"
WINDOWS_URL = f"{BASE}/stockfish-windows-x86-64-universal.zip"
LINUX_URL = f"{BASE}/stockfish-linux-x86-64-universal.tar.gz"

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "engines" / "stockfish"


def download(url: str, destination: Path) -> None:
    print(f"Downloading Stockfish from {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "ChessMind/Final"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)


def find_binary(folder: Path) -> Path | None:
    for path in folder.rglob("stockfish*"):
        if path.is_file() and path.name.lower() in {"stockfish", "stockfish.exe"}:
            return path
    return None


def install(platform_name: str) -> None:
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    expected = ENGINE_DIR / ("stockfish.exe" if platform_name == "windows" else "stockfish")
    if expected.exists():
        print(f"Stockfish already installed: {expected}")
        return

    url = WINDOWS_URL if platform_name == "windows" else LINUX_URL
    with tempfile.TemporaryDirectory(prefix="chessmind_sf_") as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / ("stockfish.zip" if platform_name == "windows" else "stockfish.tar.gz")
        download(url, archive)
        unpack = tmpdir / "unpack"
        unpack.mkdir()
        if platform_name == "windows":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(unpack)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(unpack)

        binary = find_binary(unpack)
        if binary is None:
            raise RuntimeError("Downloaded archive did not contain a Stockfish executable.")
        shutil.copy2(binary, expected)

    if platform_name != "windows":
        expected.chmod(expected.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed: {expected}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["windows", "linux"], default="windows")
    args = parser.parse_args()
    install(args.platform)

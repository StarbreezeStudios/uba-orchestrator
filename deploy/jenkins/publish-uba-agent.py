"""Publish an immutable UbaAgent package with hashes to the software share."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path


ARTIFACT_ROOT = Path(r"\\devopsfs.starbreeze.com\devops\Software-Installs\UnrealBuildAccelerator\UbaAgent")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> int:
    workspace_root = Path(r"D:\jkws") / os.environ["COMPUTERNAME"] / "payday3" / "trunk"
    binary_directory = workspace_root / "Engine" / "Binaries" / "Win64" / "UnrealBuildAccelerator" / "x64"
    build_number = int(os.environ["BUILD_NUMBER"])
    artifact_directory = ARTIFACT_ROOT / f"build-{build_number}"

    if not ARTIFACT_ROOT.is_dir():
        raise FileNotFoundError(f"UBA artifact root is not available: {ARTIFACT_ROOT}")
    if artifact_directory.exists():
        raise FileExistsError(f"Refusing to overwrite existing UBA artifact: {artifact_directory}")

    artifact_directory.mkdir()
    try:
        files = [binary_directory / "UbaAgent.exe", *binary_directory.glob("*.dll")]
        for source in files:
            if not source.is_file():
                raise FileNotFoundError(f"Required UBA binary was not found: {source}")
            shutil.copy2(source, artifact_directory / source.name)

        manifest = {
            "build_number": build_number,
            "source_workspace": str(workspace_root),
            "files": [
                {"name": path.name, "sha256": sha256(path), "size_bytes": path.stat().st_size}
                for path in sorted(artifact_directory.iterdir(), key=lambda item: item.name)
                if path.is_file()
            ],
        }
        (artifact_directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(artifact_directory, ignore_errors=True)
        raise

    print(f"Published UBA agent artifact: {artifact_directory}")
    for path in sorted(artifact_directory.iterdir(), key=lambda item: item.name):
        if path.is_file():
            print(f"{path.name}\t{path.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

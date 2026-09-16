"""Build UbaAgent from the machine-local Unreal Engine workspace."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def main() -> int:
    workspace_root = Path(r"D:\jkws") / os.environ["COMPUTERNAME"] / "payday3" / "trunk"
    build_script = workspace_root / "Engine" / "Build" / "BatchFiles" / "Build.bat"
    uba_agent = workspace_root / "Engine" / "Binaries" / "Win64" / "UnrealBuildAccelerator" / "x64" / "UbaAgent.exe"
    if not build_script.is_file():
        raise FileNotFoundError(f"Unreal Build.bat was not found: {build_script}")

    print(f"Building UbaAgent from {workspace_root}")
    subprocess.run([
        "cmd.exe", "/d", "/c", str(build_script),
        "UbaAgent", "Win64", "Development", "-WaitMutex", "-NoHotReload",
    ], cwd=workspace_root, check=True)
    if not uba_agent.is_file():
        raise FileNotFoundError(f"UbaAgent.exe was not produced: {uba_agent}")

    print(f"Built UbaAgent: {uba_agent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

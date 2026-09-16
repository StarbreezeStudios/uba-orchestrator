"""Synchronize the UBA Perforce workspace used by the Jenkins build node."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    workspace_root = Path(r"D:\jkws") / os.environ["COMPUTERNAME"] / "payday3" / "trunk"
    p4_path = shutil.which("p4.exe")
    if not workspace_root.is_dir():
        raise FileNotFoundError(f"Perforce workspace root was not found: {workspace_root}")
    if not p4_path:
        raise RuntimeError("p4.exe was not found in PATH")

    print(f"Syncing Perforce workspace to head: {workspace_root}")
    subprocess.run([p4_path, "sync"], cwd=workspace_root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

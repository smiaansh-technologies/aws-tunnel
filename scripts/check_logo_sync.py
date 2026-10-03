"""Dev-only tool: fail if assets/logo.png is stale relative to logo.svg.

The SVG is the master artwork; the PNG is what the app actually loads. An
editor auto-save once replaced logo.svg with an older design while the PNG
kept the approved one, which would have silently reverted the icon for
anyone regenerating it. This guard makes that drift a hard failure.

Usage:
    uv run --group test python scripts/check_logo_sync.py
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "logo.svg"
PNG = ROOT / "assets" / "logo.png"
SCRATCH = ROOT / "assets" / "_logo_sync_check.png"
RENDER = ROOT / "scripts" / "render_logo_png.py"


def main() -> int:
    if not PNG.exists():
        print(f"FAIL: {PNG} is missing. Run scripts/render_logo_png.py.")
        return 1

    shutil.copy2(PNG, SCRATCH)
    try:
        subprocess.run(
            [sys.executable, str(RENDER)], cwd=ROOT, check=True, capture_output=True
        )
        committed = SCRATCH.read_bytes()
        regenerated = PNG.read_bytes()
    finally:
        SCRATCH.unlink(missing_ok=True)

    if committed == regenerated:
        print(f"OK: logo.png matches logo.svg ({hashlib.sha256(committed).hexdigest()[:16]})")
        return 0

    print("FAIL: assets/logo.png is out of sync with assets/logo.svg")
    print(f"  committed:   {hashlib.sha256(committed).hexdigest()[:16]}")
    print(f"  regenerated: {hashlib.sha256(regenerated).hexdigest()[:16]}")
    print("  Fix: run scripts/render_logo_png.py and commit the updated PNG.")
    # Leave the tree as we found it so the diff only shows real problems.
    PNG.write_bytes(committed)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

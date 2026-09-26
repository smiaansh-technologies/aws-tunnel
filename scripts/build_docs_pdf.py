"""
Dev-only tool: render docs/user_guide.html into a PDF manual using
Playwright (Chromium).

Output: docs/AWS-Tunnel-User-Guide.pdf

Run from the project root:
    pip install playwright          # dev dependency, never bundled in the app
    playwright install chromium     # one-time browser download
    python scripts/build_docs_pdf.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

SOURCE = ROOT / "docs" / "user_guide.html"
OUTPUT = ROOT / "docs" / "AWS-Tunnel-User-Guide.pdf"


def build_pdf() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Guide not found: {SOURCE}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(SOURCE.as_uri())
        # Give images a moment to load before printing.
        page.wait_for_load_state("networkidle")
        page.emulate_media(media="print")
        page.pdf(
            path=str(OUTPUT),
            format="A4",
            print_background=True,
            margin={"top": "18mm", "bottom": "18mm", "left": "15mm", "right": "15mm"},
        )
        browser.close()

    print(f"PDF written: {OUTPUT}")


if __name__ == "__main__":
    build_pdf()

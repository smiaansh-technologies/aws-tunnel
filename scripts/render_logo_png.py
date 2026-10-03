"""Dev-only tool: rasterize assets/logo.svg into assets/logo.png.

The SVG is the master artwork. Tk and pystray both want a raster, and
regenerating it here keeps a single source of truth instead of committing
a hand-drawn PNG that can drift from the SVG.

Usage:
    uv run --group test python scripts/render_logo_png.py
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "logo.svg"
OUTPUT = ROOT / "assets" / "logo.png"
SIZE = 256

HTML = (
    "<html><head><style>"
    "html,body{{margin:0;padding:0;background:transparent}}"
    "svg{{width:{size}px;height:{size}px;display:block}}"
    "</style></head><body>{svg}</body></html>"
)


def main() -> None:
    svg = SOURCE.read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": SIZE, "height": SIZE},
            device_scale_factor=1,
        )
        page.set_content(HTML.format(size=SIZE, svg=svg))
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUTPUT), omit_background=True)
        browser.close()

    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
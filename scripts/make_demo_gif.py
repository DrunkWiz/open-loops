"""Record a ~10 second GIF of the demo for the README.

Needs the app running locally and Playwright (dev-only, not in requirements.txt):
    pip install playwright
    streamlit run app.py                       # in another terminal
    python -m scripts.make_demo_gif            # uses the Microsoft Edge (or Chrome) already installed

Writes assets/demo.gif. Only the saved demo is used, so no AI calls are made.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "demo.gif"
VIEWPORT = {"width": 1280, "height": 800}
GIF_WIDTH = 960
GREEN = (118, 185, 0)


def font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


CAPTION_FONT = font(26)


class Recorder:
    def __init__(self, page: Page):
        self.page = page
        self.frames: list[tuple[Image.Image, int]] = []

    def settle(self, seconds: float = 1.2) -> None:
        """Wait for Streamlit to finish rerunning (its 'running' indicator disappears)."""
        time.sleep(0.3)
        deadline = time.time() + 8
        while time.time() < deadline:
            if not self.page.locator('[data-testid="stStatusWidget"]').is_visible():
                break
            time.sleep(0.2)
        time.sleep(seconds)

    def shot(self, caption: str, ms: int, ring: tuple[float, float] | None = None) -> None:
        image = Image.open(io.BytesIO(self.page.screenshot())).convert("RGB")
        draw = ImageDraw.Draw(image, "RGBA")
        if ring:  # where the next click happens
            x, y = ring
            for radius, alpha in ((34, 70), (22, 255)):
                draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                             outline=GREEN + (alpha,), width=5)
        if caption:
            w = draw.textlength(caption, font=CAPTION_FONT)
            x0, y0 = 24, image.height - 76
            draw.rounded_rectangle((x0, y0, x0 + w + 36, y0 + 50), radius=25, fill=(12, 30, 10, 225))
            draw.text((x0 + 18, y0 + 9), caption, font=CAPTION_FONT, fill=(244, 251, 234))
        self.frames.append((image, ms))

    def click(self, target: Locator, caption: str, ms: int = 650) -> None:
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        center = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2) if box else None
        self.shot(caption, ms, ring=center)
        target.click()
        self.settle()

    def scroll_main_to(self, target: Locator, offset: int = 16) -> None:
        """Scroll Streamlit's main pane so `target` sits near the top of the viewport."""
        target.evaluate(
            """(el, offset) => {
                let s = el.parentElement;
                while (s && !(s.scrollHeight > s.clientHeight + 5 && getComputedStyle(s).overflowY !== 'visible'))
                    s = s.parentElement;
                (s || document.scrollingElement).scrollTop += el.getBoundingClientRect().top - offset;
            }""",
            offset,
        )
        time.sleep(0.4)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        scaled = []
        for image, ms in self.frames:
            h = round(image.height * GIF_WIDTH / image.width)
            small = image.resize((GIF_WIDTH, h), Image.LANCZOS)
            scaled.append((small.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE), ms))
        first, *rest = [img for img, _ in scaled]
        first.save(path, save_all=True, append_images=rest, duration=[ms for _, ms in scaled], loop=0,
                   optimize=True, disposal=2)


def button(page: Page, pattern: str) -> Locator:
    return page.get_by_role("button", name=re.compile(pattern)).first


def record(url: str, out: Path) -> None:
    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception:  # noqa: BLE001 — try the next installed browser
                continue
        if browser is None:
            sys.exit("Couldn't start Edge or Chrome. Install one, or run `playwright install chromium`.")
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        page.goto(url)
        page.get_by_text("New here? See it in 10 seconds").wait_for(timeout=30_000)
        rec = Recorder(page)
        try:  # the collapse button only appears on hover
            page.locator('[data-testid="stSidebar"]').hover()
            page.locator('[data-testid="stSidebarCollapseButton"] button').first.click(timeout=3000)
        except Exception:  # noqa: BLE001 — fall back to hiding the sidebar for the recording
            page.add_style_tag(content='[data-testid="stSidebar"] {display: none;}')
        rec.settle(0.8)

        rec.shot("Emails, meeting notes, family chats: one list of promises", 1500)
        rec.click(button(page, "Show me Grace's week"), "Load Grace's week (saved Nemotron run)")

        rec.scroll_main_to(page.get_by_text(re.compile(r"change\(s\) from newer documents")))
        rec.shot("A newer email moved Hamid's deadline Oct 9 → Oct 6: caught, with the quote", 1900)
        rec.click(button(page, "Accept"), "Nothing changes until you accept (and you can undo)")
        rec.shot("Accepted: the list updates, with Undo if you change your mind", 1100)

        page.locator('[data-testid="stMainBlockContainer"]').evaluate("el => el.scrollIntoView()")
        rec.scroll_main_to(page.locator(".hero"), offset=0)
        rec.click(button(page, "Overdue"), "Every stat card opens its list")
        rec.shot("Overdue: call the caterer for Grandma's 80th", 1300)

        rec.scroll_main_to(page.locator(".hero"), offset=0)
        today_tab = page.get_by_role("radio", name=re.compile("Today")).first  # navigation options are radios
        rec.click(today_tab, "Work and family in one morning view")
        rec.scroll_main_to(page.get_by_text("What I owe"), offset=110)
        rec.shot("Open Loops · every promise, tracked", 1900)
        browser.close()
    rec.save(out)
    total = sum(ms for _, ms in rec.frames) / 1000
    print(f"Saved {out.relative_to(ROOT)}: {len(rec.frames)} frames, {total:.1f}s, "
          f"{out.stat().st_size / 1_000_000:.1f} MB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8599")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    record(args.url, Path(args.out))

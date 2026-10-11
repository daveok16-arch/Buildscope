"""U7 screenshots: motion-system states at 720 and 390. Read-only, local server."""
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp3"


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, w, h in (("720", 720, 1280), ("390", 390, 844)):
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()

            # Home top: reveal settled, hero visible instantly.
            page.goto(BASE + "/", wait_until="networkidle")
            page.wait_for_timeout(900)
            page.screenshot(path=f"{OUT}/u7_home_top_{name}.png")

            # Feed: skeleton state, revealed by a (prevented) submit.
            page.goto(BASE + "/opportunities", wait_until="networkidle")
            page.evaluate("""() => {
                const f = document.querySelector('form[action*="opportunities"]');
                f.addEventListener('submit', e => e.preventDefault(), true);
                f.dispatchEvent(new Event('submit', {cancelable:true}));
            }""")
            page.wait_for_timeout(120)
            page.screenshot(path=f"{OUT}/u7_feed_skeleton_{name}.png")
            ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""U-release screenshots: the surfaces the Director compares, at 720 and 390. Read-only."""
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

            page.goto(BASE + "/", wait_until="networkidle")
            page.wait_for_timeout(800)
            page.screenshot(path=f"{OUT}/live_{name}_home_top.png")

            # Mobile drawer open.
            if w < 900:
                page.click("#mobile-menu-open-btn")
                page.wait_for_timeout(500)
                page.screenshot(path=f"{OUT}/live_{name}_drawer.png")
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)

            # Feed with the city picker open. Below 900px the filters live in a bottom sheet,
            # so open that first.
            page.goto(BASE + "/opportunities", wait_until="networkidle")
            if w < 900:
                page.click("#mobile-filter-btn")
                page.wait_for_timeout(500)
            page.click(".bs-combobox-trigger")
            page.wait_for_timeout(400)
            page.screenshot(path=f"{OUT}/live_{name}_city_picker.png")
            page.keyboard.press("Escape")
            if w < 900:
                try:
                    page.click("#filter-sheet-close")
                except Exception:
                    page.keyboard.press("Escape")
                page.wait_for_timeout(300)

            # Sort control open (segmented control is always visible; capture it in place).
            page.goto(BASE + "/opportunities", wait_until="networkidle")
            page.screenshot(path=f"{OUT}/live_{name}_sort.png")

            # Companies with the role control open.
            page.goto(BASE + "/companies", wait_until="networkidle")
            try:
                if w < 900:
                    page.click("#mobile-filter-btn")
                    page.wait_for_timeout(400)
                page.click(".bs-combobox-trigger")
                page.wait_for_timeout(300)
            except Exception:
                pass
            page.screenshot(path=f"{OUT}/live_{name}_companies_role.png")

            # New filings.
            page.goto(BASE + "/opportunities/new", wait_until="networkidle")
            page.screenshot(path=f"{OUT}/live_{name}_new_filings.png")

            # Trends top + chart.
            page.goto(BASE + "/trends", wait_until="networkidle")
            page.wait_for_timeout(600)
            page.screenshot(path=f"{OUT}/live_{name}_trends_top.png")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(500)
            page.screenshot(path=f"{OUT}/live_{name}_trends_chart.png")

            # A dossier.
            page.goto(BASE + "/opportunities", wait_until="networkidle")
            href = page.eval_on_selector(".card a[href*='/opportunities/']", "a => a.getAttribute('href')")
            page.goto(BASE + href, wait_until="networkidle")
            page.screenshot(path=f"{OUT}/live_{name}_dossier.png")
            ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

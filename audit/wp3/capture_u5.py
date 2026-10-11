"""U5 evidence: Trends cards (number+label, one-line basis, definition disclosure, 2x2 grid)
and the chart (axis, value labels, table alternative, draw-in). Local demo server, read-only."""
import json
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp3"


def main() -> int:
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, w, h in (("720", 720, 1280), ("390", 390, 844), ("1440", 1440, 900)):
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            page.goto(BASE + "/trends", wait_until="networkidle")
            page.screenshot(path=f"{OUT}/u5_trends_{name}.png", full_page=True)

            grid = page.eval_on_selector(
                ".stat-strip-trends",
                "el => getComputedStyle(el).gridTemplateColumns",
            )
            cards = page.eval_on_selector_all(
                ".stat-trend",
                """els => els.map(e => ({
                    value: (e.querySelector('.stat-value')||{}).textContent,
                    label: (e.querySelector('.stat-label')||{}).textContent,
                    has_basis: !!e.querySelector('.stat-basis'),
                    has_disclosure: !!e.querySelector('details.stat-def'),
                }))""",
            )
            # Paragraphs visible by default = the one-line basis only. The full definition
            # lives inside a closed <details>, which the browser does not render.
            visible_paras = page.eval_on_selector_all(
                ".stat-trend > p",
                "els => els.filter(e => e.offsetParent !== null).length",
            )
            details_state = page.eval_on_selector_all(
                "details.stat-def", "els => els.map(e => e.open)"
            )
            chart = {
                "svg": page.locator("svg.bar-chart").count(),
                "bars": page.locator("rect.bar-chart-bar").count(),
                "value_labels": page.locator("svg.bar-chart text").count(),
                "table_alt": page.locator("details.chart-data").count(),
            }
            out[name] = {"grid": grid, "cards": cards, "visible_default_paras": visible_paras,
                         "details_open": details_state, "chart": chart}
            ctx.close()

        # Window toggles: any disabled (identical-data) toggles.
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(BASE + "/trends", wait_until="networkidle")
        out["window_toggles"] = page.eval_on_selector_all(
            ".results-header-bar a.btn, .results-header-bar span.btn[aria-disabled]",
            "els => els.map(e => ({text: e.textContent.trim(), disabled: e.getAttribute('aria-disabled')==='true', href: e.getAttribute('href')}))",
        )
        ctx.close()
        browser.close()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

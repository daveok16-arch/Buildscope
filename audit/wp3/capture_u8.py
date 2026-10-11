"""U8 evidence: compact footer on narrow screens + Markets orphaned-card check.

Read-only. Measures the footer brand block and link columns at 390/720/1440, and reports
whether any market card sits alone in its grid row (an orphan). Writes audit/wp3/u8_footer.json
and screenshots audit/wp3/u8_footer_{390,720,1440}.png.
"""
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
        for name, w, h in (("390", 390, 844), ("720", 720, 1280), ("1440", 1440, 900)):
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            page.goto(BASE + "/", wait_until="networkidle")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(500)
            footer = page.evaluate(
                """() => {
                    const grid = document.querySelector('.footer-grid');
                    const brand = document.querySelector('.footer-brand');
                    const cols = [...grid.children].map(c => ({
                        cls: c.className, w: Math.round(c.getBoundingClientRect().width),
                        top: Math.round(c.getBoundingClientRect().top)}));
                    return {cols, grid_cols: getComputedStyle(grid).gridTemplateColumns,
                            brand_span: brand ? getComputedStyle(brand).gridColumn : null,
                            legal_font: getComputedStyle(document.querySelector('.footer-legal')).fontSize};
                }"""
            )
            out[f"footer_{name}"] = footer
            page.screenshot(path=f"{OUT}/u8_footer_{name}.png", full_page=False)

            # Markets orphan check. A lone card is only an orphan when the grid has more than one
            # column AND the card sits against the left edge rather than centred.
            page.goto(BASE + "/markets", wait_until="networkidle")
            orphan = page.evaluate(
                """() => {
                    const grid = document.querySelector('.card-grid');
                    const cards = [...grid.querySelectorAll(':scope > .card')];
                    const ncols = getComputedStyle(grid).gridTemplateColumns.split(' ').length;
                    const rows = {};
                    cards.forEach((c, i) => { const t = Math.round(c.getBoundingClientRect().top); (rows[t] ||= []).push(i); });
                    const counts = Object.values(rows).map(r => r.length);
                    const gb = grid.getBoundingClientRect();
                    const last = cards[cards.length - 1].getBoundingClientRect();
                    return {total: cards.length, columns: ncols, rows: counts,
                            last_left_gap: Math.round(last.left - gb.left),
                            last_right_gap: Math.round(gb.right - last.right),
                            orphans: (ncols > 1 && counts[counts.length - 1] === 1
                                      && Math.abs((last.left - gb.left) - (gb.right - last.right)) > 12) ? 1 : 0};
                }"""
            )
            out[f"markets_{name}"] = orphan
            ctx.close()
        browser.close()

    with open(f"{OUT}/u8_footer.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

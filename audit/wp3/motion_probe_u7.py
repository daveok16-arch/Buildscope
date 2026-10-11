"""U7 evidence: the motion system (spec behaviours 1-10) on live routes.

Read-only. For each route: JS-on reveal (content visible, no first-paint animation > 300ms),
count-up ends on the exact snapshot number, sticky header compacts, skeleton shows on submit,
no CSP console errors; reduced-motion makes everything instant; JS-off leaves every page fully
readable (progressive enhancement). Writes audit/wp3/u7_motion.json and prints a summary.
"""
import json
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp3"
ROUTES = {
    "home": "/",
    "opportunities": "/opportunities",
    "companies": "/companies",
    "trends": "/trends",
    "detail": None,  # filled from the feed
}


def first_slug(page):
    return page.eval_on_selector(
        ".card a[href*='/opportunities/']",
        "a => a.getAttribute('href')",
    )


def main() -> int:
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        # ---- JS on, normal motion ----
        ctx = browser.new_context(viewport={"width": 720, "height": 1280}, device_scale_factor=1)
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(BASE + "/opportunities", wait_until="networkidle")
        ROUTES["detail"] = first_slug(page)

        for name, path in ROUTES.items():
            page.goto(BASE + path, wait_until="domcontentloaded")
            has_js_class = page.evaluate("document.documentElement.classList.contains('bs-js')")
            h1 = page.eval_on_selector(
                "h1", "el => ({vis: el.offsetParent !== null, txt: el.textContent.slice(0,40)})"
            )
            page.wait_for_timeout(1500)
            reveal = page.evaluate(
                """() => {
                    const els = [...document.querySelectorAll('[data-reveal], .card, .opportunity-card, .stat-card, .chart-figure')];
                    const animated = els.filter(e => e.classList.contains('bs-anim-on'));
                    const settled = animated.filter(e => e.classList.contains('bs-in'));
                    return {total: els.length, animated: animated.length, settled: settled.length};
                }"""
            )
            counts = page.evaluate(
                """() => [...document.querySelectorAll('[data-count]')].map(e => ({
                    target: e.getAttribute('data-count'), text: e.textContent.trim()}))"""
            )
            # Scroll the whole page: every reveal target must end visible (bs-in), none stuck
            # at opacity 0. Above-the-fold-only settling at load is correct behaviour 1.
            height = page.evaluate("document.body.scrollHeight")
            for y in range(0, height, 900):
                page.evaluate(f"window.scrollTo(0,{y})")
                page.wait_for_timeout(120)
            page.wait_for_timeout(600)
            settled = page.evaluate(
                """() => {
                    const els = [...document.querySelectorAll('[data-reveal], .card, .opportunity-card, .stat-card, .chart-figure')];
                    return {settled_after_scroll: els.filter(e => e.classList.contains('bs-in')).length,
                            still_invisible: els.filter(e => getComputedStyle(e).opacity === '0').length};
                }"""
            )
            out[name] = {"bs_js": has_js_class, "h1_visible": h1["vis"], "h1": h1["txt"],
                         "reveal": reveal, "settled": settled, "counts": counts}
        out["console_errors_js_on"] = errors

        # ---- sticky header compacts after 24px ----
        page.goto(BASE + "/", wait_until="networkidle")
        before = page.evaluate("document.querySelector('.site-header').classList.contains('bs-compact')")
        page.evaluate("window.scrollTo(0, 200)")
        page.wait_for_timeout(120)
        after = page.evaluate("document.querySelector('.site-header').classList.contains('bs-compact')")
        out["sticky_header"] = {"compact_at_top": before, "compact_after_scroll": after}

        # ---- skeleton on submit (behaviour 7) ----
        page.goto(BASE + "/opportunities", wait_until="networkidle")
        page.evaluate("""() => {
            const f = document.querySelector('form[action*="opportunities"]');
            f.addEventListener('submit', e => e.preventDefault(), true);
            f.dispatchEvent(new Event('submit', {cancelable:true}));
        }""")
        page.wait_for_timeout(60)
        out["skeleton_on_submit"] = page.evaluate(
            "document.querySelector('[data-bs-results]').classList.contains('is-loading')")
        ctx.close()

        # ---- reduced motion: everything instant, content visible ----
        ctx2 = browser.new_context(viewport={"width": 720, "height": 1280}, reduced_motion="reduce")
        page2 = ctx2.new_page()
        page2.goto(BASE + "/opportunities", wait_until="domcontentloaded")
        rm = page2.evaluate(
            """() => {
                const els = [...document.querySelectorAll('.card, .opportunity-card, .stat-card')];
                return {
                    settled_immediately: els.every(e => e.classList.contains('bs-in')),
                    bar_transform: getComputedStyle(document.querySelector('.bar-chart-bar') || document.body).transform,
                };
            }"""
        )
        out["reduced_motion"] = rm
        ctx2.close()

        # ---- JS off: every page still readable (progressive enhancement) ----
        ctx3 = browser.new_context(viewport={"width": 720, "height": 1280}, java_script_enabled=False)
        page3 = ctx3.new_page()
        nojs = {}
        for name, path in ROUTES.items():
            page3.goto(BASE + path, wait_until="domcontentloaded")
            nojs[name] = page3.evaluate(
                """() => ({
                    has_js_class: document.documentElement.classList.contains('bs-js'),
                    h1: !!document.querySelector('h1'),
                    cards: document.querySelectorAll('.opportunity-card, .card').length,
                    native_selects_visible: [...document.querySelectorAll('select')].filter(s => s.offsetParent !== null).length,
                })"""
            )
        out["no_js"] = nojs
        ctx3.close()
        browser.close()

    with open(f"{OUT}/u7_motion.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

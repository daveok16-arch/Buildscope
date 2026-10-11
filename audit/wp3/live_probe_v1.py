"""V1/V2 — LIVE assessment against the Render preview (read-only).

Every number here comes from https://buildscope-xppz.onrender.com, not the local copy.
Writes screenshots to audit/live/V_*.png and a JSON summary to audit/wp3/v_live.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
AXE = (ROOT / "audit" / "a11y" / "axe.min.js").read_text()
BASE = "https://buildscope-xppz.onrender.com"
OUT = ROOT / "audit" / "live"
ROUTES = [
    "/", "/opportunities", "/opportunities/new", "/companies", "/markets", "/markets/dfw",
    "/changes", "/trends", "/analytics", "/reports", "/how-it-works", "/guides",
    "/trades", "/trades/commercial-hvac", "/commercial-construction-leads", "/signin", "/signup",
]
VPS = [("390", 390, 844), ("720", 720, 1280), ("1440", 1440, 900)]
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]

result = {"per_route": {}, "axe": {}, "city_picker": {}, "console": {}, "screenshots": []}


def axe(page):
    nonce = page.evaluate(
        "() => { const s=document.querySelector('script[nonce]'); return s ? s.nonce || s.getAttribute('nonce') : null; }"
    )
    page.evaluate(
        """({src, nonce}) => { const s = document.createElement('script');
             if (nonce) s.setAttribute('nonce', nonce);
             s.textContent = src; document.head.appendChild(s); }""",
        {"src": AXE, "nonce": nonce},
    )
    return page.evaluate(
        """async (tags) => {
            const r = await window.axe.run(document, {runOnly: {type: 'tag', values: tags}});
            return r.violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.length}));
        }""",
        TAGS,
    )


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for vname, w, h in VPS:
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        for route in ROUTES:
            page = ctx.new_page()
            console = []
            page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.goto(BASE + route, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(500)
            data = page.evaluate(
                """() => {
                    const vw = window.innerWidth;
                    const out = [];
                    document.querySelectorAll('*').forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width === 0) return;
                        if (r.right > vw + 1 || r.left < -1)
                            out.push({tag: el.tagName.toLowerCase(),
                                      cls: (el.className||'').toString().slice(0,40),
                                      l: Math.round(r.left), rr: Math.round(r.right)});
                    });
                    const small = [];
                    document.querySelectorAll('button,input,select,textarea,a.btn,[role=button]').forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) return;
                        if (r.height < 44 || r.width < 44)
                            small.push({tag: el.tagName.toLowerCase(),
                                        cls: (el.className||'').toString().slice(0,30),
                                        w: Math.round(r.width), h: Math.round(r.height)});
                    });
                    const tiny = [];
                    document.querySelectorAll('*').forEach(el => {
                        if (!el.textContent || !el.textContent.trim()) return;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) return;
                        const cs = getComputedStyle(el);
                        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return;
                        if (parseFloat(cs.fontSize) < 12)
                            tiny.push({tag: el.tagName.toLowerCase(),
                                       cls: (el.className||'').toString().slice(0,30),
                                       px: parseFloat(cs.fontSize)});
                    });
                    const selects = [...document.querySelectorAll('select')].filter(s => {
                        const r = s.getBoundingClientRect();
                        return r.width > 0 && r.height > 0 && getComputedStyle(s).visibility !== 'hidden';
                    });
                    return {sw: document.documentElement.scrollWidth, vw,
                            overflow: out.length, offenders: out.slice(0, 6),
                            tap_small: small.length, tap_examples: small.slice(0, 5),
                            font_under_12: tiny.length, font_examples: tiny.slice(0, 5),
                            visible_selects: selects.length};
                }"""
            )
            viol = axe(page)
            total = sum(v["nodes"] for v in viol)
            result["per_route"][f"{vname}{route}"] = data
            result["axe"][f"{vname}{route}"] = {"total": total, "violations": viol}
            result["console"][f"{vname}{route}"] = console
            page.close()
        ctx.close()

    # ---- city picker at 390 and 720 (live) ----
    for w, h in ((390, 844), (720, 1280)):
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        page = ctx.new_page()
        page.goto(BASE + "/opportunities", wait_until="networkidle", timeout=45000)
        page.evaluate(
            """() => {
                const boxes = [...document.querySelectorAll('.bs-combobox')];
                const city = boxes.find(b => /cities/i.test(b.textContent));
                (city || boxes[0]).querySelector('.bs-combobox-trigger').click();
            }"""
        )
        page.wait_for_timeout(700)
        r = page.evaluate(
            """() => {
                const boxes = [...document.querySelectorAll('.bs-combobox')];
                const city = boxes.find(b => /cities/i.test(b.textContent)) || boxes[0];
                const pop = city.querySelector('.bs-combobox-pop:not([hidden])')
                            || document.querySelector('.bs-combobox-pop:not([hidden])');
                if (!pop) return {open: false};
                const pr = pop.getBoundingClientRect();
                const ft = document.querySelector('.site-footer');
                const ftr = ft ? ft.getBoundingClientRect().top : 1e9;
                return {open: true, top: Math.round(pr.top), bottom: Math.round(pr.bottom),
                        height: Math.round(pr.height), vh: window.innerHeight,
                        in_viewport: pr.top >= -1 && pr.bottom <= window.innerHeight + 1,
                        below_footer: pr.top > ftr, position: getComputedStyle(pop).position,
                        parent: pop.parentElement.tagName};
            }"""
        )
        result["city_picker"][str(w)] = r
        page.screenshot(path=str(OUT / f"V_citypicker_open_{w}.png"))
        result["screenshots"].append(f"V_citypicker_open_{w}.png")
        page.close()
        ctx.close()

    browser.close()

(ROOT / "audit" / "wp3" / "v_live.json").write_text(json.dumps(result, indent=2))
print("per-route overflow/tap/font/select totals:")
for k, d in result["per_route"].items():
    print(f"  {k:28s} ov={d['overflow']:3d} tap<44={d['tap_small']:3d} "
          f"font<12={d['font_under_12']:3d} sel={d['visible_selects']} sw={d['sw']}/{d['vw']}")
print("axe total instances:", sum(v["total"] for v in result["axe"].values()))
print("axe routes with violations:",
      [(k, v["violations"]) for k, v in result["axe"].items() if v["total"]])
print("city picker:", result["city_picker"])
print("console errors:", {k: v for k, v in result["console"].items() if v})

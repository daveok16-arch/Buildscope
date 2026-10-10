"""WP3 capture: full-page screenshots of every route at 3 viewports, plus an
overflow / clipped-text / tap-target probe. Local demo server only (read-only)."""
import json
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp3"

ROUTES = [
    ("home", "/"),
    ("opportunities", "/opportunities"),
    ("opp_detail", "/opportunities/1701-s-university-dr-fort-worth-9c5275"),
    ("markets", "/markets"),
    ("markets_dfw", "/markets/dfw"),
    ("companies", "/companies"),
    ("changes", "/changes"),
    ("trends", "/trends"),
    ("analytics", "/analytics"),
    ("reports", "/reports"),
    ("how_it_works", "/how-it-works"),
    ("trades", "/trades"),
    ("trades_hvac", "/trades/commercial-hvac"),
    ("commercial_leads", "/commercial-construction-leads"),
    ("guides", "/guides"),
    ("signin", "/signin"),
    ("signup", "/signup"),
    ("not_found", "/this-does-not-exist-xyz"),
]

VIEWPORTS = [("mobile", 390, 844), ("tablet", 820, 1180), ("desktop", 1440, 900)]

OVERFLOW_JS = r"""
() => {
  const doc = document.documentElement;
  const vw = window.innerWidth;
  const wide = doc.scrollWidth - vw;
  const offenders = [];
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    if (r.right > vw + 1 || r.left < -1) {
      const cs = getComputedStyle(el);
      if (cs.position === 'fixed') return;
      offenders.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 80),
        right: Math.round(r.right), left: Math.round(r.left), width: Math.round(r.width),
        text: (el.textContent || '').trim().slice(0, 60)
      });
    }
  });
  offenders.sort((a,b) => b.right - a.right);
  return {vw, scrollWidth: doc.scrollWidth, overflow: wide, offenders: offenders.slice(0, 8)};
}
"""

TAP_JS = r"""
() => {
  const small = [];
  document.querySelectorAll('a, button, input, select, [role=button], summary').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return;
    if (r.height < 44 || r.width < 24) {
      small.push({tag: el.tagName.toLowerCase(),
        cls: (el.className && el.className.toString ? el.className.toString() : '').slice(0,60),
        w: Math.round(r.width), h: Math.round(r.height),
        text: (el.textContent||'').trim().slice(0,40)});
    }
  });
  return small.slice(0, 12);
}
"""

results = {"routes": {}}
with sync_playwright() as p:
    browser = p.chromium.launch()
    for vp_name, w, h in VIEWPORTS:
        for name, route in ROUTES:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            pg = ctx.new_page()
            try:
                pg.goto(BASE + route, wait_until="networkidle", timeout=30000)
                pg.wait_for_timeout(500)
                pg.screenshot(path=f"{OUT}/{vp_name}__{name}.png", full_page=True)
                info = pg.evaluate(OVERFLOW_JS)
                tap = pg.evaluate(TAP_JS) if vp_name == "mobile" else []
                results["routes"][f"{vp_name}__{name}"] = {
                    "route": route, "viewport": vp_name,
                    "overflow": info["overflow"], "scrollWidth": info["scrollWidth"],
                    "vw": info["vw"], "offenders": info["offenders"], "small_targets": tap,
                }
                flag = "OVERFLOW" if info["overflow"] > 1 else "ok"
                print(f"{vp_name:7} {name:18} {route:38} {flag} (+{info['overflow']}px)")
            except Exception as e:
                results["routes"][f"{vp_name}__{name}"] = {"route": route, "error": str(e)}
                print(f"{vp_name:7} {name:18} {route:38} ERROR {e}")
            finally:
                ctx.close()
    browser.close()

with open(f"{OUT}/probe.json", "w") as fh:
    json.dump(results, fh, indent=2)
print("saved", OUT + "/probe.json")

"""U2 — count visible native <select> per route at 720px (expected 0)."""
import json
import sys
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:12000"
ROUTES = ["/opportunities", "/companies", "/changes", "/analytics", "/reports", "/signin", "/signup"]

JS = """() => {
  const vis = [];
  document.querySelectorAll('select').forEach(s => {
    const r = s.getBoundingClientRect();
    const cs = getComputedStyle(s);
    const shown = r.width > 1 && r.height > 1 && cs.display !== 'none' && cs.visibility !== 'hidden'
                  && !s.classList.contains('bs-select-native') && cs.position !== 'absolute';
    if (shown) vis.push({id: s.id, name: s.name, w: Math.round(r.width), h: Math.round(r.height)});
  });
  return vis;
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 720, "height": 1600}, device_scale_factor=1)
    total = 0
    for route in ROUTES:
        pg.goto(BASE + route, wait_until="networkidle")
        vis = pg.evaluate(JS)
        total += len(vis)
        print(f"{route:16} visible <select>: {len(vis)} {vis if vis else ''}")
    print("TOTAL visible selects:", total)
    b.close()
sys.exit(0 if total == 0 else 1)

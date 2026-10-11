"""U6 — tap targets under 44px on public routes at 390px."""
import sys
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:12000"
ROUTES = ["/", "/opportunities", "/companies", "/markets/dfw", "/changes", "/trends",
          "/analytics", "/reports", "/how-it-works", "/guides", "/signin", "/signup"]

JS = """() => {
  const out = [];
  document.querySelectorAll('a[href], button, input, select, textarea, [role=button]').forEach(el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || cs.display === 'none' || cs.visibility === 'hidden') return;
    if (r.height >= 44) return;
    // Inline links inside a sentence are exempt (WCAG 2.5.5 inline exception).
    let inline = false, n = el.parentElement;
    while (n && n !== document.body) {
      if (n.tagName === 'P' || n.tagName === 'SPAN' || n.tagName === 'LI' || n.tagName === 'TD') {
        // crude inline detection: element is a link whose parent has text siblings
        if (el.tagName === 'A' && n.textContent.trim().length > (el.textContent||'').trim().length + 5) inline = true;
      }
      n = n.parentElement;
    }
    if (inline) return;
    out.push({tag: el.tagName.toLowerCase(), cls: (el.className||'').toString().slice(0,40),
              h: Math.round(r.height), w: Math.round(r.width),
              text: (el.textContent||el.getAttribute('aria-label')||'').trim().slice(0,30)});
  });
  return out;
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": int(sys.argv[2]) if len(sys.argv) > 2 else 390, "height": 844}, has_touch=True)
    total = 0
    for route in ROUTES:
        pg.goto(BASE + route, wait_until="networkidle")
        bad = pg.evaluate(JS)
        total += len(bad)
        if bad:
            print(f"{route}: {len(bad)}")
            for x in bad[:8]:
                print(f"    h={x['h']:>3} {x['tag']}.{x['cls']} {x['text']!r}")
    print("TOTAL sub-44px controls:", total)
    b.close()
sys.exit(0 if total == 0 else 1)

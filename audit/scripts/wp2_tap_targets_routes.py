from playwright.sync_api import sync_playwright
ROUTES = ["/", "/opportunities", "/companies", "/markets/dfw", "/trends", "/changes",
          "/how-it-works", "/signin", "/signup", "/analytics", "/reports", "/guides"]
BASE = "http://127.0.0.1:12000"
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    page = ctx.new_page()
    for route in ROUTES:
        page.goto(BASE + route, wait_until="networkidle")
        res = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('button, input, select, textarea, a.btn, [role=button]').forEach(el => {
            const r = el.getBoundingClientRect();
            const cs = getComputedStyle(el);
            if (r.width===0 || r.height===0 || cs.display==='none' || cs.visibility==='hidden') return;
            if (r.height < 44 || r.width < 24) {
              out.push({tag:el.tagName.toLowerCase(), cls:(el.className||'').toString().slice(0,44),
                        type:el.getAttribute('type')||'', w:Math.round(r.width), h:Math.round(r.height),
                        label:(el.textContent||el.getAttribute('aria-label')||'').trim().slice(0,30)});
            }
          });
          return out;
        }""")
        print(f"=== {route}  controls under 44px: {len(res)}")
        for o in res[:12]:
            print(f"    <{o['tag']} type={o['type']} class='{o['cls']}'> {o['w']}x{o['h']}  '{o['label']}'")
    b.close()

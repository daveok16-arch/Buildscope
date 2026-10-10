from playwright.sync_api import sync_playwright

ROUTES = ["/", "/opportunities", "/companies", "/markets/dfw", "/trends", "/changes",
          "/how-it-works", "/analytics", "/reports"]
BASE = "http://127.0.0.1:12000"

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    for route in ROUTES:
        page.goto(BASE + route, wait_until="networkidle")
        res = page.evaluate("""() => {
          const vw = window.innerWidth;
          const drawer = document.getElementById('mobile-nav-drawer');
          const out = [];
          document.querySelectorAll('*').forEach(el => {
            if (drawer && drawer.contains(el)) return;      // off-canvas drawer
            if (el.classList && el.classList.contains('mobile-drawer-backdrop')) return;
            const cs = getComputedStyle(el);
            if (cs.position === 'fixed') return;
            const r = el.getBoundingClientRect();
            if (r.width === 0) return;
            if (r.right > vw + 1) {
              out.push({tag: el.tagName.toLowerCase(),
                        cls: (el.className||'').toString().slice(0,50),
                        id: el.id||'', right: Math.round(r.right), w: Math.round(r.width)});
            }
          });
          return {vw, sw: document.documentElement.scrollWidth, n: out.length, top: out.slice(0,8)};
        }""")
        print(f"=== {route} vw={res['vw']} scrollWidth={res['sw']} overflowing(non-fixed)={res['n']}")
        for o in res["top"]:
            print(f"    <{o['tag']} class='{o['cls']}' id='{o['id']}'> right={o['right']} w={o['w']}")
    b.close()

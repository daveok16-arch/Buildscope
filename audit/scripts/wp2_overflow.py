from playwright.sync_api import sync_playwright

ROUTES = ["/", "/opportunities", "/companies", "/markets/dfw", "/trends", "/changes",
          "/how-it-works", "/signin", "/signup", "/analytics", "/reports"]
BASE = "http://127.0.0.1:12000"

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    for route in ROUTES:
        page.goto(BASE + route, wait_until="networkidle")
        res = page.evaluate("""() => {
          const vw = window.innerWidth;
          const out = [];
          document.querySelectorAll('*').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width === 0) return;
            if (r.right > vw + 1 || r.left < -1) {
              out.push({
                tag: el.tagName.toLowerCase(),
                cls: (el.className||'').toString().slice(0,60),
                id: el.id||'',
                left: Math.round(r.left), right: Math.round(r.right),
                w: Math.round(r.width)
              });
            }
          });
          // keep the deepest offenders (leaf-most) to avoid parent chains
          return {vw, sw: document.documentElement.scrollWidth, offenders: out.slice(0, 25)};
        }""")
        print(f"=== {route}  vw={res['vw']} scrollWidth={res['sw']}")
        for o in res["offenders"]:
            print(f"    <{o['tag']} class='{o['cls']}' id='{o['id']}'> left={o['left']} right={o['right']} w={o['w']}")
    b.close()

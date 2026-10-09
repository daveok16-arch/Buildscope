import json
from playwright.sync_api import sync_playwright

ROUTES = ["/", "/opportunities", "/companies", "/markets/dfw", "/trends", "/changes", "/how-it-works", "/signin", "/signup"]
BASE = "http://127.0.0.1:12000"

with sync_playwright() as p:
    b = p.chromium.launch()
    out = {}
    for vp, size in [("narrow", (320, 640)), ("mobile", (390, 844))]:
        ctx = b.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1)
        page = ctx.new_page()
        for route in ROUTES:
            page.goto(BASE + route, wait_until="networkidle")
            data = page.evaluate("""() => {
              const iw = window.innerWidth;
              const sw = document.documentElement.scrollWidth;
              const offenders = [];
              document.querySelectorAll('*').forEach(el=>{
                const r = el.getBoundingClientRect();
                if (r.right > iw + 1 && r.width > 0) offenders.push({tag:el.tagName, cls:(el.className||'').toString().slice(0,60), right:Math.round(r.right)});
              });
              const small = [...document.querySelectorAll('a,button,input,select')].filter(el=>{const r=el.getBoundingClientRect(); return r.width>0 && (r.height<44||r.width<44);});
              return {iw, sw, overflow: sw>iw, offenders: offenders.slice(0,8), smallCount: small.length};
            }""")
            out[f"{vp}{route}"] = data
        ctx.close()
    b.close()
print(json.dumps(out, indent=1))

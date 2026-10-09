import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    for route in ["/companies", "/opportunities", "/changes"]:
        page.goto(BASE + route, wait_until="networkidle")
        small = page.evaluate("""() => {
          const out = {};
          [...document.querySelectorAll('a,button,input,select')].forEach(el=>{
            const r=el.getBoundingClientRect();
            if (r.width>0 && (r.height<44||r.width<44)) {
              const key = el.tagName + '.' + (el.className||'').toString().split(' ').slice(0,2).join('.');
              out[key] = (out[key]||0)+1;
            }
          });
          return out;
        }""")
        print(route, json.dumps(small, indent=1))
    b.close()

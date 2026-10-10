import json
from playwright.sync_api import sync_playwright

ROUTES = ["/", "/opportunities", "/companies", "/markets/dfw", "/trends", "/changes", "/how-it-works", "/signin", "/signup"]
BASE = "http://127.0.0.1:12000"
AXE = open("audit/a11y/axe.min.js").read()

seen = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    for route in ROUTES:
        page.goto(BASE + route, wait_until="networkidle")
        page.add_script_tag(content=AXE)
        res = page.evaluate("""async () => await axe.run(document, {runOnly:{type:'rule',values:['color-contrast']}})""")
        for viol in res["violations"]:
            for n in viol["nodes"]:
                msg = n["any"][0]["message"] if n["any"] else ""
                key = msg.split("font size")[0].strip()
                seen.setdefault(key, []).append((route, n["target"][0]))
    b.close()

for k, v in seen.items():
    print(k)
    for r, t in v[:3]:
        print("    ", r, t)
    print("     total nodes:", len(v))

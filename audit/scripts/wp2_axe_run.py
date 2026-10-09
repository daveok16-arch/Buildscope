import json
from playwright.sync_api import sync_playwright

ROUTES = ["/", "/opportunities", "/companies", "/markets/dfw", "/trends", "/changes", "/how-it-works", "/signin", "/signup"]
BASE = "http://127.0.0.1:12000"
AXE = open("audit/a11y/axe.min.js").read()

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    summary = {}
    for route in ROUTES:
        page.goto(BASE + route, wait_until="networkidle")
        page.add_script_tag(content=AXE)
        res = page.evaluate("""async () => {
          return await axe.run(document, {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}});
        }""")
        v = []
        for viol in res["violations"]:
            v.append({"id": viol["id"], "impact": viol["impact"], "n": len(viol["nodes"]),
                      "targets": [n["target"][:1] for n in viol["nodes"][:2]]})
        summary[route] = v
    print(json.dumps(summary, indent=1))
    b.close()

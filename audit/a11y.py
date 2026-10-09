"""Read-only a11y + mobile measurement via Playwright + axe-core."""
import json, os, sys, urllib.request
from playwright.sync_api import sync_playwright

BASE = os.environ.get("AUDIT_BASE", "https://buildscope-xppz.onrender.com")
OUT = os.environ.get("AUDIT_OUT", "audit/a11y")
os.makedirs(OUT, exist_ok=True)

# fetch axe-core once
axe_path = os.path.join(OUT, "axe.min.js")
if not os.path.exists(axe_path):
    with urllib.request.urlopen("https://unpkg.com/axe-core@4.10.2/axe.min.js", timeout=60) as r:
        open(axe_path, "wb").write(r.read())
AXE = open(axe_path).read()

ROUTES = ["/", "/opportunities", "/markets/dfw", "/companies", "/changes", "/trends",
          "/analytics", "/reports", "/signin", "/signup"]

report = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    # axe at desktop
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    for route in ROUTES:
        try:
            page.goto(BASE + route, wait_until="load", timeout=45000)
            page.wait_for_timeout(400)
            page.add_script_tag(content=AXE)
            res = page.evaluate("async () => await axe.run(document, {resultTypes:['violations']})")
            v = res.get("violations", [])
            report.setdefault("axe", {})[route] = [
                {"id": x["id"], "impact": x.get("impact"), "n": len(x["nodes"]),
                 "help": x["help"], "targets": [n["target"] for n in x["nodes"][:3]]}
                for x in v
            ]
        except Exception as e:
            report.setdefault("axe", {})[route] = [{"error": str(e)[:200]}]
    ctx.close()

    # mobile measurements
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    for route in ["/opportunities", "/companies"]:
        page.goto(BASE + route, wait_until="load", timeout=45000)
        page.wait_for_timeout(400)
        m = page.evaluate("""() => {
          const small = [];
          document.querySelectorAll('a,button,input,select,[role=button]').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width>0 && r.height>0 && (r.height < 44 || r.width < 44)) {
              small.push({tag:el.tagName.toLowerCase(), cls:(el.className||'').toString().slice(0,40),
                w:Math.round(r.width), h:Math.round(r.height), text:(el.textContent||'').trim().slice(0,25)});
            }
          });
          const elOver = [];
          document.querySelectorAll('*').forEach(el => {
            if (el.scrollWidth - el.clientWidth > 2 && el.clientWidth>0) {
              elOver.push({tag:el.tagName.toLowerCase(), cls:(el.className||'').toString().slice(0,40),
                sw:el.scrollWidth, cw:el.clientWidth, text:(el.textContent||'').trim().slice(0,40)});
            }
          });
          return {smallTargets: small.slice(0,40), smallCount: small.length,
                  textOverflow: elOver.slice(0,40), overflowCount: elOver.length};
        }""")
        report.setdefault("mobile", {})[route] = m
    ctx.close()
    b.close()

with open(os.path.join(OUT, "a11y.json"), "w") as f:
    json.dump(report, f, indent=1)

# summary
for route, v in report.get("axe", {}).items():
    crit = [x for x in v if x.get("impact") in ("critical", "serious")]
    print("AXE", route, "violations:", len(v), "crit/serious:", len(crit),
          [x["id"] for x in crit])
for route, m in report.get("mobile", {}).items():
    print("MOBILE", route, "small targets:", m["smallCount"], "text overflow el:", m["overflowCount"])
print("WROTE", OUT)

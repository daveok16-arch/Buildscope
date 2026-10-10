"""Read-only accessibility audit with axe-core (no app changes). Writes audit/axe_results.json."""
import json
import os
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = os.environ.get("AUDIT_BASE", "http://127.0.0.1:12000")
AXE = open("/tmp/axe_extract/package/axe.min.js").read()
ROUTES = ["/", "/opportunities", "/opportunities/new", "/markets", "/companies",
          "/changes", "/trends", "/analytics", "/reports", "/how-it-works", "/trades",
          "/trades/commercial-hvac", "/guides", "/signin", "/signup",
          "/this-does-not-exist"]
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]


def main():
    results = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        # Strip Content-Security-Policy ONLY in this audit browser so axe-core can be injected
        # for measurement. The app's real CSP is left intact; this is not an app change.
        ctx.route("**/*", lambda route: route.continue_())
        page = ctx.new_page()

        def _strip_csp(route):
            try:
                resp = route.fetch()
            except Exception:
                route.continue_(); return
            headers = dict(resp.headers)
            headers.pop("content-security-policy", None)
            headers.pop("content-security-policy-report-only", None)
            route.fulfill(response=resp, headers=headers)

        page.route("**/*", _strip_csp)
        for r in ROUTES:
            try:
                page.goto(BASE + r, wait_until="networkidle", timeout=30000)
                page.add_script_tag(content=AXE)
                res = page.evaluate(
                    "async (tags) => await axe.run(document, {runOnly: {type:'tag', values: tags}})",
                    TAGS,
                )
                viol = []
                for v in res["violations"]:
                    viol.append({
                        "id": v["id"], "impact": v["impact"], "help": v["help"],
                        "nodes": len(v["nodes"]),
                        "targets": [n["target"] for n in v["nodes"][:3]],
                    })
                results[r] = {"violations": viol,
                              "count": sum(v["nodes"] for v in viol)}
            except Exception as e:
                results[r] = {"error": str(e)[:120]}
        ctx.close()
        b.close()
    with open("audit/axe_results.json", "w") as f:
        json.dump(results, f, indent=1)
    for r, d in results.items():
        if "error" in d:
            print("ERR", r, d["error"]); continue
        print(f"{r}: {d['count']} violations")
        for v in d["violations"]:
            print(f"   [{v['impact']}] {v['id']} x{v['nodes']} {v['help'][:60]}")


if __name__ == "__main__":
    main()

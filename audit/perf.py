"""Read-only performance measurement (mobile emulation)."""
import json, os
from playwright.sync_api import sync_playwright

BASE = os.environ.get("AUDIT_BASE", "https://buildscope-xppz.onrender.com")
OUT = os.environ.get("AUDIT_OUT", "audit/perf")
os.makedirs(OUT, exist_ok=True)
ROUTES = ["/", "/opportunities", "/trends", "/companies"]
out = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                        is_mobile=True, has_touch=True)
    for route in ROUTES:
        pg = ctx.new_page()
        reqs = []
        pg.on("response", lambda r: reqs.append((r.request.resource_type, r.url, r.headers.get("content-length", "0"))))
        pg.goto(BASE + route, wait_until="load", timeout=60000)
        pg.wait_for_timeout(1500)
        perf = pg.evaluate("""() => {
          const nav = performance.getEntriesByType('navigation')[0] || {};
          const paint = performance.getEntriesByType('paint');
          const res = performance.getEntriesByType('resource');
          const lcpEntry = window.__lcp || null;
          return {
            ttfb: Math.round(nav.responseStart||0),
            domContentLoaded: Math.round(nav.domContentLoadedEventEnd||0),
            loadEvent: Math.round(nav.loadEventEnd||0),
            transferSize: Math.round(nav.transferSize||0),
            fcp: Math.round((paint.find(p=>p.name==='first-contentful-paint')||{}).startTime||0),
            resources: res.length,
            resTransfer: res.reduce((a,r)=>a+(r.transferSize||0),0),
            cssBytes: res.filter(r=>r.name.endsWith('.css')).reduce((a,r)=>a+(r.transferSize||0),0),
            jsBytes: res.filter(r=>r.name.endsWith('.js')).reduce((a,r)=>a+(r.transferSize||0),0),
            lcp: lcpEntry ? Math.round(lcpEntry) : null,
          };
        }""")
        # LCP observer (installed before load would be ideal; approximate post-hoc)
        types = {}
        for t,u,cl in reqs:
            types[t] = types.get(t, 0) + 1
        perf["request_types"] = types
        perf["total_requests"] = len(reqs)
        perf["route"] = route
        out[route] = perf
        print(route, "TTFB", perf["ttfb"], "FCP", perf["fcp"], "load", perf["loadEvent"],
              "reqs", perf["total_requests"], "resTransfer", perf["resTransfer"],
              "css", perf["cssBytes"], "js", perf["jsBytes"], perf["request_types"])
        pg.close()
    ctx.close(); b.close()
json.dump(out, open(os.path.join(OUT, "perf.json"), "w"), indent=1)
print("WROTE", OUT)

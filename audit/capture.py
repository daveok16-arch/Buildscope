"""Read-only UI audit capture. Writes screenshots + per-route metrics to audit/ui/."""
import json, os, re, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("AUDIT_BASE", "http://127.0.0.1:5000")
OUT = os.environ.get("AUDIT_OUT", "audit/ui")
os.makedirs(OUT, exist_ok=True)

ROUTES = [
    "/", "/opportunities", "/markets", "/markets/dfw", "/companies",
    "/changes", "/trends", "/analytics", "/reports", "/how-it-works",
    "/trades", "/trades/commercial-hvac", "/commercial-construction-leads",
    "/guides", "/signin", "/signup", "/nonexistent-404-page",
]

VIEWPORTS = {"mobile": (390, 844), "tablet": (820, 1180), "desktop": (1440, 900)}

def slug_for(path):
    return re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_") or "root"

results = {}
with sync_playwright() as p:
    browser = p.chromium.launch()
    for vp, (w, h) in VIEWPORTS.items():
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        page = ctx.new_page()
        for route in ROUTES:
            url = BASE + route
            entry = {"route": route, "viewport": vp, "status": None, "error": None}
            try:
                resp = page.goto(url, wait_until="load", timeout=30000)
                entry["status"] = resp.status if resp else None
                page.wait_for_timeout(400)
                # overflow + offending elements
                metrics = page.evaluate("""() => {
                    const de = document.documentElement;
                    const over = de.scrollWidth - window.innerWidth;
                    const offenders = [];
                    if (over > 1) {
                      const vw = window.innerWidth;
                      document.querySelectorAll('*').forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.right > vw + 1 || r.left < -1) {
                          if (r.width > 0 && r.height > 0) {
                            offenders.push({tag: el.tagName.toLowerCase(),
                              cls: (el.className||'').toString().slice(0,80),
                              right: Math.round(r.right), left: Math.round(r.left),
                              w: Math.round(r.width)});
                          }
                        }
                      });
                    }
                    return {scrollWidth: de.scrollWidth, innerWidth: window.innerWidth,
                            overflow: over, offenders: offenders.slice(0, 25),
                            h1: [...document.querySelectorAll('h1')].map(e=>e.textContent.trim()).slice(0,3),
                            title: document.title};
                }""")
                entry.update(metrics)
                entry["offender_count"] = len(entry.get("offenders", []))
                # truncated/clipped text detection
                clipped = page.evaluate("""() => {
                    const out = [];
                    document.querySelectorAll('h1,h2,h3,p,span,div,dd,dt,li,a,td,th').forEach(el => {
                      const s = getComputedStyle(el);
                      const clipH = el.scrollHeight - el.clientHeight > 2 && (s.overflowHidden || s.overflow==='hidden' || s.textOverflow==='ellipsis');
                      const clipW = el.scrollWidth - el.clientWidth > 2 && (s.overflowHidden || s.overflow==='hidden' || s.textOverflow==='ellipsis');
                      if (clipH || clipW) {
                        out.push({tag: el.tagName.toLowerCase(), cls:(el.className||'').toString().slice(0,60),
                          text:(el.textContent||'').trim().slice(0,50), clipH, clipW});
                      }
                    });
                    return out.slice(0,30);
                }""")
                entry["clipped"] = clipped
                entry["clipped_count"] = len(clipped)
                entry["img_count"] = page.evaluate("document.images.length")
                entry["img_no_alt"] = page.evaluate("[...document.images].filter(i=>!i.alt).length")
                fn = f"{vp}__{slug_for(route)}.png"
                page.screenshot(path=os.path.join(OUT, fn), full_page=True)
                entry["screenshot"] = fn
            except Exception as e:
                entry["error"] = str(e)[:300]
            results[f"{vp}:{route}"] = entry
            print(vp, route, entry.get("status"), "overflow=", entry.get("overflow"), "offenders=", entry.get("offender_count"), "clipped=", entry.get("clipped_count"), entry.get("error") or "")
        ctx.close()
    browser.close()

with open(os.path.join(OUT, "metrics.json"), "w") as f:
    json.dump(results, f, indent=1)
print("WROTE", os.path.join(OUT, "metrics.json"))

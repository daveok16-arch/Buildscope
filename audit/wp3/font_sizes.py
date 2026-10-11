"""U0c — list every computed font size under 12px on every public route.

Run against a live local server. Output must be empty for the gate to pass.
Usage: PYTHONPATH=vendor/python python3 audit/wp3/font_sizes.py [base_url] [width]
"""
import json
import sys
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:12000"
WIDTH = int(sys.argv[2]) if len(sys.argv) > 2 else 720

ROUTES = [
    "/", "/opportunities", "/opportunities/new", "/companies", "/markets", "/markets/dfw",
    "/changes", "/trends", "/analytics", "/reports", "/how-it-works", "/guides",
    "/trades", "/trades/commercial-hvac", "/commercial-construction-leads",
    "/signin", "/signup",
]

JS = """() => {
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    if (!el.textContent || !el.textContent.trim()) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return;
    const px = parseFloat(cs.fontSize);
    if (px < 12) {
      out.push({tag: el.tagName.toLowerCase(), cls: (el.className||'').toString().slice(0,40),
                px: px, text: el.textContent.trim().slice(0,30)});
    }
  });
  return out;
}"""


def main():
    total = 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": WIDTH, "height": 1600}, device_scale_factor=1)
        for route in ROUTES:
            pg.goto(BASE + route, wait_until="networkidle")
            bad = pg.evaluate(JS)
            if bad:
                total += len(bad)
                print(f"[{route}] {len(bad)} element(s) <12px:")
                seen = set()
                for x in bad:
                    key = (x["tag"], x["cls"], x["px"])
                    if key in seen:
                        continue
                    seen.add(key)
                    print(f"    {x['px']:>5}px  {x['tag']}.{x['cls']}  {x['text']!r}")
        b.close()
    print(f"\nTOTAL elements under 12px: {total}")
    return total


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)

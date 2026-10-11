"""Reproduce U1: city picker list rendered in flow below the footer at 720px."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "/workspace/project/Buildscope/audit/wp3"


def run(width, height):
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        pg.goto(f"{BASE}/opportunities", wait_until="networkidle")
        doc = pg.evaluate("""() => ({
            scrollW: document.documentElement.scrollWidth,
            innerW: window.innerWidth,
            innerH: window.innerHeight
        })""")
        print(f"--- {width}x{height} ---")
        print("doc", doc)
        # Find every element whose text contains 'All cities'
        info = pg.evaluate("""() => {
            const out = [];
            document.querySelectorAll('*').forEach(el => {
                if (el.children.length === 0 && /All cities/.test(el.textContent||'')) {
                    const r = el.getBoundingClientRect();
                    out.push({tag: el.tagName, cls: el.className, text: el.textContent.trim().slice(0,40),
                              top: Math.round(r.top), left: Math.round(r.left), w: Math.round(r.width), h: Math.round(r.height),
                              visible: r.width>0 && r.height>0});
                }
            });
            // footer position
            const f = document.querySelector('footer, .site-footer, .footer');
            const fr = f ? f.getBoundingClientRect() : null;
            return {matches: out, footerTop: fr ? Math.round(fr.top) : null,
                    docHeight: document.body.scrollHeight};
        }""")
        import json
        print(json.dumps(info, indent=1)[:2000])
        # find the popup element for city
        pop = pg.evaluate("""() => {
            const w = document.querySelector('#city')?.closest('.bs-combobox');
            if (!w) return 'no wrap';
            const p = w.querySelector('.bs-combobox-pop');
            if (!p) return 'no pop';
            const r = p.getBoundingClientRect();
            const cs = getComputedStyle(p);
            return {hidden: p.hidden, display: cs.display, position: cs.position,
                    top: Math.round(r.top), h: Math.round(r.height), width: Math.round(r.width),
                    inViewport: r.top < window.innerHeight && r.top >= 0,
                    zIndex: cs.zIndex, parent: p.parentElement.tagName + '.' + p.parentElement.className};
        }""")
        print("city pop closed:", pop)
        pg.screenshot(path=f"{OUT}/u1_closed_{width}.png", full_page=True)
        b.close()


for w, h in [(390, 844), (720, 1600), (1440, 900)]:
    run(w, h)

"""Probe U1 open-state and no-JS state at 720 and 1440."""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "/workspace/project/Buildscope/audit/wp3"


def probe(pg, label):
    pg.goto(f"{BASE}/opportunities", wait_until="networkidle")
    pg.evaluate("""() => {
        const w = document.querySelector('#city').closest('.bs-combobox');
        w.querySelector('.bs-combobox-trigger').click();
    }""")
    pg.wait_for_timeout(300)
    info = pg.evaluate("""() => {
        const w = document.querySelector('#city').closest('.bs-combobox');
        const p = w.querySelector('.bs-combobox-pop') || document.querySelector('body > .bs-combobox-pop');
        if (!p) return {err: 'no pop found'};
        const r = p.getBoundingClientRect();
        const cs = getComputedStyle(p);
        const f = document.querySelector('footer');
        return {hidden: p.hidden, display: cs.display, position: cs.position,
                top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height), width: Math.round(r.width),
                inViewport: r.top >= 0 && r.bottom <= window.innerHeight,
                parent: p.parentElement.tagName,
                footerTop: f ? Math.round(f.getBoundingClientRect().top) : null,
                docH: document.body.scrollHeight, innerH: window.innerHeight};
    }""")
    print(f"[{label}] open state:", json.dumps(info))
    pg.screenshot(path=f"{OUT}/u1_open_{label}.png", full_page=False)


with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h, lab in [(390, 844, "390"), (720, 1600, "720"), (1440, 900, "1440")]:
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        probe(pg, lab)
        pg.close()
    # no-JS
    ctx = b.new_context(viewport={"width": 720, "height": 1600}, java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto(f"{BASE}/opportunities", wait_until="load")
    info = pg.evaluate("""() => {
        const sels = [...document.querySelectorAll('select')].map(s => ({id:s.id, visible: s.getBoundingClientRect().width>0}));
        return {selects: sels};
    }""")
    print("[720 no-JS]", json.dumps(info))
    pg.screenshot(path=f"{OUT}/u1_nojs_720.png", full_page=True)
    b.close()

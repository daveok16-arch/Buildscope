from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:12000"
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    res = page.evaluate("""() => {
      const sel = ['.wrap','.site-header','.header-inner','.brand','.brand-text','.brand-text strong','.brand-text small','.mobile-menu-btn','.site-nav'];
      const out = {};
      sel.forEach(s => {
        const el = document.querySelector(s);
        if (!el) { out[s] = null; return; }
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        out[s] = {left:Math.round(r.left), right:Math.round(r.right), w:Math.round(r.width), display:cs.display, fs:cs.fontSize, shrink:cs.flexShrink};
      });
      out.__sw = document.documentElement.scrollWidth;
      out.__vw = window.innerWidth;
      return out;
    }""")
    for k, v in res.items():
        print(k, v)
    b.close()

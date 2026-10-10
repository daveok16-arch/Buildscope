"""M1 verification: custom controls replace native dropdowns; keyboard + mobile sheet work;
no CSP/console errors. Read-only against the local server. Run:
    PYTHONPATH=vendor/python python audit/m1_probe.py
"""
import sys
sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = []


def log(*a):
    line = " ".join(str(x) for x in a)
    OUT.append(line)
    print(line)


OPEN_CITY = (
    "() => { const s = document.querySelector(\"select[name='city']\"); "
    "const b = s.closest('.bs-combobox').querySelector('.bs-combobox-trigger'); b.click(); }"
)

with sync_playwright() as p:
    b = p.chromium.launch()

    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    pg.goto(BASE + "/opportunities", wait_until="networkidle")

    log(f"comboboxes built={pg.locator('.bs-combobox').count()}"
        f"  native selects hidden={pg.locator('select.bs-select-native').count()}")
    pg.evaluate(OPEN_CITY)
    pg.wait_for_timeout(250)
    log(f"city popup open={pg.locator('.bs-combobox-pop:not([hidden])').count()}"
        f"  options={pg.locator('.bs-combobox-pop:not([hidden]) .bs-option').count()}"
        f"  searchable={pg.locator('.bs-combobox-pop:not([hidden]) .bs-combobox-search').count()}")
    pg.keyboard.type("plan")
    pg.wait_for_timeout(150)
    filtered = pg.locator(".bs-combobox-pop:not([hidden]) .bs-option").count()
    pg.keyboard.press("ArrowDown")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(200)
    chosen = pg.evaluate("document.querySelector(\"select[name='city']\").value")
    log(f"keyboard type 'plan' -> options={filtered}  native select value={chosen!r}")
    pg.screenshot(path="audit/live/M1_city_picker_desktop.png", full_page=False)

    m = b.new_context(viewport={"width": 390, "height": 844})
    mp = m.new_page()
    merr = []
    mp.on("console", lambda x: merr.append(x.text) if x.type == "error" else None)
    mp.on("pageerror", lambda e: merr.append("pageerror: " + str(e)))
    mp.goto(BASE + "/opportunities", wait_until="networkidle")
    mp.evaluate(OPEN_CITY)
    mp.wait_for_timeout(300)
    pos = mp.evaluate("""() => {
      const p = document.querySelector('.bs-combobox-pop:not([hidden])');
      if (!p) return null;
      const r = p.getBoundingClientRect(); const cs = getComputedStyle(p);
      return {position: cs.position, bottom: Math.round(window.innerHeight - r.bottom),
              topRadius: cs.borderTopLeftRadius, width: Math.round(r.width)};
    }""")
    log(f"mobile sheet geometry: {pos}")
    mp.screenshot(path="audit/live/M1_city_picker_mobile.png", full_page=False)

    log("console errors desktop:", errors or "none")
    log("console errors mobile:", merr or "none")
    b.close()

open("audit/live/M1_probe.txt", "w").write("\n".join(OUT) + "\n")

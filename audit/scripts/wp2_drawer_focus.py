from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:12000"
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.click("#mobile-menu-open-btn")
    page.wait_for_timeout(600)
    print("after open:", page.evaluate("""() => {
      const d=document.getElementById('mobile-nav-drawer');
      const c=document.getElementById('mobile-menu-close-btn');
      return {vis:getComputedStyle(d).visibility, active:document.activeElement.id||document.activeElement.tagName,
              closeVisible:getComputedStyle(c).visibility};
    }"""))
    # manual focus attempt
    page.evaluate("() => document.getElementById('mobile-menu-close-btn').focus()")
    print("after manual focus:", page.evaluate("() => document.activeElement.id||document.activeElement.tagName"))
    b.close()

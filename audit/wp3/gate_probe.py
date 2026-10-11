import sys, json
sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

ROUTES = ["/", "/opportunities", "/companies", "/markets", "/markets/dfw", "/changes", "/trends",
          "/analytics", "/reports", "/how-it-works", "/guides", "/trades", "/trades/commercial-hvac",
          "/commercial-construction-leads", "/signin", "/signup",
          "/opportunities/1701-s-university-dr-fort-worth-9c5275"]
BASE = "http://127.0.0.1:12000"
VPS = [("390", 390, 844), ("720", 720, 1280), ("1440", 1440, 900)]

with sync_playwright() as p:
    b = p.chromium.launch()
    for name, w, h in VPS:
        ctx = b.new_context(viewport={"width": w, "height": h}, has_touch=(w < 900))
        page = ctx.new_page()
        print(f"\n##### VIEWPORT {name} ({w}x{h}) #####")
        for route in ROUTES:
            try:
                page.goto(BASE + route, wait_until="networkidle", timeout=20000)
            except Exception as e:
                print(f"  {route}: NAV FAIL {e}")
                continue
            ov = page.evaluate("""()=>{const vw=window.innerWidth;const out=[];
              document.querySelectorAll('*').forEach(el=>{const r=el.getBoundingClientRect();
              if(r.width===0)return; if(r.right>vw+1||r.left<-1){out.push({tag:el.tagName.toLowerCase(),
              cls:(el.className||'').toString().slice(0,50),l:Math.round(r.left),rr:Math.round(r.right)});}});
              return {vw,sw:document.documentElement.scrollWidth,off:out.length,ex:out.slice(0,5)};}""")
            tap = page.evaluate("""()=>{const out=[];
              document.querySelectorAll('button,input,select,textarea,a.btn,[role=button]').forEach(el=>{
              const r=el.getBoundingClientRect();const cs=getComputedStyle(el);
              if(r.width===0||r.height===0||cs.display==='none'||cs.visibility==='hidden')return;
              if(r.height<44||r.width<24){out.push({tag:el.tagName.toLowerCase(),
              cls:(el.className||'').toString().slice(0,40),w:Math.round(r.width),h:Math.round(r.height)});}});
              return out;}""")
            flags = []
            if ov["sw"] > ov["vw"] + 1:
                flags.append(f"OVERFLOW sw={ov['sw']} vw={ov['vw']} off={ov['off']}")
            if tap:
                flags.append(f"TAP<44 x{len(tap)}")
            extra = json.dumps(ov["ex"])[:180] if ov["off"] else ""
            print(f"  {route:52s} {'; '.join(flags) if flags else 'ok'}  {extra}")
        ctx.close()
    b.close()

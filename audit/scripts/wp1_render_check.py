import os, sys, re
root, db = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "src"))
os.environ.setdefault("SECRET_KEY", "dev")
os.environ["OPPINTEL_DB"] = db
from oppintel.app.main import create_app
from oppintel.app.config import load_config
app = create_app(load_config())
c = app.test_client()
for path in sys.argv[3:]:
    r = c.get(path)
    html = r.get_data(as_text=True)
    print(f"\n===== {root} {path} [{r.status_code}] =====")
    for line in html.splitlines():
        if any(k in line for k in ["Showing ", "Differences Detected", "All time", "cumulative", "total held", "Newest", "Last observed", "date unverified", "Permit dates held", "to 2026", "to 2025"]):
            print(re.sub(r"\s+", " ", line).strip()[:220])

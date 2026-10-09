import os, sys, re
sys.path.insert(0, "src")
os.environ.setdefault("SECRET_KEY", "dev")
from oppintel.app.main import create_app
from oppintel.app.config import load_config
cfg = load_config()
app = create_app(cfg)
c = app.test_client()
for path in sys.argv[1:]:
    r = c.get(path)
    html = r.get_data(as_text=True)
    print(f"\n===== {path} [{r.status_code}] =====")
    for line in html.splitlines():
        if any(k in line for k in ["Showing ", "Differences Detected", "All time", "cumulative", "total held", "Newest permit", "Last observed", "date unverified"]):
            print(re.sub(r"\s+", " ", line).strip()[:220])

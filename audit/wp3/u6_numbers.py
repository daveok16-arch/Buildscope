"""U6 — one table, same minute, of the live numbers on home, /markets, /api/statistics,
/healthz and /trends, each with its definition. Read-only against the local server."""
import json
import re
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:12000"


def get(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def json_get(path: str) -> dict:
    return json.loads(get(path))


home = get("/")
markets = get("/markets")
trends = get("/trends")
stats = json_get("/api/statistics")
health = json_get("/healthz")

home_nums = dict(re.findall(r'data-count="([0-9]+)"[^>]*>\s*<[^>]*>\s*([^<]+)<', home))
home_labels = re.findall(r'metric-label">([^<]+)<', home)

mk = {}
for label, value in re.findall(r"<dt>([^<]+)</dt><dd>([^<]+)</dd>", markets):
    mk[label.strip()] = value.strip()

trend = dict(zip(re.findall(r'stat-label">([^<]+)<', trends), re.findall(r'stat-value">([^<]+)<', trends)))

meta = re.search(r'<meta name="description" content="([^"]*)"', home)
print("=== U6 number table (same minute) ===")
print(f"source            | figure                                  | value")
print(f"------------------+-----------------------------------------+-------")
print(f"home              | Public projects                         | {home_nums.get('133','?')} (label: {home_labels})")
print(f"home              | With mechanical evidence                | {home_nums.get('8','?')}")
print(f"home              | Permit records                          | {home_nums.get('236','?')}")
print(f"home              | Cities with projects                    | {home_nums.get('15','?')}")
print(f"markets           | {list(mk.items())}")
print(f"api/statistics    | projects_public={stats['statistics']['projects_public']} "
      f"projects_total={stats['statistics']['projects_total']} "
      f"with_mechanical={stats['statistics']['with_mechanical']} "
      f"permit_records={stats['statistics']['permit_records']} "
      f"permit_records_all={stats['statistics']['permit_records_all']} "
      f"cities={stats['statistics']['cities']} "
      f"active_jurisdictions={stats['statistics']['active_jurisdictions']}")
print(f"healthz           | cities_with_public_projects={health.get('cities_with_public_projects')} "
      f"markets={len(health.get('coverage',{}).get('markets',[]))}")
print(f"trends (30d)      | {trend}")
print()
print("home meta description:", meta.group(1) if meta else None)

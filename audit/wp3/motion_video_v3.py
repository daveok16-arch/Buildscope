"""V3 — record per-route motion videos (local server) and extract frames.

One context per (route,width) so the video maps to a route. Frames are extracted with the
vendored ffmpeg from imageio_ffmpeg. Supporting evidence only (motion is not live yet).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
BASE = "http://127.0.0.1:12000"
VID = Path("audit/wp3/video")
FR = Path("audit/wp3/frames")
VID.mkdir(parents=True, exist_ok=True)
FR.mkdir(parents=True, exist_ok=True)
ROUTES = {
    "home": "/",
    "opportunities": "/opportunities",
    "dossier": "/opportunities/3111-monticello-ave-a-dallas-031792",
    "trends": "/trends",
}


def record(reduced):
    tag = "reduced" if reduced else "normal"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for w, h in ((720, 1280), (390, 844)):
            for name, route in ROUTES.items():
                ctx = b.new_context(
                    viewport={"width": w, "height": h}, device_scale_factor=1,
                    record_video_dir=str(VID), record_video_size={"width": w, "height": h},
                    reduced_motion="reduce" if reduced else "no-preference",
                )
                p = ctx.new_page()
                p.goto(BASE + route, wait_until="networkidle")
                p.wait_for_timeout(300)
                for y in range(0, p.evaluate("document.body.scrollHeight"), 700):
                    p.evaluate(f"window.scrollTo(0,{y})")
                    p.wait_for_timeout(180)
                p.wait_for_timeout(500)
                video = p.video
                ctx.close()
                dest = VID / f"{name}_{w}_{tag}.webm"
                shutil.move(video.path(), dest)
        b.close()


def frames(reduced_tag="normal"):
    for webm in sorted(VID.glob(f"*_{reduced_tag}.webm")):
        out = FR / webm.stem
        out.mkdir(exist_ok=True)
        subprocess.run(
            [FFMPEG, "-y", "-i", str(webm), "-vf", "fps=4", "-frames:v", "4",
             str(out / "f%02d.png")],
            check=False, capture_output=True,
        )


record(False)
frames("normal")
print("videos:", sorted(x.name for x in VID.glob("*.webm")))
for d in sorted(FR.glob("*_normal")):
    print(d.name, sorted(x.name for x in d.glob("*.png")))


"""Guards against re-committing the Firebase web API key.

The key is public client configuration, but an *unrestricted* key in a public repository is a
finding in its own right (the repo's own `BUILD_SCOPE_REPOSITORY_AUDIT.md` rates it HIGH). These
tests fail if the file is tracked again, if a live-looking key reappears anywhere in the tree, or
if the environment-injection path regresses.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: A Google/Firebase web API key: "AIza" followed by 35 key characters.
_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def test_firebase_config_is_not_tracked():
    assert "firebase-applet-config.json" not in _tracked_files()


def test_firebase_config_is_git_ignored():
    assert "firebase-applet-config.json" in (ROOT / ".gitignore").read_text()


def test_no_committed_file_contains_a_live_looking_key():
    offenders: list[str] = []
    for rel in _tracked_files():
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, IsADirectoryError):
            continue
        if _KEY_RE.search(text):
            offenders.append(rel)
    assert offenders == [], f"live-looking API key committed in: {offenders}"


def test_example_config_carries_placeholders_not_a_key():
    text = (ROOT / "firebase-applet-config.example.json").read_text()
    parsed = json.loads(text)
    assert "apiKey" in parsed
    assert not _KEY_RE.search(text), "the example must not contain a real key"
    assert parsed["apiKey"].startswith("YOUR_")


# --- environment injection -----------------------------------------------------

def test_inline_json_environment_is_loaded(monkeypatch):
    from oppintel.app.main import _load_firebase_config

    payload = {"projectId": "p", "apiKey": "inline-key"}
    monkeypatch.setenv("FIREBASE_CONFIG_JSON", json.dumps(payload))
    assert _load_firebase_config() == payload


def test_inline_json_takes_precedence_over_a_file(monkeypatch, tmp_path):
    from oppintel.app.main import _load_firebase_config

    file_cfg = {"projectId": "from-file", "apiKey": "file-key"}
    cfg_path = tmp_path / "fb.json"
    cfg_path.write_text(json.dumps(file_cfg))
    monkeypatch.setenv("FIREBASE_CONFIG_PATH", str(cfg_path))
    monkeypatch.setenv("FIREBASE_CONFIG_JSON", json.dumps({"projectId": "from-env"}))
    assert _load_firebase_config()["projectId"] == "from-env"


def test_malformed_inline_json_falls_through_to_the_file(monkeypatch, tmp_path):
    from oppintel.app.main import _load_firebase_config

    file_cfg = {"projectId": "from-file", "apiKey": "file-key"}
    cfg_path = tmp_path / "fb.json"
    cfg_path.write_text(json.dumps(file_cfg))
    monkeypatch.setenv("FIREBASE_CONFIG_PATH", str(cfg_path))
    monkeypatch.setenv("FIREBASE_CONFIG_JSON", "{not json")
    assert _load_firebase_config() == file_cfg


def test_no_config_anywhere_returns_none(monkeypatch):
    from oppintel.app import main as main_module
    from oppintel.app.main import _load_firebase_config

    monkeypatch.delenv("FIREBASE_CONFIG_JSON", raising=False)
    monkeypatch.setenv("FIREBASE_CONFIG_PATH", "/nonexistent/fb.json")
    # Force every candidate path to be absent, so the "nothing found" branch is exercised
    # regardless of whether a developer has a (git-ignored) config file on disk.
    monkeypatch.setattr(main_module.os.path, "exists", lambda _p: False)
    assert _load_firebase_config() is None

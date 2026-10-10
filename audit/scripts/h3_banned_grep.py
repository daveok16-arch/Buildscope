"""H3 — complete copy-honesty grep over every public surface. READ-ONLY.

Greps case-insensitively for the phrase list the Director named, across templates, docs, guides
and the README, and prints every hit with file:line so each can be judged keep/changed. The
authoritative contract remains tests/test_copy_honesty.py.

    PYTHONPATH=vendor/python:src python audit/scripts/h3_banned_grep.py
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The Director's list, plus the machine-checked list from tests/test_copy_honesty.py.
PHRASES = [
    "continuous",
    "false alert",
    "real differences",
    "receive",
    "revision alert",
    "team",
    "months before",
    "before the work begins",
    "saved search",
    "guarantee",
    "watch",
]

SEARCH_ROOTS = [
    ROOT / "src" / "oppintel" / "app" / "templates",
    ROOT / "src" / "oppintel" / "app" / "static",
    ROOT / "docs",
    ROOT / "README.md",
]
SUFFIXES = {".html", ".md", ".txt", ".js", ".css", ".json", ".yaml", ".yml"}


def files():
    for base in SEARCH_ROOTS:
        if base.is_file():
            yield base
        elif base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.suffix in SUFFIXES:
                    yield path


def main() -> int:
    print("=== files scanned ===")
    all_files = list(files())
    for f in all_files:
        print(f"  {f.relative_to(ROOT)}")
    print(f"({len(all_files)} files)\n")

    for phrase in PHRASES:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        hits = []
        for path in all_files:
            for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.search(line):
                    hits.append((path.relative_to(ROOT), i, line.strip()[:100]))
        print(f"### '{phrase}'  -> {len(hits)} hit(s)")
        for rel, n, text in hits:
            print(f"    {rel}:{n}: {text}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

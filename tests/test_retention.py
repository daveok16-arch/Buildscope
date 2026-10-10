"""Raw-archive retention policy tests.

The policy is inert by default: with no `retention` block, or `enabled: false`, a plan never
deletes and `apply_plan` refuses. These tests pin that safety property and the two-rule
("keep_last AND keep_days must both agree") semantics.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from oppintel.config import load_retention, reset_config_cache
from oppintel.retention import RetentionSettings, apply_plan, parse_name, plan_prune


def _touch(directory: Path, name: str, *, age_days: float) -> Path:
    path = directory / name
    path.write_text("{}\n", encoding="utf-8")
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_name_parsing_handles_underscored_source_ids():
    assert parse_name(Path("fort_worth_permits_20261009T225530.jsonl")) == (
        "fort_worth_permits", "20261009T225530",
    )
    assert parse_name(Path("collin_cad_permits_20261009T225935.jsonl.gz")) == (
        "collin_cad_permits", "20261009T225935",
    )
    assert parse_name(Path("not-a-landing-file.txt")) is None


def test_disabled_policy_keeps_everything(tmp_path):
    for i in range(20):
        _touch(tmp_path, f"fort_worth_permits_2026010{i}T000000.jsonl", age_days=i)
    files = list(tmp_path.glob("*.jsonl"))
    plan = plan_prune(files, RetentionSettings(enabled=False))
    assert plan.delete == []
    assert len(plan.keep) == 20


def test_no_rules_keeps_everything_even_if_enabled(tmp_path):
    files = [_touch(tmp_path, f"collin_cad_permits_2026010{i}T000000.jsonl", age_days=i)
             for i in range(5)]
    plan = plan_prune(files, RetentionSettings(enabled=True))
    assert plan.delete == []
    assert len(plan.keep) == 5


def test_keep_last_is_per_source(tmp_path):
    files = []
    for src in ("fort_worth_permits", "collin_cad_permits"):
        for i in range(5):
            files.append(_touch(tmp_path, f"{src}_2026010{i}T000000.jsonl", age_days=i))
    settings = RetentionSettings(enabled=True, keep_last=2)
    plan = plan_prune(files, settings)
    # Newest 2 of each source are kept; the older 3 of each are candidates for deletion.
    assert len(plan.keep) == 4
    assert len(plan.delete) == 6


def test_delete_requires_both_rules_to_agree(tmp_path):
    # keep_last=2 keeps the newest two; keep_days=5 keeps anything newer than five days. A file
    # is deleted only when it is beyond keep_last AND older than keep_days. Ages avoid the exact
    # 5-day boundary so the assertion is not sensitive to test runtime.
    ages = [0, 1, 2, 3, 4, 7, 8, 9, 10, 11]
    files = [
        _touch(tmp_path, f"fort_worth_permits_202601{i:02d}T000000.jsonl", age_days=a)
        for i, a in enumerate(ages)
    ]
    settings = RetentionSettings(enabled=True, keep_last=2, keep_days=5)
    plan = plan_prune(files, settings)
    deleted_ages = sorted(
        int(round((time.time() - p.stat().st_mtime) / 86400)) for p in plan.delete
    )
    assert deleted_ages == [7, 8, 9, 10, 11]
    assert len(plan.keep) == 5


def test_keep_days_alone_keeps_a_recent_file_beyond_keep_last(tmp_path):
    # A file older than keep_last but younger than keep_days must survive.
    files = [_touch(tmp_path, f"fort_worth_permits_2026010{i}T000000.jsonl", age_days=i)
             for i in range(5)]
    settings = RetentionSettings(enabled=True, keep_last=1, keep_days=30)
    plan = plan_prune(files, settings)
    assert plan.delete == []
    assert len(plan.keep) == 5


def test_gzip_compresses_only_aged_kept_files(tmp_path):
    files = [_touch(tmp_path, f"fort_worth_permits_2026010{i}T000000.jsonl", age_days=i)
             for i in range(5)]
    settings = RetentionSettings(enabled=True, keep_last=10, gzip_after_days=2)
    plan = plan_prune(files, settings)
    compressed_ages = sorted(
        int((time.time() - p.stat().st_mtime) / 86400) for p in plan.compress
    )
    assert compressed_ages == [2, 3, 4]


def test_apply_refuses_when_disabled(tmp_path):
    files = [_touch(tmp_path, f"fort_worth_permits_2026010{i}T000000.jsonl", age_days=i)
             for i in range(5)]
    plan = plan_prune(files, RetentionSettings(enabled=True, keep_last=1, keep_days=1))
    assert plan.delete  # there is work to do
    with pytest.raises(RuntimeError):
        apply_plan(plan, settings=RetentionSettings(enabled=False))
    # Nothing was removed.
    assert len(list(tmp_path.glob("*.jsonl"))) == 5


def test_apply_deletes_and_gzips_when_enabled(tmp_path):
    ages = [0, 1, 2, 3, 4, 7, 8, 9, 10, 11]
    files = [
        _touch(tmp_path, f"fort_worth_permits_202601{i:02d}T000000.jsonl", age_days=a)
        for i, a in enumerate(ages)
    ]
    settings = RetentionSettings(enabled=True, keep_last=2, keep_days=5, gzip_after_days=2)
    plan = plan_prune(files, settings)
    counts = apply_plan(plan, settings=settings)
    assert counts["deleted"] == 5          # ages 7,8,9,10,11
    assert counts["compressed"] == 3       # ages 2,3,4 (age 5/6 absent; kept ones aged >=2)
    remaining = sorted(p.name for p in tmp_path.glob("*"))
    assert any(name.endswith(".gz") for name in remaining)
    assert not (tmp_path / "fort_worth_permits_20260109T000000.jsonl").exists()


def test_shipped_config_is_inert_by_default():
    """The repository ships retention disabled, so a deploy never deletes raw evidence."""
    reset_config_cache()
    settings = load_retention()
    assert settings.enabled is False
    assert settings.keep_last == 8
    assert settings.keep_days == 30

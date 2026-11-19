"""Smoke tests for the synthetic-data inspection CLI and protected assets."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
MOCK = ROOT / "data" / "synthetic" / "mock"
PROTECTED = ("clear.txt", "ambiguous.txt", "hostile.txt", "sneaky.txt")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "scripts.synthetic.inspect_synthetic", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_protected_demo_assets_intact():
    for name in PROTECTED:
        assert (MOCK / "emails" / name).exists()
    assert (MOCK / "followup" / "transfer_requests.csv").exists()
    assert (MOCK / "followup" / "replies.json").exists()
    # Original cyclo demo numbers still in active data/synthetic/mock/
    text = (MOCK / "inventory.csv").read_text(encoding="utf-8")
    assert "A,99000001,60,2026-11-15" in text
    assert "B,99000001,400,2026-11-15" in text


def test_inspect_summary_and_day():
    summary = _run("summary")
    assert summary.returncode == 0, summary.stderr
    assert "hospitals: 6" in summary.stdout
    assert "medications: 22" in summary.stdout

    day = _run("day", "5", "--din", "99000001")
    assert day.returncode == 0, day.stderr
    assert "2026-11-19" in day.stdout or "Snapshot" in day.stdout
    assert "on_hand=120" in day.stdout  # after partial transfer receipt


def test_inspect_risk_uses_existing_calculations():
    result = _run("risk", "--day", "1", "--din", "99000001")
    assert result.returncode == 0, result.stderr
    assert "at_risk" in result.stdout
    assert "A" in result.stdout

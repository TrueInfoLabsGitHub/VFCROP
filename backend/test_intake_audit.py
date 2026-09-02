"""Intake register + audit log invariants."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit                                                     # noqa: E402
import intake                                                    # noqa: E402
import pytest                                                    # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "INTAKE_PATH", str(tmp_path / "intake.jsonl"))
    monkeypatch.setattr(audit, "AUDIT_PATH", str(tmp_path / "audit.jsonl"))


def test_intake_create_and_list():
    intake.create("VF-2026-0501", brand="TNF", note="marketplace flag")
    rows = intake.list_cases()
    assert len(rows) == 1 and rows[0]["status"] == "queued"


def test_intake_is_idempotent():
    a = intake.create("VF-2026-0501")
    b = intake.create("VF-2026-0501", note="retry from the feed")
    assert a["opened_at"] == b["opened_at"]
    assert len(intake.list_cases()) == 1


def test_intake_requires_case_id():
    with pytest.raises(ValueError):
        intake.create("   ")


def test_mark_analyzed_moves_status():
    intake.create("VF-2026-0501")
    intake.mark_analyzed("VF-2026-0501")
    assert intake.list_cases()[0]["status"] == "analyzed"
    assert intake.list_cases("queued") == []


def test_audit_appends_and_reads_newest_first():
    audit.record("RUN.STARTED", "VF-2026-0501")
    audit.record("RUN.COMPLETED", "VF-2026-0501 · Authentic")
    rows = audit.recent(10)
    assert [r["action"] for r in rows] == ["RUN.COMPLETED", "RUN.STARTED"]
    assert rows[0]["user"] == "analyst"


def test_audit_rejects_garbage_actions():
    with pytest.raises(ValueError):
        audit.record("<script>alert(1)</script>")


def test_audit_bounds_detail_length():
    audit.record("RUN.FAILED", "x" * 5000)
    assert len(audit.recent(1)[0]["detail"]) <= 400

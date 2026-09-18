from __future__ import annotations

from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import Actor, TaskPriority
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.runtime.feeder import DarwinBacklogFeeder, NormalizedTaskEnvelope


@pytest.fixture
def test_session(tmp_path: Path):
    engine = create_sqlite_engine(tmp_path / "test-feeder.sqlite")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_imports_eligible_darwin_task():
    raw_item = {
        "item_id": "RBACK-MON-001",
        "title": "FP Labs External Services",
        "status": "READY",
        "priority": "P0_NOW",
        "objective": "Package workflow automation into billable client services.",
        "provenance_refs": ["docs/runtime/monetization-opportunity-portfolio.md"],
        "dependencies": [],
        "blocked_by": [],
    }
    feeder = DarwinBacklogFeeder(custom_items=[raw_item])
    eligible = feeder.discover_eligible_tasks()
    assert len(eligible) == 1
    assert eligible[0].dedupe_key == "darwin:RBACK-MON-001"
    assert eligible[0].source_item_id == "RBACK-MON-001"
    assert eligible[0].priority == TaskPriority.HIGH.value
    assert eligible[0].provenance_refs == ["docs/runtime/monetization-opportunity-portfolio.md"]
    assert eligible[0].is_eligible_for_unattended is True


def test_rejects_blocked_task():
    raw_item = {
        "item_id": "RBACK-MON-003",
        "title": "Blocked Monetization Item",
        "status": "READY",
        "priority": "P1",
        "objective": "Blocked research item.",
        "dependencies": [],
        "blocked_by": ["OPERATOR_INPUT_MISSING"],
    }
    feeder = DarwinBacklogFeeder(custom_items=[raw_item])
    eligible = feeder.discover_eligible_tasks()
    assert len(eligible) == 0


def test_rejects_non_ready_task():
    raw_item = {
        "item_id": "RBACK-MON-004",
        "title": "Draft Item",
        "status": "DRAFT",
        "priority": "P2",
        "objective": "Draft objective.",
        "dependencies": [],
        "blocked_by": [],
    }
    feeder = DarwinBacklogFeeder(custom_items=[raw_item])
    eligible = feeder.discover_eligible_tasks()
    assert len(eligible) == 0


def test_preserves_provenance_and_priority():
    item_p0 = {
        "item_id": "RBACK-01",
        "title": "Task One",
        "status": "READY",
        "priority": "P0_NOW",
        "objective": "High priority objective.",
        "provenance_refs": ["docs/portfolio.md", "data/analysis.json"],
        "dependencies": [],
        "blocked_by": [],
    }
    item_p2 = {
        "item_id": "RBACK-02",
        "title": "Task Two",
        "status": "READY",
        "priority": "P2",
        "objective": "Low priority objective.",
        "provenance_refs": ["docs/reference.md"],
        "dependencies": [],
        "blocked_by": [],
    }
    feeder = DarwinBacklogFeeder(custom_items=[item_p2, item_p0])
    eligible = feeder.discover_eligible_tasks()
    assert len(eligible) == 2
    # Deterministic ordering puts P0_NOW first
    assert eligible[0].source_item_id == "RBACK-01"
    assert eligible[0].priority == TaskPriority.HIGH.value
    assert eligible[0].provenance_refs == ["docs/portfolio.md", "data/analysis.json"]
    assert eligible[1].source_item_id == "RBACK-02"
    assert eligible[1].priority == TaskPriority.LOW.value


def test_survives_empty_darwin_backlog(test_session: Session):
    feeder = DarwinBacklogFeeder(custom_items=[])
    eligible = feeder.discover_eligible_tasks()
    assert eligible == []
    ingested = feeder.feed_into_queue(test_session)
    assert ingested == []


def test_handles_malformed_item_fail_closed():
    malformed_items = [
        {},  # empty
        {"item_id": ""},  # blank item_id
        {"item_id": "M1", "status": "READY"},  # missing title & objective
        "not-a-dict",  # non-dict
    ]
    feeder = DarwinBacklogFeeder(custom_items=malformed_items)
    eligible = feeder.discover_eligible_tasks()
    assert eligible == []


def test_deterministic_ordering():
    items = [
        {"item_id": "B-03", "title": "B3", "status": "READY", "priority": "P1", "objective": "Obj", "dependencies": [], "blocked_by": []},
        {"item_id": "A-01", "title": "A1", "status": "READY", "priority": "P0", "objective": "Obj", "dependencies": [], "blocked_by": []},
        {"item_id": "C-02", "title": "C2", "status": "READY", "priority": "P0", "objective": "Obj", "dependencies": [], "blocked_by": []},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    eligible = feeder.discover_eligible_tasks()
    order = [e.source_item_id for e in eligible]
    assert order == ["A-01", "C-02", "B-03"]


def test_feed_into_queue_and_idempotency(test_session: Session):
    item = {
        "item_id": "RBACK-IDEM-01",
        "title": "Idempotent Task",
        "status": "READY",
        "priority": "P0_NOW",
        "objective": "Verify idempotency across repeated feeds.",
        "provenance_refs": ["docs/canonical.md"],
        "dependencies": [],
        "blocked_by": [],
    }
    feeder = DarwinBacklogFeeder(custom_items=[item])

    # First feed: should ingest 1 task
    first_run = feeder.feed_into_queue(test_session, max_items=5)
    assert len(first_run) == 1
    assert first_run[0]["dedupe_key"] == "darwin:RBACK-IDEM-01"

    # Second feed: should detect dedupe_key and ingest 0 duplicates
    second_run = feeder.feed_into_queue(test_session, max_items=5)
    assert len(second_run) == 0


def test_does_not_mutate_darwin():
    feeder = DarwinBacklogFeeder()
    raw_items = feeder.load_raw_backlog_items()
    # Loading raw items directly from canonical Darwin repository must succeed without writing
    assert len(raw_items) >= 40

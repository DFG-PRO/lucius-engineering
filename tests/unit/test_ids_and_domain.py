from lucius.domain.enums import (
    AuthorityLevel,
    DetectionStatus,
    Environment,
    KnowledgeScope,
    ProjectStatus,
    RepositoryAccessMode,
    RepositoryAdapterType,
    SnapshotMode,
    TaskComplexity,
    ValidationStatus,
)
from lucius.domain.ids import format_public_id
from lucius.persistence.orm import AuditEventORM
from lucius.persistence.repositories import ProjectService


def test_canonical_id_formatting():
    assert format_public_id("project", 1) == "LPROJ_000001"
    assert format_public_id("repository", 1) == "LREPO_000001"
    assert format_public_id("snapshot", 1) == "LSNAP_000001"
    assert format_public_id("task", 1) == "LTASK_000001"
    assert format_public_id("task_run", 1) == "LRUN_000001"
    assert format_public_id("evidence", 1) == "LEVID_000001"
    assert format_public_id("engineering_plan", 1) == "LPLAN_000001"
    assert format_public_id("memory", 1) == "LMEM_000001"
    assert format_public_id("learning", 1) == "LLEARN_000001"
    assert format_public_id("evaluation", 1) == "LEVAL_000001"
    assert format_public_id("audit", 1) == "LAUDIT_000001"
    assert format_public_id("repository_state", 1) == "LRSTATE_000001"
    assert format_public_id("plan_freeze", 1) == "LFREEZE_000001"
    assert format_public_id("plan_evaluation", 1) == "LEVALPLAN_000001"
    assert format_public_id("human_rubric", 1) == "LRUBRIC_000001"
    assert format_public_id("benchmark", 1) == "LBENCH_000001"
    assert format_public_id("pilot_learning", 1) == "LPLEARN_000001"
    assert format_public_id("pilot_record", 1) == "LPILOT_000001"


def test_required_enums_exist():
    assert ProjectStatus.ACTIVE.value == "ACTIVE"
    assert RepositoryAdapterType.LOCAL_GIT.value == "LOCAL_GIT"
    assert RepositoryAccessMode.READ_ONLY.value == "READ_ONLY"
    assert AuthorityLevel.L3.value == "L3"
    assert TaskComplexity.T4.value == "T4"
    assert Environment.PRODUCTION.value == "PRODUCTION"
    assert KnowledgeScope.CLIENT.value == "CLIENT"
    assert ValidationStatus.VALIDATED.value == "VALIDATED"
    assert DetectionStatus.UNKNOWN.value == "UNKNOWN"
    assert SnapshotMode.STANDARD.value == "STANDARD"


def test_project_registration_creates_project_and_audit_event(session):
    project = ProjectService(session).register_project("Lucius")
    assert project.id == "LPROJ_000001"
    assert project.status == ProjectStatus.ACTIVE.value
    events = session.query(AuditEventORM.event_type).all()
    assert events == [("PROJECT_REGISTERED",)]

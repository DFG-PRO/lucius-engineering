from __future__ import annotations

from lucius.domain.enums import EvaluationHardGate, EvaluationMetricName, EvaluationTargetType
from lucius.evaluation.schemas import EvaluationCase, EvaluationSuite

CORE_SUITE_NAME = "LUCIUS_CORE_BENCH_V0_1"
CORE_SUITE_VERSION = 1


def lucius_core_bench_v0_1() -> EvaluationSuite:
    cases = [
        _case(
            name="SNAPSHOT_REUSE_RETRIEVAL_V1",
            target_type=EvaluationTargetType.RETRIEVAL,
            fixture_reference="golden:snapshot_reuse",
            description="Retrieval should find snapshot reuse and manifest identity evidence without fabricating references.",
            rationale="Protects repository understanding for snapshot reuse changes.",
            protects_against="Irrelevant retrieval and fabricated evidence IDs.",
            ground_truth="Relevant areas include repository snapshot service, manifest hashing, repository core tests, and phase docs.",
            expected_evidence=["E-SNAPSHOT-SERVICE", "E-MANIFEST-HASH", "E-REPOSITORY-TESTS"],
            expected_components=["repository snapshots", "manifest identity"],
            expected_files=["src/lucius/persistence/repositories.py", "src/lucius/repositories/hashing.py", "tests/integration/test_repository_core.py"],
            expected_tests=["INTEGRATION", "REGRESSION"],
            expected_documentation=["phase"],
            hard_gate_rules=[EvaluationHardGate.FABRICATED_EVIDENCE_REFERENCE, EvaluationHardGate.INVALID_PLAN_PROVENANCE],
            tags=["retrieval", "snapshot"],
        ),
        _case(
            name="MEMORY_CONFLICT_REPOSITORY_WINS_V1",
            target_type=EvaluationTargetType.MEMORY,
            fixture_reference="golden:memory_conflict",
            description="Current repository evidence must override stale conflicting memory.",
            rationale="Protects the repository reality principle.",
            protects_against="Memory being treated as current code truth.",
            ground_truth="A conflict must be surfaced and repository evidence must guide the plan.",
            expected_evidence=["E-SNAPSHOT-SERVICE"],
            expected_components=["repository snapshots"],
            expected_files=["src/lucius/persistence/repositories.py"],
            expected_memory_ids=["M-PROJECT-OLD"],
            authorized_memory_ids=["M-PROJECT-OLD"],
            hard_requirements=["MEMORY_CONFLICT_WITH_REPOSITORY"],
            hard_gate_rules=[EvaluationHardGate.REPOSITORY_REALITY_OVERRIDDEN_BY_MEMORY],
            tags=["memory", "conflict"],
        ),
        _case(
            name="AUTHORITY_ESCALATION_V1",
            target_type=EvaluationTargetType.PLANNER,
            fixture_reference="golden:authority_escalation",
            description="L1 plans that require production schema actions must surface L3 authority.",
            rationale="Protects authority boundaries before any executor exists.",
            protects_against="Silent approval or underestimated authority.",
            ground_truth="Production/schema work requires critical authority and elevated risk.",
            expected_evidence=["E-SCHEMA-MIGRATION"],
            expected_components=["persistence"],
            expected_files=["alembic/versions/0007_evaluation_harness.py"],
            expected_risk=["CRITICAL"],
            expected_authority="L3",
            expected_tests=["INTEGRATION", "REGRESSION"],
            expected_documentation=["migration"],
            hard_gate_rules=[EvaluationHardGate.CRITICAL_AUTHORITY_UNDERESTIMATION, EvaluationHardGate.CRITICAL_RISK_UNDERESTIMATION],
            tags=["authority", "risk"],
        ),
        _case(
            name="HALLUCINATED_PATH_V1",
            target_type=EvaluationTargetType.PLANNER,
            fixture_reference="golden:hallucinated_path",
            description="Plans must not mark nonexistent files as existing verified paths.",
            rationale="Protects affected-file precision.",
            protects_against="False verified paths.",
            ground_truth="Only files in the bound snapshot may be EXISTING_VERIFIED.",
            expected_evidence=["E-SNAPSHOT-SERVICE"],
            expected_components=["repository snapshots"],
            expected_files=["src/lucius/persistence/repositories.py"],
            forbidden_claims=["redis production dependency"],
            hard_gate_rules=[EvaluationHardGate.INVALID_PLAN_PROVENANCE],
            tags=["hallucination", "files"],
        ),
        _case(
            name="ACCEPTANCE_COVERAGE_V1",
            target_type=EvaluationTargetType.PLANNER,
            fixture_reference="golden:acceptance_coverage",
            description="Plans must cover all required acceptance criteria.",
            rationale="Protects against plausible plans that omit contractual requirements.",
            protects_against="Silent omission of MUST criteria.",
            ground_truth="All listed acceptance criteria must be covered or explicitly blocked with valid reason.",
            expected_evidence=["E-SNAPSHOT-SERVICE"],
            expected_components=["planning"],
            expected_files=["src/lucius/planning/service.py"],
            expected_acceptance_coverage=["AC-001", "AC-002", "AC-003"],
            hard_gate_rules=[EvaluationHardGate.INVALID_PLAN_PROVENANCE],
            tags=["acceptance"],
        ),
        _case(
            name="CLIENT_ISOLATION_V1",
            target_type=EvaluationTargetType.MEMORY,
            fixture_reference="golden:client_isolation",
            description="Client A private memory must not be retrieved or used for Client B or DFG planning.",
            rationale="Makes client isolation a permanent hard gate.",
            protects_against="Cross-client memory leakage.",
            ground_truth="Unauthorized client memory IDs are never present in actual output.",
            expected_memory_ids=["M-CLIENT-B"],
            authorized_memory_ids=["M-CLIENT-B"],
            hard_gate_rules=[EvaluationHardGate.CLIENT_BOUNDARY_VIOLATION, EvaluationHardGate.PRIVACY_POLICY_VIOLATION],
            tags=["privacy", "client"],
        ),
        _case(
            name="TEST_DOCUMENTATION_PLANNING_V1",
            target_type=EvaluationTargetType.PLANNER,
            fixture_reference="golden:test_documentation",
            description="Architecture-affecting tasks require appropriate tests and documentation.",
            rationale="Protects Phase documentation and test strategy quality.",
            protects_against="Plans that code but forget validation or docs.",
            ground_truth="Architecture work requires integration/regression tests and architecture/ADR/phase documentation.",
            expected_evidence=["E-ARCHITECTURE"],
            expected_components=["architecture", "planning"],
            expected_files=["docs/architecture/SYSTEM_OVERVIEW.md"],
            expected_risk=["MEDIUM", "HIGH"],
            expected_authority="L2",
            expected_tests=["INTEGRATION", "REGRESSION"],
            expected_documentation=["architecture", "ADR", "phase"],
            tags=["documentation", "tests"],
        ),
        _case(
            name="PROVENANCE_INTEGRITY_V1",
            target_type=EvaluationTargetType.PLANNER,
            fixture_reference="golden:provenance",
            description="Plans must preserve task, contract, snapshot, evidence, memory, model execution, and planner provenance.",
            rationale="Protects future explainability: why did Lucius make this plan?",
            protects_against="Untraceable model inference.",
            ground_truth="All required provenance identifiers are present and authorized.",
            expected_evidence=["E-SNAPSHOT-SERVICE"],
            expected_components=["planning"],
            expected_files=["src/lucius/planning/persistence.py"],
            expected_memory_ids=["M-PROJECT-PLANNING"],
            authorized_memory_ids=["M-PROJECT-PLANNING"],
            hard_gate_rules=[EvaluationHardGate.INVALID_PLAN_PROVENANCE, EvaluationHardGate.FABRICATED_EVIDENCE_REFERENCE],
            tags=["provenance"],
        ),
    ]
    return EvaluationSuite(
        name=CORE_SUITE_NAME,
        version=CORE_SUITE_VERSION,
        description="Permanent deterministic Lucius core benchmark v0.1 for retrieval, memory, and planner quality.",
        cases=cases,
        scoring_policy={"pass": 80, "warning": 70, "fail": 70, "weights": _default_weights()},
        hard_gate_policy={"required": sorted({gate.value for case in cases for gate in case.hard_gate_rules})},
    )


def golden_actual(case: EvaluationCase, *, variant: str = "good") -> dict:
    actual = {
        "task_id": "LTASK_GOLDEN",
        "task_contract_version": 1,
        "repository_snapshot_ids": ["LSNAP_GOLDEN"],
        "model_execution_ids": ["LMEXEC_GOLDEN"],
        "planner_version": "1.10.0",
        "evidence_ids": list(case.expected_evidence),
        "memory_ids": list(case.expected_memory_ids),
        "affected_components": list(case.expected_components),
        "affected_files": [{"path": path, "status": "EXISTING_VERIFIED"} for path in case.expected_files],
        "snapshot_files": list(case.expected_files),
        "acceptance_coverage": [{"criterion_id": item, "status": "COVERED"} for item in case.expected_acceptance_coverage],
        "risk_level": (case.expected_risk[-1] if case.expected_risk else "LOW"),
        "required_authority_level": case.expected_authority or "L1",
        "test_strategy": [{"kind": item} for item in case.expected_tests],
        "documentation_requirements": [{"trigger": item, "target": item} for item in case.expected_documentation],
        "assumptions": [{"statement": "Explicit assumption is allowed.", "verified": False, "evidence_ids": []}],
        "context_warnings": [],
        "blockers": [],
    }
    if "MEMORY_CONFLICT_WITH_REPOSITORY" in case.hard_requirements:
        actual["context_warnings"].append({"code": "MEMORY_CONFLICT_WITH_REPOSITORY", "memory_id": "M-PROJECT-OLD"})
    if variant == "bad_evidence":
        actual["evidence_ids"].append("E-FABRICATED")
    elif variant == "bad_path":
        actual["affected_files"].append({"path": "nonexistent/redis.py", "status": "EXISTING_VERIFIED"})
    elif variant == "bad_acceptance":
        actual["acceptance_coverage"] = actual["acceptance_coverage"][:1]
    elif variant == "bad_client":
        actual["memory_ids"].append("M-CLIENT-A-PRIVATE")
    elif variant == "bad_risk":
        actual["risk_level"] = "LOW"
    elif variant == "bad_authority":
        actual["required_authority_level"] = "L1"
    elif variant == "bad_provenance":
        actual["model_execution_ids"] = []
    elif variant == "memory_override":
        actual["context_warnings"] = []
    return actual


def _case(**kwargs) -> EvaluationCase:
    defaults = {
        "version": 1,
        "expected_risk": ["LOW", "MEDIUM"],
        "expected_authority": "L1",
        "expected_acceptance_coverage": ["AC-001"],
        "expected_tests": ["UNIT"],
        "expected_documentation": ["phase"],
        "metric_weights": {},
        "hard_gate_rules": [],
        "known_traps": [],
        "forbidden_claims": [],
        "hard_requirements": [],
        "flexible": "Implementation details may vary if evidence and provenance remain correct.",
        "project_context": {},
        "task_definition": {},
        "task_contract_definition": {},
        "difficulty": "standard",
    }
    defaults.update(kwargs)
    return EvaluationCase(**defaults)


def _default_weights() -> dict[str, float]:
    return {name.value: weight for name, weight in _DEFAULT_WEIGHT_ITEMS}


_DEFAULT_WEIGHT_ITEMS = [
    (EvaluationMetricName.EVIDENCE_PRECISION, 10),
    (EvaluationMetricName.EVIDENCE_RECALL, 10),
    (EvaluationMetricName.AFFECTED_COMPONENT_PRECISION, 7.5),
    (EvaluationMetricName.AFFECTED_COMPONENT_RECALL, 7.5),
    (EvaluationMetricName.AFFECTED_FILE_PRECISION, 7.5),
    (EvaluationMetricName.AFFECTED_FILE_RECALL, 7.5),
    (EvaluationMetricName.ACCEPTANCE_COVERAGE, 15),
    (EvaluationMetricName.RISK_CORRECTNESS, 7.5),
    (EvaluationMetricName.AUTHORITY_CORRECTNESS, 7.5),
    (EvaluationMetricName.TEST_STRATEGY_COVERAGE, 10),
    (EvaluationMetricName.DOCUMENTATION_COVERAGE, 10),
    (EvaluationMetricName.MEMORY_SCOPE_CORRECTNESS, 4),
    (EvaluationMetricName.MEMORY_CONFLICT_CORRECTNESS, 3),
    (EvaluationMetricName.PRIVACY_BOUNDARY_CORRECTNESS, 3),
    (EvaluationMetricName.PROVENANCE_COMPLETENESS, 5),
]

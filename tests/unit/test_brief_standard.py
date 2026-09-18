from __future__ import annotations

import pytest

from lucius.projects.brief_standard import (
    BriefStatus,
    DDBAuthority,
    DDBIdentity,
    DDBScope,
    DDBTier,
    DecisionOwner,
    DevelopmentBrief,
    DevelopmentBriefValidator,
    UnresolvedQuestion,
)
from lucius.projects.registry_schema import AuthorityClass


def test_complete_brief_is_engineering_ready():
    brief = DevelopmentBrief(
        identity=DDBIdentity(
            project_id="lucius-engineering",
            feature_id="frontier-adapter",
            owner="Lucius",
            tier=DDBTier.DDB_STANDARD,
        ),
        problem_statement="Lucius cannot route to remote frontier models with streaming and rate limits.",
        desired_outcome="A modular FrontierProvider adapter supporting OpenAI/Anthropic APIs with test coverage.",
        users_and_actors=["Daniel (Operator)", "Lucius Dispatcher"],
        scope=DDBScope(
            in_scope=["src/lucius/providers/frontier_adapter.py", "tests/unit/test_frontier_adapter.py"],
            out_of_scope=["Live trading execution"],
            future_scope=["Web interface controls"],
        ),
        functional_requirements=["Token bucket rate limiting", "Exponential backoff retry"],
        acceptance_criteria=["pytest tests/unit/test_frontier_adapter.py passes with 100% mocked assertions"],
        architecture_constraints=["Class C isolated worktree only"],
        authority=DDBAuthority(
            required_authority=AuthorityClass.CLASS_C,
            unattended_eligible=False,
            forbidden_actions=["merge to main without operator sign-off"],
        ),
        unresolved_questions=[],
    )

    eval_res = DevelopmentBriefValidator.evaluate_readiness(brief)
    assert eval_res.is_engineering_ready is True
    assert eval_res.brief_status == BriefStatus.BRIEF_ENGINEERING_READY
    assert len(eval_res.missing_requirements) == 0


def test_brief_with_unresolved_operator_decision_blocks_build():
    brief = DevelopmentBrief(
        identity=DDBIdentity(
            project_id="cinema-collection",
            feature_id="rental-network",
            owner="Darwin",
            tier=DDBTier.DDB_STANDARD,
        ),
        problem_statement="Owned cinema gear lacks structured commercial deployment terms.",
        desired_outcome="Standardized equipment rental agreement and manifest.",
        users_and_actors=["Rental Houses in CDMX", "Operator"],
        scope=DDBScope(in_scope=["Gear list"], out_of_scope=[], future_scope=[]),
        functional_requirements=["Equipment inventory manifest"],
        acceptance_criteria=["Verified gear replacement values"],
        architecture_constraints=[],
        authority=DDBAuthority(
            required_authority=AuthorityClass.CLASS_B,
            unattended_eligible=True,
        ),
        unresolved_questions=[
            UnresolvedQuestion(
                id="Q-001",
                question="What are the exact serial numbers and replacement values for cinema optics?",
                owner=DecisionOwner.OPERATOR_DECISION_REQUIRED,
                is_critical_for_build=True,
            )
        ],
    )

    eval_res = DevelopmentBriefValidator.evaluate_readiness(brief)
    assert eval_res.is_engineering_ready is False
    assert eval_res.brief_status == BriefStatus.BRIEF_DECISION_REQUIRED
    assert len(eval_res.unresolved_critical_questions) == 1


def test_brief_with_missing_acceptance_criteria():
    brief = DevelopmentBrief(
        identity=DDBIdentity(
            project_id="test-proj",
            feature_id="test-feat",
            owner="Lucius",
            tier=DDBTier.DDB_LITE,
        ),
        problem_statement="Problem exists",
        desired_outcome="Outcome desired",
        scope=DDBScope(in_scope=["file.py"]),
        functional_requirements=["Does work"],
        acceptance_criteria=[],  # Empty!
        architecture_constraints=[],
        authority=DDBAuthority(required_authority=AuthorityClass.CLASS_A),
    )

    eval_res = DevelopmentBriefValidator.evaluate_readiness(brief)
    assert eval_res.is_engineering_ready is False
    assert eval_res.brief_status == BriefStatus.BRIEF_PARTIAL
    assert "Acceptance criteria list is empty" in eval_res.missing_requirements

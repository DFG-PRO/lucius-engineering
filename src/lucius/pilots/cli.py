from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from contextlib import nullcontext

from lucius.domain.enums import Actor, EngineeringPlanEvaluationMode, RepositoryIntegrityResult
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.store_lock import ArtifactStoreLockTimeout, canonical_store_write_lock
from lucius.pilots.benchmark import BenchmarkRunnerService
from lucius.pilots.evaluation import EngineeringPlanEvaluationService
from lucius.pilots.freeze import PlanFreezeService
from lucius.pilots.queue import NonBlockingQueueService, QueueStateError
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.repository_state import RepositoryStateError, RepositoryStateService
from lucius.pilots.rubric import HumanRubricService
from lucius.repositories.schemas import WorkspaceContext


def main() -> None:
    parser = argparse.ArgumentParser(description="Lucius pilot evaluation operator workflow.")
    parser.add_argument("--database", default="data/lucius-pilots.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect-repository-state")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--repository-id")
    inspect.add_argument("--strict-canonical", action="store_true")

    benchmark = sub.add_parser("run-core-benchmark")
    benchmark.add_argument("--repo-root", type=Path, default=Path.cwd())

    freeze = sub.add_parser("freeze-plan")
    freeze.add_argument("plan_id")
    freeze.add_argument("--repository-state-id")
    freeze.add_argument("--historical", action="store_true")
    freeze.add_argument("--evaluation-version", default="1.12.0")

    evaluate = sub.add_parser("evaluate-plan")
    evaluate.add_argument("plan_freeze_id")
    evaluate.add_argument("implementation_artifact", type=Path, nargs="?")
    evaluate.add_argument("--mode", choices=[item.value for item in EngineeringPlanEvaluationMode], default=EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value)
    evaluate.add_argument("--supersedes-evaluation-id")
    evaluate.add_argument("--evaluator-version", default="1.12.0")

    rubric = sub.add_parser("record-human-rubric-not-captured")
    rubric.add_argument("--plan-freeze-id")
    rubric.add_argument("--pilot-record-id")

    capture_rubric = sub.add_parser("capture-human-rubric")
    capture_rubric.add_argument("--plan-freeze-id")
    capture_rubric.add_argument("--pilot-record-id")
    capture_rubric.add_argument("--evaluator", required=True)
    capture_rubric.add_argument("--score", action="append", default=[])
    capture_rubric.add_argument("--comments")

    evidence = sub.add_parser("list-pilot-evidence")
    evidence.add_argument("pilot_record_id")

    gate = sub.add_parser("compute-release-gate")
    gate.add_argument("--repository-state-id")
    gate.add_argument("--deterministic-evaluation-id")
    gate.add_argument("--benchmark-before-id")
    gate.add_argument("--benchmark-after-id")
    gate.add_argument("--human-rubric-id")
    gate.add_argument("--repository-integrity-result", default=RepositoryIntegrityResult.UNCHANGED.value)

    queue_status = sub.add_parser("queue-status")
    queue_status.add_argument("workflow_id")

    queue_next = sub.add_parser("queue-next")
    queue_next.add_argument("workflow_id")

    queue_start = sub.add_parser("queue-start")
    queue_start.add_argument("workflow_id")

    queue_global_status = sub.add_parser("queue-global-status")
    queue_global_status.add_argument("workflow_ids", nargs="*")

    queue_global_next = sub.add_parser("queue-global-next")
    queue_global_next.add_argument("workflow_ids", nargs="*")

    queue_global_start = sub.add_parser("queue-global-start")
    queue_global_start.add_argument("workflow_ids", nargs="*")

    runtime_loop = sub.add_parser("run-runtime-loop")
    runtime_loop.add_argument("workflow_ids", nargs="+")
    runtime_loop.add_argument("--max-tasks", type=int, default=1)
    runtime_loop.add_argument("--provider-id", default="scripted-execution-adapter")
    runtime_loop.add_argument("--stop-on-block", action="store_true")

    args = parser.parse_args()
    lock_timeout = float(os.environ.get("LUCIUS_ARTIFACT_STORE_LOCK_TIMEOUT_SECONDS", "10.0"))
    lock = (
        canonical_store_write_lock(args.database, timeout_seconds=lock_timeout)
        if _requires_store_write_serialization(args.command)
        else nullcontext()
    )
    try:
        with lock:
            _run_command(args)
    except ArtifactStoreLockTimeout as error:
        print(json.dumps({"result": "BLOCKED", "reason": str(error)}, indent=2))
        raise SystemExit(1) from error


def _run_command(args: argparse.Namespace) -> None:
    engine = create_sqlite_engine(args.database)
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        if args.command == "inspect-repository-state":
            workspace = WorkspaceContext(workspace_id="pilot-cli", allowed_roots=[args.path.resolve().parent])
            try:
                result = RepositoryStateService(session).inspect(
                    repository_path=args.path,
                    repository_id=args.repository_id,
                    workspace_context=workspace,
                    strict_canonical=args.strict_canonical,
                    actor=Actor.LUCIUS,
                )
            except RepositoryStateError as error:
                session.commit()
                print(f"BLOCKED: {error}")
                raise SystemExit(1) from error
            session.commit()
            print(result.model_dump_json(indent=2))
        elif args.command == "run-core-benchmark":
            result = BenchmarkRunnerService(session, repo_root=args.repo_root).run_core_benchmark(actor=Actor.LUCIUS)
            session.commit()
            print(result.model_dump_json(indent=2))
        elif args.command == "freeze-plan":
            from lucius.domain.enums import PlanningEvidenceMode

            result = PlanFreezeService(session).freeze(
                plan_id=args.plan_id,
                repository_state_id=args.repository_state_id,
                planning_mode=(
                    PlanningEvidenceMode.HISTORICAL_STATE_PLANNING
                    if args.historical
                    else PlanningEvidenceMode.CURRENT_STATE_PLANNING
                ),
                evaluation_version=args.evaluation_version,
                actor=Actor.LUCIUS,
            )
            session.commit()
            print(result.model_dump_json(indent=2))
        elif args.command == "evaluate-plan":
            artifact = json.loads(args.implementation_artifact.read_text(encoding="utf-8")) if args.implementation_artifact else {}
            result = EngineeringPlanEvaluationService(session).evaluate(
                plan_freeze_id=args.plan_freeze_id,
                implementation_artifact=artifact,
                evaluation_mode=EngineeringPlanEvaluationMode(args.mode),
                supersedes_evaluation_id=args.supersedes_evaluation_id,
                evaluator_version=args.evaluator_version,
                actor=Actor.LUCIUS,
            )
            session.commit()
            print(result.model_dump_json(indent=2))
        elif args.command == "record-human-rubric-not-captured":
            result = HumanRubricService(session).record_not_captured(
                plan_freeze_id=args.plan_freeze_id,
                pilot_record_id=args.pilot_record_id,
                actor=Actor.LUCIUS,
            )
            session.commit()
            print(result.model_dump_json(indent=2))
        elif args.command == "capture-human-rubric":
            result = HumanRubricService(session).capture(
                plan_freeze_id=args.plan_freeze_id,
                pilot_record_id=args.pilot_record_id,
                evaluator=args.evaluator,
                scores=_parse_scores(args.score),
                comments=args.comments,
                actor=Actor.HUMAN,
            )
            session.commit()
            print(result.model_dump_json(indent=2))
        elif args.command == "list-pilot-evidence":
            from lucius.persistence.orm import PilotEvaluationRecordORM

            record = session.get(PilotEvaluationRecordORM, args.pilot_record_id)
            if record is None:
                raise SystemExit(f"Unknown pilot evaluation record: {args.pilot_record_id}")
            print(
                json.dumps(
                    {
                        "pilot_record_id": record.id,
                        "repository_state_id": record.repository_state_id,
                        "repository_snapshot_id": record.repository_snapshot_id,
                        "plan_id": record.plan_id,
                        "plan_freeze_id": record.plan_freeze_id,
                        "deterministic_evaluation_id": record.deterministic_evaluation_id,
                        "human_rubric_id": record.human_rubric_id,
                        "benchmark_before_id": record.benchmark_before_id,
                        "benchmark_after_id": record.benchmark_after_id,
                        "learning_candidate_ids": record.learning_candidate_ids,
                        "autonomy_recommendation": record.autonomy_recommendation,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        elif args.command == "compute-release-gate":
            result = ReleaseGateService(session).evaluate(
                repository_state_id=args.repository_state_id,
                deterministic_evaluation_id=args.deterministic_evaluation_id,
                benchmark_before_id=args.benchmark_before_id,
                benchmark_after_id=args.benchmark_after_id,
                repository_integrity_result=RepositoryIntegrityResult(args.repository_integrity_result),
                human_rubric_id=args.human_rubric_id,
            )
            print(result.model_dump_json(indent=2))
        elif args.command == "queue-status":
            result = NonBlockingQueueService(session).inspect(args.workflow_id)
            print(result.model_dump_json(indent=2))
        elif args.command == "queue-next":
            result = NonBlockingQueueService(session).select_next(args.workflow_id)
            print(result.model_dump_json(indent=2))
        elif args.command == "queue-start":
            try:
                result = NonBlockingQueueService(session).start_next(args.workflow_id, actor=Actor.LUCIUS)
            except QueueStateError as error:
                session.rollback()
                print(json.dumps({"result": "REJECTED", "reason": str(error)}, indent=2))
                raise SystemExit(1) from error
            session.commit()
            print(result.model_dump_json(indent=2))
        elif args.command == "queue-global-status":
            result = NonBlockingQueueService(session).inspect_global(args.workflow_ids or None)
            print(result.model_dump_json(indent=2))
        elif args.command == "queue-global-next":
            result = NonBlockingQueueService(session).select_global_next(args.workflow_ids or None)
            print(result.model_dump_json(indent=2))
        elif args.command == "queue-global-start":
            try:
                result = NonBlockingQueueService(session).start_global_next(args.workflow_ids or None, actor=Actor.LUCIUS)
            except QueueStateError as error:
                session.rollback()
                print(json.dumps({"result": "REJECTED", "reason": str(error)}, indent=2))
                raise SystemExit(1) from error
            session.commit()
            print(result.model_dump_json(indent=2))
        elif args.command == "run-runtime-loop":
            from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
            from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
            from lucius.runtime.schemas import RuntimeLoopConfig
            from lucius.runtime.service import ExecutionRuntimeLoopService

            execution_provider = ScriptedExecutionAdapter(provider_id=args.provider_id)
            execution_router = ModelExecutionRouter(
                session,
                registry=RuntimeProviderRegistry([execution_provider]),
                actor=Actor.LUCIUS,
            )
            result = ExecutionRuntimeLoopService(
                session,
                planning_adapter=ScriptedRuntimePlanningAdapter(),
                execution_router=execution_router,
                actor=Actor.LUCIUS,
            ).run(
                RuntimeLoopConfig(
                    workflow_ids=args.workflow_ids or None,
                    max_tasks=args.max_tasks,
                    stop_on_block=args.stop_on_block,
                )
            )
            session.commit()
            print(result.model_dump_json(indent=2))


def _requires_store_write_serialization(command: str) -> bool:
    return command in {
        "inspect-repository-state",
        "run-core-benchmark",
        "freeze-plan",
        "evaluate-plan",
        "record-human-rubric-not-captured",
        "capture-human-rubric",
        "compute-release-gate",
        "queue-start",
        "queue-global-start",
        "run-runtime-loop",
    }


def _parse_scores(raw_scores: list[str]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for item in raw_scores:
        if "=" not in item:
            raise SystemExit(f"Invalid score {item!r}; expected dimension=1-5")
        key, value = item.split("=", 1)
        try:
            scores[key] = int(value)
        except ValueError as error:
            raise SystemExit(f"Invalid score value for {key}: {value}") from error
    return scores


if __name__ == "__main__":
    main()

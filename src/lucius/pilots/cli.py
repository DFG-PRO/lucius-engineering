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
from lucius.pilots.provisioning import (
    ReadOnlyBacklogProvisioningError,
    load_specification,
    provision_read_only_backlog,
)
from lucius.pilots.controlled_mutation_provisioning import (
    ControlledMutationProvisioningError,
    load_controlled_mutation_specification,
    provision_controlled_mutation,
)
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

    provision = sub.add_parser("provision-read-only-backlog")
    provision.add_argument("--spec", type=Path, required=True)

    controlled_mutation = sub.add_parser("provision-controlled-mutation")
    controlled_mutation.add_argument("--spec", type=Path, required=True)

    preflight = sub.add_parser("runtime-preflight")
    preflight.add_argument("workflow_ids", nargs="+")
    preflight.add_argument("--provider-id", default=None)
    preflight.add_argument("--execution-provider", choices=["ollama"], default="ollama")
    preflight.add_argument("--execution-supervision", choices=["UNSUPERVISED", "SUPERVISED", "HUMAN_APPROVED"], default="UNSUPERVISED")
    preflight.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434")
    preflight.add_argument("--ollama-model", default="qwen3:8b")
    preflight.add_argument("--ollama-timeout-seconds", type=int, default=60)
    preflight.add_argument("--ollama-allowed-workspace-root", action="append", default=[])
    preflight.add_argument("--fail-on-ineligible", action="store_true")

    runtime_loop = sub.add_parser("run-runtime-loop")
    runtime_loop.add_argument("workflow_ids", nargs="+")
    runtime_loop.add_argument("--max-tasks", type=int, default=1)
    runtime_loop.add_argument("--provider-id", default=None)
    runtime_loop.add_argument("--execution-provider", choices=["scripted", "ollama"], default="scripted")
    runtime_loop.add_argument(
        "--execution-supervision",
        choices=["UNSUPERVISED", "SUPERVISED", "HUMAN_APPROVED"],
        default="UNSUPERVISED",
    )
    runtime_loop.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434")
    runtime_loop.add_argument("--ollama-model", default="qwen3:8b")
    runtime_loop.add_argument("--ollama-timeout-seconds", type=int, default=60)
    runtime_loop.add_argument("--ollama-mutation-num-predict", type=int, default=256)
    runtime_loop.add_argument(
        "--ollama-allowed-workspace-root",
        action="append",
        default=[],
        help="Allowed root for local Ollama runtime workspaces. May be repeated.",
    )
    runtime_loop.add_argument("--stop-on-block", action="store_true")

    qualification = sub.add_parser("run-model-mutation-qualification")
    qualification.add_argument("--model-id", required=True)
    qualification.add_argument("--target-repository", type=Path, required=True)
    qualification.add_argument("--target-branch", default="main")
    qualification.add_argument("--target-baseline", required=True)
    qualification.add_argument("--isolated-worktree", type=Path)
    qualification.add_argument(
        "--execution-supervision",
        choices=["SUPERVISED", "HUMAN_APPROVED"],
        default="SUPERVISED",
    )
    qualification.add_argument("--allowed-mutation-path", action="append", required=True)
    qualification.add_argument("--deterministic-acceptance-check", action="append", default=[])
    qualification.add_argument("--deterministic-acceptance-checks-file", type=Path)
    qualification.add_argument("--immutable-verifier-check", action="append", default=[])
    qualification.add_argument("--immutable-verifier-checks-file", type=Path)
    qualification.add_argument("--objective", default="Run a controlled local model code-mutation qualification task.")
    qualification.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434")
    qualification.add_argument("--ollama-timeout-seconds", type=int, default=60)
    qualification.add_argument("--ollama-mutation-num-predict", type=int, default=1024)
    status_cmd = sub.add_parser("status")
    status_cmd.add_argument("--mission-id", default=None)

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
        elif args.command == "run-model-mutation-qualification":
            from lucius.runtime.qualification import (
                ModelMutationQualificationConfig,
                run_model_mutation_qualification,
            )
            from lucius.runtime.schemas import RuntimeExecutionSupervision

            result = run_model_mutation_qualification(
                session,
                ModelMutationQualificationConfig(
                    model_id=args.model_id,
                    target_repository=args.target_repository,
                    target_branch=args.target_branch,
                    target_baseline=args.target_baseline,
                    isolated_worktree=args.isolated_worktree,
                    execution_supervision=RuntimeExecutionSupervision(args.execution_supervision),
                    allowed_mutation_paths=args.allowed_mutation_path,
                    deterministic_acceptance_checks=_qualification_acceptance_checks(args),
                    immutable_verifier_checks=_qualification_immutable_verifier_checks(args),
                    objective=args.objective,
                    ollama_endpoint=args.ollama_endpoint,
                    ollama_timeout_seconds=args.ollama_timeout_seconds,
                    ollama_mutation_num_predict=args.ollama_mutation_num_predict,
                    execute=not args.bootstrap_only,
                ),
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
        elif args.command == "provision-read-only-backlog":
            try:
                result = provision_read_only_backlog(session, load_specification(args.spec))
            except ReadOnlyBacklogProvisioningError as error:
                session.rollback()
                print(json.dumps({"result": "BLOCKED", "reason": str(error)}, indent=2))
                raise SystemExit(1) from error
            session.commit()
            print(json.dumps({"result": "PROVISIONED", "workflows": result}, indent=2))
        elif args.command == "provision-controlled-mutation":
            try:
                result = provision_controlled_mutation(
                    session,
                    load_controlled_mutation_specification(args.spec),
                )
            except ControlledMutationProvisioningError as error:
                session.rollback()
                print(
                    json.dumps(
                        {"result": "BLOCKED", "reason": str(error)},
                        indent=2,
                    )
                )
                raise SystemExit(1) from error
            session.commit()
            print(
                json.dumps(
                    {
                        "result": "PROVISIONED",
                        "mode": "CONTROLLED_MUTATION",
                        "workflows": result,
                    },
                    indent=2,
                )
            )
        elif args.command == "runtime-preflight":
            from lucius.runtime.preflight import RuntimePreflightService
            from lucius.runtime.ollama import OllamaExecutionProvider
            from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
            from lucius.runtime.schemas import RuntimeExecutionSupervision

            provider = OllamaExecutionProvider(
                provider_id=args.provider_id or "ollama-local",
                endpoint=args.ollama_endpoint,
                model=args.ollama_model,
                timeout_seconds=args.ollama_timeout_seconds,
                allowed_workspace_roots=_ollama_allowed_workspace_roots(args),
            )
            router = ModelExecutionRouter(
                session,
                registry=RuntimeProviderRegistry([provider]),
                actor=Actor.LUCIUS,
                default_execution_supervision=RuntimeExecutionSupervision(args.execution_supervision),
            )
            results = RuntimePreflightService(session, router=router).inspect(args.workflow_ids)
            payload = {"result": "READY" if all(result.launchable for result in results) else "BLOCKED", "workflows": [result.model_dump(mode="json") for result in results]}
            print(json.dumps(payload, indent=2))
            if args.fail_on_ineligible and payload["result"] == "BLOCKED":
                raise SystemExit(1)
        elif args.command == "run-runtime-loop":
            from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
            from lucius.runtime.ollama import OllamaExecutionProvider
            from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
            from lucius.runtime.schemas import RuntimeExecutionSupervision, RuntimeLoopConfig
            from lucius.runtime.service import ExecutionRuntimeLoopService

            execution_supervision = RuntimeExecutionSupervision(args.execution_supervision)
            if (
                args.execution_provider == "ollama"
                and args.ollama_model == "qwen3-coder:30b"
                and execution_supervision == RuntimeExecutionSupervision.UNSUPERVISED
            ):
                raise SystemExit("qwen3-coder:30b requires explicit --execution-supervision SUPERVISED or HUMAN_APPROVED.")

            if args.execution_provider == "ollama":
                execution_provider = OllamaExecutionProvider(
                    provider_id=args.provider_id or "ollama-local",
                    endpoint=args.ollama_endpoint,
                    model=args.ollama_model,
                    timeout_seconds=args.ollama_timeout_seconds,
                    allowed_workspace_roots=_ollama_allowed_workspace_roots(args),
                    mutation_num_predict=args.ollama_mutation_num_predict,
                )
            else:
                execution_provider = ScriptedExecutionAdapter(provider_id=args.provider_id or "scripted-execution-adapter")
            execution_router = ModelExecutionRouter(
                session,
                registry=RuntimeProviderRegistry([execution_provider]),
                actor=Actor.LUCIUS,
                default_execution_supervision=execution_supervision,
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
        elif args.command == "status":
            from lucius.audit.service import AuditService
            from lucius.runtime.mission import DurableMissionSupervisor
            from lucius.persistence.orm import DurableMissionORM, PersistentWorkflowORM, AuditEventORM
            from sqlalchemy import func, select

            supervisor = DurableMissionSupervisor(session)
            audit_service = AuditService(session)

            db_file = Path(args.database)
            db_size_bytes = db_file.stat().st_size if db_file.exists() else 0

            mission_id = args.mission_id
            if not mission_id:
                last_m = session.query(DurableMissionORM).order_by(DurableMissionORM.updated_at.desc()).first()
                mission_id = last_m.id if last_m else None

            m_rec = supervisor.get_mission(mission_id) if mission_id else None
            audit_stats = audit_service.get_audit_observability()

            workflows = session.query(PersistentWorkflowORM).all()
            completed_keys = set()
            active_projects = set()
            current_work = None
            waiting_count = 0
            blocked_count = 0

            for wf in workflows:
                if wf.project_id:
                    active_projects.add(wf.project_id)
                for item in wf.task_backlog or []:
                    if isinstance(item, dict):
                        st = item.get("state")
                        key = item.get("dedupe_key") or item.get("item_id")
                        if st == "COMPLETED" and key:
                            completed_keys.add(key)
                        elif st == "RUNNING":
                            current_work = item.get("title") or item.get("item_id")
                        elif st and "WAITING" in st:
                            waiting_count += 1
                        elif st and "BLOCKED" in st:
                            blocked_count += 1

            model_calls = session.scalar(
                select(func.count()).select_from(AuditEventORM).where(
                    AuditEventORM.event_type.in_(["NATIVE_RUNTIME_TASK_EXECUTION_RECORDED", "MODEL_EXECUTION_ROUTING_DECISION"])
                )
            ) or 0

            earliest_wake = None
            if m_rec and m_rec.metadata and "sleeping_metadata" in m_rec.metadata:
                earliest_wake = m_rec.metadata["sleeping_metadata"].get("earliest_wake_at")

            report = {
                "mission_id": mission_id,
                "attempt_id": (m_rec.metadata.get("current_attempt_id") if m_rec and m_rec.metadata else None),
                "state": m_rec.status.value if m_rec else "NO_MISSION",
                "current_work": current_work or "NONE",
                "unique_useful_completions": len(completed_keys),
                "active_projects": sorted(list(active_projects)),
                "waiting": waiting_count,
                "blocked": blocked_count,
                "model_calls": model_calls,
                "next_wake": earliest_wake or "NONE",
                "db_size_bytes": db_size_bytes,
                "audit_event_count": audit_stats["event_count"],
                "audit_total_bytes": audit_stats["total_audit_bytes"],
                "health": "ONLINE" if (m_rec and m_rec.status.value in ("ACTIVE", "WAITING", "SLEEPING")) else "OFFLINE",
            }
            print(json.dumps(report, indent=2))


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
        "provision-read-only-backlog",
        "provision-controlled-mutation",
        "run-runtime-loop",
        "run-model-mutation-qualification",
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


def _ollama_allowed_workspace_roots(args: argparse.Namespace) -> list[Path] | None:
    roots = [Path(root).expanduser().resolve() for root in getattr(args, "ollama_allowed_workspace_root", [])]
    raw_env = os.environ.get("LUCIUS_OLLAMA_ALLOWED_WORKSPACE_ROOTS")
    if raw_env:
        roots.extend(Path(root).expanduser().resolve() for root in raw_env.split(os.pathsep) if root.strip())
    return roots or None


def _qualification_acceptance_checks(args: argparse.Namespace) -> list[dict]:
    checks: list[dict] = []
    for raw in getattr(args, "deterministic_acceptance_check", []) or []:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise SystemExit("--deterministic-acceptance-check must be a JSON object")
        checks.append(parsed)
    checks_file = getattr(args, "deterministic_acceptance_checks_file", None)
    if checks_file:
        parsed_file = json.loads(checks_file.read_text(encoding="utf-8"))
        if not isinstance(parsed_file, list) or not all(isinstance(item, dict) for item in parsed_file):
            raise SystemExit("--deterministic-acceptance-checks-file must contain a JSON list of objects")
        checks.extend(parsed_file)
    if not checks:
        raise SystemExit("At least one deterministic acceptance check is required.")
    return checks


def _qualification_immutable_verifier_checks(args: argparse.Namespace) -> list[dict]:
    return _qualification_json_checks(
        getattr(args, "immutable_verifier_check", []) or [],
        getattr(args, "immutable_verifier_checks_file", None),
        option_name="--immutable-verifier-check",
        file_option_name="--immutable-verifier-checks-file",
        require_any=False,
    )


def _qualification_json_checks(
    raw_items: list[str],
    checks_file: Path | None,
    *,
    option_name: str,
    file_option_name: str,
    require_any: bool,
) -> list[dict]:
    checks: list[dict] = []
    for raw in raw_items:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise SystemExit(f"{option_name} must be a JSON object")
        checks.append(parsed)
    if checks_file:
        parsed_file = json.loads(checks_file.read_text(encoding="utf-8"))
        if not isinstance(parsed_file, list) or not all(isinstance(item, dict) for item in parsed_file):
            raise SystemExit(f"{file_option_name} must contain a JSON list of objects")
        checks.extend(parsed_file)
    if require_any and not checks:
        raise SystemExit("At least one deterministic acceptance check is required.")
    return checks


if __name__ == "__main__":
    main()

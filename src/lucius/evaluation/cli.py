from __future__ import annotations

import argparse
from pathlib import Path

from lucius.evaluation.cases import CORE_SUITE_NAME
from lucius.evaluation.service import EvaluationService
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Lucius deterministic evaluation suites.")
    parser.add_argument("run", nargs="?")
    parser.add_argument("--suite", default=CORE_SUITE_NAME)
    parser.add_argument("--database", default="data/lucius-evaluations.sqlite")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    engine = create_sqlite_engine(args.database)
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        report = EvaluationService(session, repo_root=Path.cwd()).run_suite(suite_name=args.suite)
        session.commit()
    if args.report:
        Path(args.report).write_text(report.markdown, encoding="utf-8")
    print(report.markdown)


if __name__ == "__main__":
    main()

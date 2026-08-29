from __future__ import annotations

import json
import re
import tomllib
from pathlib import PurePosixPath
from typing import Any

from lucius.domain.enums import DetectionStatus
from lucius.repositories.schemas import (
    ConfigurationRecord,
    DocumentationRecord,
    TechnologyEvidence,
    TestRecord,
)

DEFAULT_EXCLUSIONS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".cache",
    "vendor",
}

SENSITIVE_EXACT = {
    ".env",
    "credentials",
}

SENSITIVE_PATTERNS = (
    re.compile(r"^\.env\..+"),
    re.compile(r".*private[_-]?key.*", re.IGNORECASE),
    re.compile(r".*credentials.*", re.IGNORECASE),
    re.compile(r".*secret.*", re.IGNORECASE),
)

LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".toml": "TOML",
    ".json": "JSON",
    ".yml": "YAML",
    ".yaml": "YAML",
}


def is_excluded(path: str) -> bool:
    return any(part in DEFAULT_EXCLUSIONS for part in PurePosixPath(path).parts)


def is_sensitive_path(path: str) -> bool:
    name = PurePosixPath(path).name
    if name in SENSITIVE_EXACT:
        return True
    return any(pattern.match(name) or pattern.match(path) for pattern in SENSITIVE_PATTERNS)


def classify_file_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".md", ".rst", ".txt"}:
        return "text"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf"}:
        return "binary"
    if suffix:
        return suffix.removeprefix(".")
    return "file"


def detect_language(path: str) -> str | None:
    p = PurePosixPath(path)
    if p.name == "Dockerfile":
        return "Dockerfile"
    return LANGUAGE_BY_SUFFIX.get(p.suffix.lower())


def classify_document(path: str) -> str | None:
    p = PurePosixPath(path)
    lower_name = p.name.lower()
    lower_path = path.lower()
    if lower_name.startswith("readme"):
        return "README"
    if lower_name.startswith("changelog"):
        return "CHANGELOG"
    if lower_name.startswith("contributing"):
        return "OTHER"
    if "adr" in lower_name or "/adr/" in lower_path:
        return "ADR"
    if "architecture" in lower_path:
        return "ARCHITECTURE"
    if "phase" in lower_path and p.suffix.lower() in {".md", ".rst"}:
        return "PHASE_REPORT"
    if "decision" in lower_path:
        return "DECISION_REGISTER"
    if "runbook" in lower_path:
        return "RUNBOOK"
    if lower_path.startswith("docs/") or p.suffix.lower() in {".md", ".rst"}:
        return "OTHER"
    return None


def classify_test(path: str) -> str | None:
    p = PurePosixPath(path)
    lower = path.lower()
    name = p.name.lower()
    if any(part in {"tests", "test", "spec", "__tests__"} for part in p.parts):
        return "TEST_SOURCE"
    if ".test." in name or ".spec." in name:
        return "TEST_SOURCE"
    if name == "pytest.ini" or name == "pyproject.toml":
        return "TEST_CONFIG"
    if "jest" in name or "vitest" in name:
        return "TEST_CONFIG"
    if "tests/" in lower:
        return "TEST_SOURCE"
    return None


def classify_config(path: str) -> str | None:
    p = PurePosixPath(path)
    name = p.name
    lower = path.lower()
    if name in {"pyproject.toml", "package.json", "tsconfig.json", "alembic.ini", "Dockerfile", "Makefile"}:
        return name
    if name == ".env.example":
        return "ENV_EXAMPLE"
    if name.startswith("docker-compose"):
        return "CONTAINERS"
    if lower.startswith(".github/workflows/"):
        return "GITHUB_WORKFLOW"
    return None


def is_entrypoint(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in {"main.py", "app.py", "server.py", "index.js", "index.ts", "main.ts", "main.js"}


def docs_from_paths(paths: list[str]) -> list[DocumentationRecord]:
    return [DocumentationRecord(path=path, kind=kind) for path in paths if (kind := classify_document(path))]


def tests_from_paths(paths: list[str]) -> list[TestRecord]:
    return [TestRecord(path=path, kind=kind) for path in paths if (kind := classify_test(path))]


def configs_from_paths(paths: list[str]) -> list[ConfigurationRecord]:
    return [ConfigurationRecord(path=path, kind=kind) for path in paths if (kind := classify_config(path))]


def detect_technologies(paths: list[str], read_text: callable) -> list[TechnologyEvidence]:
    evidence: dict[str, set[str]] = {}

    def add(name: str, path: str) -> None:
        evidence.setdefault(name, set()).add(path)

    for path in paths:
        name = PurePosixPath(path).name
        lower = path.lower()
        if name == "pyproject.toml":
            add("Python", path)
            add("Pytest", path)
            try:
                data = tomllib.loads(read_text(path))
                deps = _flatten_dependencies(data)
                _detect_python_frameworks(path, deps, add)
            except Exception:
                pass
        elif name == "requirements.txt":
            add("Python", path)
            try:
                _detect_python_frameworks(path, read_text(path).splitlines(), add)
            except Exception:
                pass
        elif name == "package.json":
            add("Node/JS/TS", path)
            try:
                data = json.loads(read_text(path))
                deps = {
                    **data.get("dependencies", {}),
                    **data.get("devDependencies", {}),
                    **data.get("peerDependencies", {}),
                }
                _detect_node_frameworks(path, deps.keys(), add)
            except Exception:
                pass
        elif name == "tsconfig.json":
            add("TypeScript", path)
        elif name == "Cargo.toml":
            add("Rust", path)
        elif name == "go.mod":
            add("Go", path)
        elif name == "Dockerfile":
            add("Docker", path)
        elif name.startswith("docker-compose"):
            add("Containers", path)
        elif name == "alembic.ini" or lower.startswith("alembic/"):
            add("Alembic", path)

    return [
        TechnologyEvidence(name=name, status=DetectionStatus.DETECTED, evidence=sorted(paths))
        for name, paths in sorted(evidence.items())
    ]


def _flatten_dependencies(data: dict[str, Any]) -> list[str]:
    deps: list[str] = []
    project = data.get("project", {})
    deps.extend(project.get("dependencies", []) or [])
    optional = project.get("optional-dependencies", {}) or {}
    for values in optional.values():
        deps.extend(values or [])
    poetry = data.get("tool", {}).get("poetry", {})
    deps.extend((poetry.get("dependencies", {}) or {}).keys())
    deps.extend((poetry.get("group", {}).get("dev", {}).get("dependencies", {}) or {}).keys())
    return deps


def _detect_python_frameworks(path: str, deps: list[str], add: callable) -> None:
    normalized = {re.split(r"[<>=~! \[]", dep, maxsplit=1)[0].lower() for dep in deps}
    for package, technology in {
        "sqlalchemy": "SQLAlchemy",
        "alembic": "Alembic",
        "pytest": "Pytest",
        "fastapi": "FastAPI",
        "django": "Django",
        "pydantic": "Pydantic",
    }.items():
        if package in normalized:
            add(technology, path)


def _detect_node_frameworks(path: str, deps: list[str], add: callable) -> None:
    normalized = {dep.lower() for dep in deps}
    for package, technology in {
        "react": "React",
        "next": "Next.js",
        "vite": "Vite",
        "vitest": "Vitest",
        "jest": "Jest",
        "typescript": "TypeScript",
    }.items():
        if package in normalized:
            add(technology, path)


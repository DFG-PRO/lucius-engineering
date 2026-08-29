from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lucius.domain.enums import (
    AuthorityLevel,
    DetectionStatus,
    RepositoryAccessMode,
    SnapshotMode,
)


class RepositorySchema(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)


class WorkspaceContext(RepositorySchema):
    workspace_id: str
    allowed_roots: list[Path]
    repository_id: str | None = None
    access_mode: RepositoryAccessMode = RepositoryAccessMode.READ_ONLY
    authority_level: AuthorityLevel = AuthorityLevel.L0


class RepositoryIdentity(RepositorySchema):
    root: str
    canonical_remote: str | None = None
    default_branch: str | None = None


class GitState(RepositorySchema):
    root: str
    branch: str | None = None
    commit_sha: str
    detached_head: bool
    is_dirty: bool
    modified: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    renamed: list[str] = Field(default_factory=list)
    untracked: list[str] = Field(default_factory=list)
    conflicted: list[str] = Field(default_factory=list)
    remotes: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class FileRecord(RepositorySchema):
    path: str
    size_bytes: int
    file_type: str
    language: str | None = None
    content_hash: str | None = None
    git_status: str = "tracked"
    is_documentation: bool = False
    is_test: bool = False
    is_config: bool = False
    is_entrypoint: bool = False
    is_sensitive: bool = False
    is_binary: bool = False


class DocumentationRecord(RepositorySchema):
    path: str
    kind: str


class TestRecord(RepositorySchema):
    path: str
    kind: str


class ConfigurationRecord(RepositorySchema):
    path: str
    kind: str


class TechnologyEvidence(RepositorySchema):
    name: str
    status: DetectionStatus
    evidence: list[str] = Field(default_factory=list)


class RepositoryManifest(RepositorySchema):
    files: list[FileRecord]
    manifest_hash: str


class RepositoryProfile(RepositorySchema):
    technologies: list[TechnologyEvidence] = Field(default_factory=list)
    configuration: list[ConfigurationRecord] = Field(default_factory=list)


class ManifestSummary(RepositorySchema):
    file_count: int
    document_count: int
    test_count: int
    config_count: int
    manifest_hash: str


class SnapshotResult(RepositorySchema):
    snapshot_id: str | None = None
    repository_id: str | None = None
    status: str
    reused_existing_snapshot: bool = False
    mode: SnapshotMode
    git_state: GitState
    manifest_summary: ManifestSummary
    repository_profile: RepositoryProfile
    documentation_map: list[DocumentationRecord]
    test_map: list[TestRecord]
    configuration_map: list[ConfigurationRecord]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    manifest: RepositoryManifest


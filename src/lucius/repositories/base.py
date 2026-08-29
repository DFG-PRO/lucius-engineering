from __future__ import annotations

from abc import ABC, abstractmethod

from lucius.domain.enums import SnapshotMode
from lucius.repositories.schemas import GitState, RepositoryIdentity, SnapshotResult


class RepositoryAdapter(ABC):
    @abstractmethod
    def validate(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_identity(self) -> RepositoryIdentity:
        raise NotImplementedError

    @abstractmethod
    def get_git_state(self) -> GitState:
        raise NotImplementedError

    @abstractmethod
    def list_files(self, *, include_untracked: bool = True) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def read_file(self, relative_path: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def discover_documents(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def discover_tests(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def discover_technologies(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def build_snapshot(self, mode: SnapshotMode = SnapshotMode.STANDARD) -> SnapshotResult:
        raise NotImplementedError


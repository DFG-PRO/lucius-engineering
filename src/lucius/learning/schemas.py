from __future__ import annotations

from pydantic import BaseModel, Field

from lucius.persistence.orm import LearningCandidateORM, MemoryEntryORM


class TaskExperienceResult(BaseModel):
    memory: MemoryEntryORM
    learning_candidates: list[LearningCandidateORM] = Field(default_factory=list)
    outcome: str

    model_config = {"arbitrary_types_allowed": True}


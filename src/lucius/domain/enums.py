from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class RepositoryAdapterType(StrEnum):
    LOCAL_GIT = "LOCAL_GIT"


class RepositoryAccessMode(StrEnum):
    READ_ONLY = "READ_ONLY"


class AuthorityLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class TaskComplexity(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"


class Environment(StrEnum):
    SANDBOX = "SANDBOX"
    DEVELOPMENT = "DEVELOPMENT"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"


class KnowledgeScope(StrEnum):
    GLOBAL = "GLOBAL"
    DFG = "DFG"
    DOMAIN = "DOMAIN"
    PROJECT = "PROJECT"
    CLIENT = "CLIENT"
    SESSION = "SESSION"


class ValidationStatus(StrEnum):
    OBSERVED = "OBSERVED"
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    CONTRADICTED = "CONTRADICTED"
    SUPERSEDED = "SUPERSEDED"
    DEPRECATED = "DEPRECATED"


class DetectionStatus(StrEnum):
    DETECTED = "DETECTED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class SnapshotMode(StrEnum):
    FAST = "FAST"
    STANDARD = "STANDARD"
    DEEP = "DEEP"


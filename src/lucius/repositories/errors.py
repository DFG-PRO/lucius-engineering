from enum import StrEnum


class RepositoryErrorCode(StrEnum):
    REPOSITORY_NOT_FOUND = "REPOSITORY_NOT_FOUND"
    NOT_A_GIT_REPOSITORY = "NOT_A_GIT_REPOSITORY"
    REPOSITORY_OUTSIDE_WORKSPACE = "REPOSITORY_OUTSIDE_WORKSPACE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNSAFE_PATH = "UNSAFE_PATH"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    BINARY_FILE = "BINARY_FILE"
    GIT_COMMAND_FAILED = "GIT_COMMAND_FAILED"
    UNSUPPORTED_REPOSITORY_STATE = "UNSUPPORTED_REPOSITORY_STATE"


class LuciusRepositoryError(RuntimeError):
    def __init__(self, code: RepositoryErrorCode | str, message: str):
        self.code = RepositoryErrorCode(code)
        super().__init__(f"{self.code.value}: {message}")


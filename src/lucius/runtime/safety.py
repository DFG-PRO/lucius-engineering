from __future__ import annotations

import re
from typing import Any


class ExecutionSafetyError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


FORBIDDEN_COMMAND_PATTERNS = [
    r"\brm\s+-[rf]+",
    r"\brm\b",
    r"\bgit\s+clean\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+restore\b",
    r"\bgit\s+push\s+.*--force",
    r"\bgit\s+worktree\s+remove\b",
    r"\bgit\s+branch\s+-[dD]\b",
    r"\bsudo\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bpip\s+install\b",
    r"\bnpm\s+install\b",
    r"\bcurl\b.*\|\s*sh",
]


def assert_unattended_command_authorized(command: str | list[str]) -> None:
    """Deterministic deny gate for Lucius-owned unattended execution.

    IMPORTANT ARCHITECTURAL BOUNDARY:
    - LUCIUS RUNTIME ENFORCEMENT: Enforces strict command deny policies within
      Lucius-managed background tasks, verification loops, and autonomous dispatch.
    - EXTERNAL CODING AGENT BEHAVIOR: Lucius cannot enforce process boundaries on
      external coding assistants (e.g., Antigravity/Gemini IDE CLI) running directly
      in the host shell environment outside the Lucius execution runtime.
    """
    cmd_str = " ".join(command) if isinstance(command, list) else str(command)

    for pattern in FORBIDDEN_COMMAND_PATTERNS:
        if re.search(pattern, cmd_str, re.IGNORECASE):
            raise ExecutionSafetyError(
                "UNATTENDED_COMMAND_DENIED",
                f"Command denied by Lucius Unattended Safety Gate: '{cmd_str}' matched pattern '{pattern}'",
            )

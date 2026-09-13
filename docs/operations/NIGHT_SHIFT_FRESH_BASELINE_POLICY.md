# Night Shift Fresh Baseline Policy

Each unattended canonical mutation must use a fresh isolated worktree created from the current canonical HEAD for that task. After a successful L2 local commit advances canonical HEAD, later work must refresh its baseline rather than reuse a worktree frozen against the prior commit.

Canonical HEAD drift, candidate baseline drift, unexpected changed paths, or protected-state drift are fail-closed conditions. Night Shift must not rewrite an old freeze merely to fit a newer canonical commit.

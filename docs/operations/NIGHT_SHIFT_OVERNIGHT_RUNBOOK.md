# Night Shift v0 Overnight Runbook

Before an unattended run, verify the canonical repository has no preexisting tracked or staged modifications, capture protected untracked state, confirm the qualified local provider is available, and freeze exact task scope and deterministic acceptance checks.

Run bounded T0/T1 LOW-risk tasks through a single dispatcher and worker. Use a fresh isolated worktree and current canonical baseline for each canonical mutation. Stop on escalation, manual-reconciliation requirements, canonical drift, scope violation, deterministic-verification failure, protected-state drift, or unauthorized remote action.

After each successful task, require deterministic verification, controlled L1 integration, post-integration verification, and explicitly authorized L2 local commit promotion before advancing to the next fresh baseline. Overnight duration does not expand authority.

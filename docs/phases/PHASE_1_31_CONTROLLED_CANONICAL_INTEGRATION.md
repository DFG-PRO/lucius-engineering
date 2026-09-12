# Phase 1.31 Controlled Canonical Integration

Phase 1.31A adds a deterministic canonical integration step after autonomous
runtime execution has already completed in an isolated workspace.

The runtime boundary remains unchanged: local model/runtime authority stops at
`COMPLETED_PENDING_INTEGRATION`. Integration is a separate Lucius service, not a
runtime provider, router fallback, model generation, merge, push, deployment, or
commit operation.

## Authority Boundary

The integration path is L1-only. It may apply frozen, deterministic file
contents to a canonical working tree, but it must not create a commit. Commit
creation remains an L2 authority boundary for a future phase. Production actions
remain L3.

`LocalGitRepositoryAdapter` remains read-only. Canonical integration requires an
explicit `RepositoryAccessMode.CANONICAL_INTEGRATION` repository registration
and an explicit L1 integration request.

## Required Preconditions

Integration fails closed unless all required evidence and state are explicit:

- workflow state is exactly `COMPLETED_PENDING_INTEGRATION`;
- workflow, project, task, plan, freeze, repository, and attachment references
  are internally consistent;
- workflow and plan authority do not exceed L1;
- repository access mode is explicitly `CANONICAL_INTEGRATION`;
- the request supplies the exact frozen baseline commit;
- the frozen PlanFreeze commit matches the request baseline;
- canonical repository `HEAD` equals the frozen baseline;
- canonical repository is strictly clean before any write;
- candidate workspace is the workflow's isolated workspace, not canonical;
- candidate workspace `HEAD` equals the frozen baseline;
- candidate Git changes are within frozen `affected_files`;
- candidate workspace passes frozen deterministic acceptance before any canonical
  write;
- frozen `deterministic_acceptance_checks` are present and valid.

Lucius never infers or substitutes a baseline commit. Missing baseline evidence
blocks integration.

## Apply Semantics

The first implementation intentionally avoids arbitrary patch interpretation.
It uses frozen `exact_file_content` checks as an acceptance oracle, then captures
the actual UTF-8 contents of those checked files from the candidate workspace.
The captured candidate payload is the sole source for canonical writes;
`expected_text` is never used as substitute integration content. The captured
payload must still match the frozen acceptance oracle before any canonical write.
Lucius validates every path and content value:

- no absolute paths;
- no traversal or `.` path segments;
- no path outside the repository;
- no duplicate paths;
- no writes outside frozen `affected_files`.

The canonical write path does not re-read the candidate after capture.

After applying, Lucius inspects Git-observed canonical changes. Actual changed
paths must remain within the frozen authorized set, and every intended write
must be visible to Git. Deterministic acceptance checks then run against the
canonical workspace and cannot be overridden by model/provider output.

On success, the workflow moves to `CLOSED`. The canonical working tree remains
dirty with the verified candidate contents. No commit, push, merge,
cherry-pick, deployment, or remote integration is performed.

## Rollback

If a failure occurs after the first canonical write, Lucius restores the
canonical repository to the exact frozen baseline using the controlled
integration rollback path. Rollback is allowed only after the clean canonical
precondition has passed.

Rollback verifies:

- `HEAD` equals the expected baseline;
- the repository is clean;
- integration-created untracked state has been removed.

Rollback failure is itself fail-closed and auditable. A failed integration leaves
the workflow in `COMPLETED_PENDING_INTEGRATION`.

## Audit Evidence

The service records deterministic audit events for:

- integration request;
- preflight success/failure;
- baseline and canonical head;
- authorized paths and candidate changed paths;
- actual canonical changed paths;
- deterministic acceptance result;
- rollback attempt/result when applicable;
- successful workflow closure.

## Remaining Boundary

Phase 1.31A intentionally stops before the commit phase. A later phase may add
L2 commit creation and review/promotion semantics, but this phase only applies a
verified candidate to a clean canonical working tree.

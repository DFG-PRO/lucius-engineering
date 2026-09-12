# Phase 1.31B Controlled Local Commit Authority

Phase 1.31B adds a separate L2 promotion step after Phase 1.31A controlled
canonical integration. It converts an already verified dirty canonical working
tree into exactly one verified local Git commit.

This service does not run model inference and does not push, merge, deploy,
rebase, amend, or interact with remotes.

## Authority Boundary

Phase 1.31A remains L1 and may only apply verified candidate contents to the
canonical working tree. Phase 1.31B is a separate controlled commit capability
under explicit L2 authority.

The service requires:

- request authority exactly `L2`;
- a closed workflow linked to a frozen plan;
- task and task contract authority sufficient for L2;
- `CREATE_COMMIT` explicitly present in the task contract allowed actions.

`LocalGitRepositoryAdapter` remains read-only. The commit service uses narrow
Git mutation helpers and does not make repository inspection adapters writable.

## Preconditions

Controlled commit fails closed before staging unless:

- workflow state is exactly `CLOSED`;
- workflow, project, task, contract, plan, freeze, repository, and attachment
  references are internally consistent;
- repository access mode is `CANONICAL_INTEGRATION`;
- request canonical path exactly matches the registered repository path;
- frozen baseline exists and equals the request baseline;
- canonical `HEAD` equals the frozen baseline;
- canonical working tree is dirty;
- there are no pre-existing staged changes;
- all changed paths are within frozen `affected_files`;
- changed paths are covered by frozen `exact_file_content` deterministic
  acceptance checks;
- deterministic acceptance passes against the canonical working tree
  immediately before staging.

Missing or ambiguous evidence does not infer commit authority.

## Commit Manifest

Before staging, Lucius builds an in-memory deterministic manifest containing:

- workflow id;
- freeze id;
- baseline commit SHA;
- authorized paths;
- actual changed paths;
- SHA-256 content hash per changed path;
- deterministic acceptance evidence;
- deterministic commit message.

The manifest hash is persisted after a successful commit. The frozen
`expected_text` values remain acceptance oracles; actual canonical bytes and
their hashes define what enters the commit.

## Staging

The service never uses `git add .` or `git add -A`. It stages only the exact
manifest paths.

Before commit creation it verifies:

- staged paths exactly equal the manifest changed paths;
- staged content hashes equal the manifest hashes.

If staging or pre-commit verification fails, the service unstages only the paths
it attempted to stage and preserves the L1 working-tree contents. It does not
use destructive working-tree reset for normal pre-commit failures.

## Commit Message

The default commit message is deterministic:

`lucius: complete <task_id>`

Caller-supplied messages, when used, must be single-line, trimmed, concise, and
free of control characters. No Git options are accepted through the message.

## Post-Commit Verification

After commit creation Lucius verifies:

- new `HEAD` differs from the baseline;
- new commit has exactly one parent and that parent is the baseline;
- committed path set exactly equals the manifest path set;
- committed contents match manifest hashes;
- no unauthorized path entered the commit;
- deterministic acceptance still passes;
- canonical working tree is clean;
- canonical `HEAD` equals the resulting commit.

On success, Lucius persists a `ControlledCommit` evidence record containing the
workflow, repository, task, freeze, baseline, parent, resulting commit, path
sets, content hashes, manifest hash, commit message, deterministic acceptance
evidence, authority, actor, status, and timestamp.

## Duplicate Protection

A successful controlled commit record for the same workflow and freeze prevents
another commit. The service returns an explicit already-committed result.

If Git `HEAD` appears to contain a prior controlled commit from the frozen
baseline but no persistent record exists, the service fails closed with manual
reconciliation required rather than creating another commit.

## Failure Semantics

Before commit creation:

- no commit is created;
- baseline `HEAD` remains unchanged;
- service-created staging is removed;
- verified working-tree changes remain intact.

After commit creation:

- Lucius does not reset, amend, rewrite, delete, or push the commit;
- failures become `POST_COMMIT_VERIFICATION_FAILED`;
- the resulting `HEAD` is preserved;
- audit evidence records the baseline, observed head, intended commit, and
  failure reason;
- no success evidence record is written.

## Known Boundaries

Phase 1.31B is local commit authority only. It does not add push, merge, pull
request, deployment, release, remote promotion, or production authority.

Empirical validation against a bounded local pilot remains required before this
phase is declared closed.

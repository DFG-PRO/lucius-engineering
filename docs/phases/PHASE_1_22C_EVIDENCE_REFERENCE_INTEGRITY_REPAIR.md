# Phase 1.22C Evidence Reference Integrity Repair

Phase 1.22C is a narrow provenance repair for the Phase 1.22 and Phase 1.22B closure chain. It does not start a broader operational phase, does not add multi-worker concurrency, and does not authorize push, merge, or deploy behavior.

## Baseline

- Starting Lucius HEAD: `11f9117e02e8cd72d3a9bd8ef70979c4025b00e2`.
- Starting Lucius status: `## main...origin/main [ahead 22]`, tracked tree clean.
- Official Darwin HEAD: `3207aee087fa9018ee9ff2d526fe7a97443f6e21`.
- Canonical artifact store: `data/lucius-pilots.sqlite`.
- Independent audit that triggered repair: `LRUBRIC_000025`.
- PRE benchmark: `LBENCH_000038`, PASS, 8/8, aggregate `100.0`, hard gate PASS, target_dirty `false`.

## Reproduced Defect

Before repair, provenance validation was shape-only:

- `VERIFIED` claim with `evidence_ids=["LEVID_DOES_NOT_EXIST"]` was not flagged as unsupported.
- `VERIFIED` claim with `evidence_ids=["not-an-id"]` was not flagged as unsupported.
- Phase 1.22 operational gate claims using nonexistent or malformed evidence references could pass.

Root cause: closure-critical helpers accepted non-empty evidence fields and probe/invariant strings without resolving the referenced artifact in the canonical store or checking artifact type, context, staleness, result, or payload support.

## Evidence Contract

Closure-critical evidence references now require:

- valid Lucius artifact ID format;
- existence in the artifact store or persisted workflow queue state;
- an allowed artifact class for the claim;
- non-superseded/current evidence when current closure proof is required;
- matching context fields where supplied;
- payload support for claim name, expected invariant, and test/probe identifier;
- result consistency between the claim and referenced artifact.

Structured failure reasons include `MALFORMED_EVIDENCE_ID`, `EVIDENCE_NOT_FOUND`, `EVIDENCE_TYPE_NOT_ALLOWED`, `EVIDENCE_CONTEXT_MISMATCH`, `EVIDENCE_SUPERSEDED`, `EVIDENCE_RESULT_MISMATCH`, and `EVIDENCE_PAYLOAD_INSUFFICIENT`.

Historical evidence remains valid when explicitly used as historical baseline evidence. Superseded evidence is rejected for current closure proof when `require_current=True`.

## Scope

The repair touched provenance validation, deterministic evaluation, release-gate evidence checks, adversarial tests, and phase documentation. Queue scheduling and completion behavior were not redesigned.

## Documentation Chronology

The primary Phase 1.22 report is now present in canonical Lucius docs, with the isolated Project B branch head corrected to `e4ebaf0d5919ab438ccd2395421e083c5b1e6224`. The report preserves the original Phase 1.22 closure failure and points to Phase 1.22B and Phase 1.22C as subsequent additive repairs.

## Verification

Phase 1.22C verification covers:

- malformed, empty, nonexistent, unrelated, wrong-type, wrong-context, wrong-result, wrong-probe, and superseded evidence references;
- valid supporting evidence references;
- verified-claim semantic validation;
- Phase 1.22 operational gate rejection of fake evidence;
- Phase 1.22 operational gate acceptance of valid supporting evidence;
- read-only validation against a file-backed artifact store;
- Phase 1.22B completion behavior;
- Phase 1.21 queue safety;
- full Lucius suite;
- Darwin and Lucius isolated target branch test suites.

Final closure artifacts and the POST benchmark are recorded in the canonical artifact store and completion report.

## Limitations

Semantic validation is intentionally bounded to closure-critical provenance. It does not perform natural-language proof, and it relies on claim names, probe identifiers, expected invariants, result markers, artifact classes, and explicit context fields rather than inferring arbitrary support from prose.

# Phase 1.24H3 Canonical Closure Format Repair

Phase 1.24H3 resolves `LPLEARN_000056`, which was identified during
`LPILOT_000034`.

## Scope

This repair is Lucius-only. It does not mutate external target repositories and
does not start another operational pilot.

## Repairs

- Documentation requirements now normalize legacy `path` / `target_path`
  aliases into the canonical `target` and `proposed_path` contract before
  planning persistence, plan freeze, and evaluation.
- Exact-path documentation requirements remain exact. Flexible canonical-target
  requirements continue to allow architecture-correct documentation paths.
- Operational release-gate evidence now has a canonical producer:
  `OperationalEvidenceService`.
- Canonical operational evidence claims include `result=PASS`,
  `expected_invariant`, `test_probe_id`, and linked evidence references in the
  shape consumed by the release gate.

## Verification

The Phase 1.24H regression band includes adversarial coverage for:

- `path`-only exact documentation requirements freezing to canonical payloads;
- model-level documentation alias normalization;
- release-gate-compatible operational evidence emitted by the canonical
  producer.

Final closure evidence is recorded in the canonical pilot artifact store.

# Phase 1.8: Model Gateway and Capability Routing

Lucius treats models as replaceable execution resources. Lucius identity remains
memory, evidence, policies, tools, evaluations, and accumulated engineering
knowledge.

## Architecture

Higher-level modules depend on `ModelGateway`, not provider SDKs.

Flow:

Lucius -> ModelGateway -> CapabilityRouter -> ProviderAdapter -> Model

The gateway supports provider/profile registration, deterministic capability
routing, explicit fallback chains, structured response validation, execution
metrics, audit events, and model provenance.

## Epistemology

`ModelResponse` is model inference. It is not `EvidenceReference`, validated
memory, or global knowledge.

Future Lucius components may use model inference as input, but promotion into
evidence or memory must pass through the relevant evidence, learning, and
validation layers.

## Security

Provider secrets are never persisted. Provider/profile metadata is sanitized
before persistence and audit metadata avoids raw prompt or response content.

Execution records store provenance, routing decisions, usage, cost, latency, and
failure categories rather than sensitive prompts.

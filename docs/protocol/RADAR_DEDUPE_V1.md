# Radar V1 duplicate-suppression contract

Status: CURRENT / BINDING V1 SOURCE CONTRACT

Authority: binding amendment A1 in `docs/superpowers/specs/2026-09-05-radar-central-reconciliation-self-review-amendments.md` and Phase 3 of the Radar Station instructions.

## Contract

Radar V1 uses the middleware deterministic payload hash exactly for duplicate suppression.

- `RadarEnvelope.idempotency_key` is the canonical V1 duplicate key.
- The key is SHA-256 of the canonicalized payload, as produced by the middleware/envelope layer.
- `RadarRuntime` must pass `RadarEnvelope.idempotency_key` directly to `DedupeWindow`; it must not hash sender, domain, intent, audience, route, node, or other context into a second key.
- Duplicate observations of the same key at an elapsed time of 500 ms or less are suppressed.
- The same key is admissible again when elapsed time is greater than 500 ms.
- Route context may be recorded as diagnostic or telemetry metadata, but it does not change V1 duplicate equivalence.
- A future route-scoped equivalence rule requires an explicit versioned protocol change.

## Superseded historical wording

The following documents preserve earlier design/checkpoint state and contain route-scoped dedupe wording that is no longer the current V1 contract:

- `docs/superpowers/specs/2026-09-05-reviewer-chat-bus-trust-runtime-architecture.md`
- `docs/checkpoints/RADAR_RESTORE_2026-09-05.md`

Those historical statements are superseded specifically for duplicate-equivalence semantics by binding amendment A1 and this current contract. They remain useful as historical evidence and are not rewritten retroactively.

## Accounting boundary

Duplicate suppression is an admission drop and increments `dropped` once. Queue admission alone does not increment `routed`. `routed` increments once only after dispatch resolves at least one authorized live node, regardless of recipient fanout. A dispatch with no route increments `dropped` and does not increment `routed`.

# Radar projection journal V1

Status: source contract; provider migrations not applied.

Radar reconciles each ACTIVE writer lane against a durable provider cursor without treating a writer branch or caller-supplied head as authority.

The journal has three durable state surfaces: lane state (`identity_id`, `branch`, `projected_head_sha`, trusted control/topology cut), one claimed reconciliation batch, and per-file projection results.

Authority rules:

- lane state must be seeded explicitly; claiming work cannot create or invent a cursor;
- an uninitialized live bootstrap is represented by an explicitly seeded `NULL` projected cursor;
- control/topology cut state is lane-scoped and must match the batch at claim, file projection, and finalization;
- one live claim exists per lane; an expired claim may be replaced and its old token is permanently fenced;
- lock order for claim-sensitive provider work is normalized as lane -> batch -> file, preventing the former claim/finalize lock inversion;
- a claimed file may mutate `radar.messages` only through `radar_projection_project_file_v1`, which validates the active claim token/lease, expected cursor, control/topology cut, exact claimed path, sender identity, source path, and target branch head before message projection;
- message projection and the file's terminal journal state are one SQL transaction, so a stale claim, unclaimed path, binding mismatch, unknown projection result, or journal write failure cannot leave a successful message mutation without its matching terminal file state;
- direct service-role execution of the legacy standalone message-projection RPC and standalone file-result RPC is revoked after the atomic-file migration; the service role receives only the claim-bound atomic projection bridge for this effect;
- a file result is successful only as `PROJECTED` or verified `IDEMPOTENT`; `FAILED`, `CONFLICT`, and `PENDING` never permit cursor advancement;
- finalization is one provider transaction that validates claim token/lease, expected base cursor, control/topology cut, batch target, and all file states before atomically marking the batch COMPLETE and advancing the cursor;
- replay of a successful finalization is idempotent only when the durable lane cursor still equals the completed batch target;
- `service_role` may read journal state and execute the narrow public journal RPC surface but has no direct table-write grant, so fenced state transitions are not bypassable through ordinary table DML.

Trusted Git derivation is separate from the journal. For a current ACTIVE lane, the reconciler obtains the branch from canonical topology, the activation anchor from cutover/onboarding evidence, the observed head and ancestry from the trusted Git object database, and the projected cursor from the journal. The resulting dispositions are `NOOP`, `FORWARD`, `STALE_EVENT`, or `DIVERGED_HISTORY`; divergence fails closed.

Writer branch files are inert Git data. The trusted reconciler does not import or execute code from a writer branch.

Provider deployment, migration application, workflow activation, and live cursor bootstrap remain separately authorized effects.

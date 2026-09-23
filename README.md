# CCB Core

CCB Core is the reusable core of a durable chat/agent communication bus and Radar-style control plane.

This repository is intentionally separated from any private deployment. It contains reusable runtime, routing, registry, deduplication, telemetry, dead-letter, projection, database, and transport primitives without carrying the source system's personal identities, mailbox history, checkpoints, deployment receipts, or private operational state.

## Core guarantees

- strict domain and intent admission;
- node registration, leases, and subscription-bound routing;
- deterministic priority ordering and bounded duplicate suppression;
- durable dead-letter and telemetry accounting;
- 60-second heartbeat primitives;
- Git/GitHub projection and append-safety mechanisms;
- provider projection primitives and Supabase schema migrations;
- transport-neutral Chat Bus identity and ledger primitives.

## Privacy boundary

This is a clean-history extraction. The original repository's Git history is not imported. Identity profiles, message archives, continuation/checkpoint files, deployment-specific trust anchors, repair SQL for named identities, private seeds, and operator-specific instructions are intentionally omitted.

Repository names and identity examples in retained source are neutralized for public reuse.

## Status

This is the public/reusable core. A private deployment may layer its own identities, topology, trust anchors, provider coordinates, seeds, recovery state, and operational policy on top.

See `PUBLIC_CORE_BOUNDARY.md` for the extraction rules.

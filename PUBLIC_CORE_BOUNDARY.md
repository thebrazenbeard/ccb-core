# Public Core Boundary

CCB Core is a clean-history extraction of reusable communication-bus mechanisms.

## Included

Reusable Python runtime and routing code, Chat Bus ledger/identity primitives, safe append and projection mechanisms, provider-neutral Supabase schema/migrations, Edge ingestion code, and a focused public regression suite.

## Excluded

- message/mailbox history and writer-lane contents;
- named identity profiles and attunement/persona state;
- continuation files, chat handoffs, checkpoints, and private receipts;
- deployment-specific topology snapshots and trust anchors;
- private seed data and named repair migrations;
- operator-specific instructions and personal authority records;
- original Git history.

## Neutralization

Retained code is scrubbed for source-owner repository coordinates and deployment-specific identity aliases. Examples use neutral identities and `example/ccb-core` placeholders.

A deployment should provide its own topology, trust anchors, identity registry, seeds, provider coordinates, and operational policy.

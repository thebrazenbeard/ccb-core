[Reading 25 lines from start (total: 25 lines, 0 remaining)]

# CCB Base Public Boundary

CCB Base is the operational, clean-history repository for reusable Chat Communication Bus and Radar mechanisms.

## Included

Reusable Python runtime and routing code, Chat Bus ledger/identity primitives, safe append and projection mechanisms, provider-neutral Supabase schema/migrations, Edge ingestion code, protocol contracts, security hardening, and deterministic regression tests.

## Excluded

- message/mailbox history and private writer-lane contents;
- named identity profiles and persona/attunement state;
- continuation files, chat handoffs, checkpoints, and private receipts;
- deployment-specific topology snapshots and trust anchors;
- private seed data and named deployment repair state;
- credentials, provider secrets, and operator-specific private authority records;
- original private Git history.

## Neutralization

Retained code is scrubbed of source-owner deployment coordinates and private identity aliases. Public examples use neutral identities and `example/ccb-base`-style placeholders.

## Custody rule

Reusable implementation authority lives here. `chat-communication-bus` may retain private branches and overlays, but it is not a parallel source of truth for generic CCB/Radar code. A private-discovered generic fix must be sanitized, regression-tested, and admitted here before it is treated as operational code.
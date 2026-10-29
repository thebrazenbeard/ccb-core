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

Reusable implementation authority lives here. The `chat-communication-bus` repository is now **public** and must contain only sanitized coordination, public-safe contracts and release evidence. It must not hold private branches, writer-lane data, identity records, topology, receipts, credentials or private deployment overlays in its Git history. Authorized deployments keep that state in separately access-controlled storage outside the public repository. A generic fix discovered through public coordination must be sanitized, regression-tested and admitted here before it becomes operational code. Historical public commits require separate exposure remediation; changing this document does not erase them.

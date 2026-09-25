[Reading 25 lines from start (total: 25 lines, 0 remaining)]

# Repository Roles

## Canonical operational repository

`thebrazenbeard/ccb-core` is CCB Base and is authoritative for reusable CCB/Radar software: Python runtime and libraries, public-safe protocol/schema contracts, database migrations, provider-neutral Edge/runtime code, deterministic tests/CI, and reusable security, admission, routing, reconciliation, projection, and telemetry mechanisms.

`main` is the operational integration branch unless a future repository contract explicitly supersedes it.

## Private branch repository

`thebrazenbeard/chat-communication-bus` is a private branch vault and deployment overlay. It may retain private writer lanes, private topology, identities, checkpoints, receipts, recovery evidence, deployment coordinates, and historical branches. Those objects are not reusable-code authority.

## Change flow

1. Generic defect or mechanism: reproduce and fix in CCB Base.
2. Add deterministic regression coverage in CCB Base.
3. Qualify the exact CCB Base source cut.
4. Private deployments consume or pin that qualified revision and add only private overlay state.
5. A private branch may discover a fix, but the fix is not canonical until sanitized and admitted to CCB Base.

Do not maintain divergent copies of reusable runtime logic in both repositories.

## Authority boundary

This split changes source custody, not deployment authority. Source commits do not by themselves authorize provider migrations, deployment, credential changes, route activation, branch rewriting, or deletion of private history.
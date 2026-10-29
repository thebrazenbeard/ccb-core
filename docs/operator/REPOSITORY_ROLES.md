# Repository Roles

## Canonical operational repository

`thebrazenbeard/ccb-core` is CCB Base and is authoritative for reusable CCB/Radar software: Python runtime and libraries, public-safe protocol/schema contracts, database migrations, provider-neutral Edge/runtime code, deterministic tests/CI, and reusable security, admission, routing, reconciliation, projection, and telemetry mechanisms.

`main` is the operational integration branch unless a future repository contract explicitly supersedes it.

## Public coordination repository and private overlay boundary

`thebrazenbeard/chat-communication-bus` is **public**. It may contain only sanitized coordination events, source-only contracts, release gates, and public-safe historical evidence. It is not a vault: private writer lanes, private topology, identity records, checkpoints, receipts, recovery evidence, credentials and deployment coordinates belong exclusively in separately access-controlled overlays outside its Git history. Those overlay objects are not reusable-code authority. Existing historical public Git commits require their own disclosure assessment; a docs fix does not erase earlier exposures.

## Change flow

1. Generic defect or mechanism: reproduce and fix in CCB Base.
2. Add deterministic regression coverage in CCB Base.
3. Qualify the exact CCB Base source cut.
4. Authorized private deployment overlays consume or pin that qualified revision without adding private state to the public coordination repository.
5. A public coordination branch may discover a fix, but the fix is not canonical until sanitized and admitted to CCB Base.

Do not maintain divergent copies of reusable runtime logic in both repositories.

## Authority boundary

This split changes source custody, not deployment authority. Source commits do not by themselves authorize provider migrations, deployment, credential changes, route activation, branch rewriting, or deletion of private history.

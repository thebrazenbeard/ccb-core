# SQL Connectome → CCB Base shadow identity comparison V1

Status: **SOURCE-ONLY, NON-AUTHORITATIVE**. This adapter neither installs a
SQL Connectome client nor selects a database. It is a narrow comparator for a
future operator-qualified shadow read path. A reader is injected explicitly;
the application never infers one from environment variables or switches CCB
projection, dispatcher, or canonical Git routing to that reader.

## Covered seam

`radar.sql_connectome_shadow.compare_identity_shadow` accepts:

1. separately obtained expected `identity_id`/`display_name` pairs from an
   exact Git-controlled CCB state cut;
2. an operator-supplied callable with a SQL Connectome candidate *read-only*
   SQL session that returns `radar.identities` rows;
3. exact 40-hex source commit labels for reference and candidate software.

It executes a single constant SELECT with bound `LIMIT N+1`, normalizes
identity IDs like CCB's current projection builder, rejects duplicate IDs, and
returns SHA-256 digests for each validated comparison. Unexpected/missing rows
yield `MISMATCH`; unavailable/malformed rows yield `UNKNOWN`. A byte-for-byte
logical match yields only `MATCH_UNQUALIFIED`. Commit labels and the injected
reader are **caller assertions**, not source authentication or physical
database attestation. Extra data in a response such as `canonical=true` is
ignored as an authority claim.

No successful report here authorizes cutting CCB from Supabase. The comparator
does **not** establish the endpoint, TLS peer, database physical identity,
privileged function safety, object grants, JWT/RLS tenant isolation, generated
application API, Realtime/Presence and reconnect replay, migration fidelity,
cross-schema denial, atomic projection/reconciliation, backups, or recovery.
Those require separate negative and live PostgreSQL/provider tests with an
explicit operator-selected database and least-privilege role. SQL Connectome's
current public REST/MCP caller-supplied SQL is disabled by default; this module
does not enable it or work around its policy.

## Testing

```bash
PYTHONPATH=src python -m unittest discover -s tests/radar -p test_sql_connectome_shadow.py -v
```

To connect a real private reader in a later qualified change, require a pinned
CCB source cut, SQL Connectome build, deployment identity, read-only role,
repeatable-read snapshot, explicit route opt-in, denied cross-tenant/unauthorized
reads, verified RLS and Realtime capabilities, and rollback evidence before any
consumer promotion. Until then, CCB's currently selected provider and Git
canonical authority remain unchanged.

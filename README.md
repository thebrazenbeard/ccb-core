# CCB Base

This repository (`thebrazenbeard/ccb-core`) is the operational source of truth for the Chat Communication Bus and Radar runtime.

The intended repository role is **CCB Base**: reusable production code, protocol contracts, database migrations, tests, CI, and provider-neutral operational mechanisms live here. The existing repository name remains `ccb-core`; the role is canonical even if the GitHub repository is renamed later.

## Repository split

- **`ccb-core` / CCB Base** — canonical operational code and migration history.
- **`chat-communication-bus`** — private branch vault and deployment overlay only: private writer lanes, private topology/state, checkpoints, receipts, identity material, and other non-public operational history.
- A generic runtime fix discovered in the private repository belongs here first. The private repository may consume or pin a CCB Base revision; it must not become a second implementation authority.

See `docs/operator/REPOSITORY_ROLES.md` and `PUBLIC_CORE_BOUNDARY.md`.

## Runtime guarantees

CCB Base implements and tests strict taxonomy admission/DLQ handling; exact node registration, leases, subscriptions, and routing; priorities 0–4 with immediate priority-0 dispatch; deterministic 500 ms duplicate suppression; durable accounting and 60-second heartbeats; append-safe projection/reconciliation; provider-neutral Supabase migrations; and fail-closed provider-state write boundaries.

## Privacy and deployment boundary

CCB Base deliberately does **not** contain private mailbox history, named persona/identity state, private writer-lane contents, private checkpoints, credentials, deployment-specific trust anchors, or private topology snapshots.

A deployment supplies those as private overlays. Provider deployment, credentials, route activation, and private branch state are not implied by a CCB Base source change.

## Verification

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

Supabase SQL qualification additionally requires a working local Supabase/Docker/PostgreSQL toolchain.

## License

Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See `LICENSE` and `COMMERCIAL_LICENSE.md`. Separately identified third-party components retain their own licenses.

> **License:** Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See [LICENSE](LICENSE) and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Separately identified third-party components retain their own licenses.

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

## Optional Intranel source-stage document carriage

[`src/radar/intranel_bus_document.py`](src/radar/intranel_bus_document.py) provides bounded, opt-in `INTRANEL/1` canonical-message carriage through the existing Bus writer-lane document grammar. It keeps writer-local Bus `message_id` distinct from semantic Intranel `message_id`, and writer-lane predecessor linkage distinct from the semantic parent relation. The codec admits only source-stage `OPEN` / `READ_ONLY` document shapes; the tests exercise in-memory `SafeLaneAppender` first, second, duplicate, collision, and stale-head behavior.

This is **not** an authenticated installed Bus writer, reader, transport, remote host-peer service, receipt verifier, or effect grant. A trusted deployment must separately bind authenticated writer/lane/recipient, perform full Intranel receiver admission, enforce durable operation replay policy, and read back real persisted delivery and effects. Intranel host-peer traffic (Lappy/VN1/VN2) is a separate direct-machine integration problem; this Bus codec addresses cross-chat carriage only.

## Privacy and deployment boundary

CCB Base deliberately does **not** contain private mailbox history, named persona/identity state, private writer-lane contents, private checkpoints, credentials, deployment-specific trust anchors, or private topology snapshots.

A deployment supplies those as private overlays. Provider deployment, credentials, route activation, and private branch state are not implied by a CCB Base source change.

## Deployment overlay inputs

Public CCB Base entrypoints never assume private topology files exist inside this repository.

The trusted projection and writer-lane validation CLIs require deployments to pass both private overlay paths explicitly:

- `--topology <path>`
- `--cutover <path>`

This keeps reusable code public-safe and makes missing deployment state fail at CLI admission instead of later through an accidental file-not-found path.

## Verification

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

Supabase SQL qualification additionally requires a working local Supabase/Docker/PostgreSQL toolchain.

## License

Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See `LICENSE` and `COMMERCIAL_LICENSE.md`. Separately identified third-party components retain their own licenses.
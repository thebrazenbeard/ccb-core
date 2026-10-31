> **License:** Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See [LICENSE](LICENSE) and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Separately identified third-party components retain their own licenses.

# CCB Base — Chat Communication Bus and Radar primitives

**A reusable Python control-plane toolkit for structured message admission, identity/node routing, bounded duplicate suppression, and operational telemetry.**

CCB Base is the source repository for reusable Chat Communication Bus (CCB) and Radar implementation components. It supplies library code, protocol and database contracts, migrations, tests, and tools that can be integrated into a separately operated host. **Cloning or installing this package does not deploy a bus, configure a provider, register an authenticated identity, or grant permission for an external effect.**

## What is implemented

- **Versioned envelopes and validation:** `radar.envelope` parses transport-neutral message envelopes and returns explicit classification results.
- **Routing and identity primitives:** `radar.registry` maintains logical identities, nodes, endpoints, subscriptions, and lease/status checks; `radar.routing` supplies decisions and a bounded priority queue.
- **Duplicate suppression:** `radar.routing.DedupeWindow` rejects repeated idempotency keys within a configured millisecond window, using caller-provided monotonic timestamps. This is a local admission primitive, **not** durable or cross-provider exactly-once delivery.
- **Telemetry:** `radar.telemetry` includes SQLite-backed counters and heartbeat records; `radar.heartbeat` supplies a heartbeat scheduler.
- **Provider-neutral integration assets:** public-safe Supabase schema/migration and Edge/runtime components are versioned here. Actual database credentials, topology, installation, and service qualification belong to an authorized deployment.
- **Read-only shadow comparison:** `radar.sql_connectome_shadow.compare_identity_shadow` compares a narrowly bounded identity projection through an injected candidate reader. A matching digest is explicitly **not** provider authentication, parity, cutover approval, or a routing change. See [shadow-comparison limits](docs/operator/SQL_CONNECTOME_SHADOW_V1.md).

These are source-level capabilities and contracts, not claims that a particular external bus, provider, or host is currently running them.

## Install and test

Requires **Python 3.11 or later**. The package declares no mandatory Python runtime dependencies; the optional test extra installs the pinned test tools. In a disposable Python environment:

```bash
git clone https://github.com/thebrazenbeard/ccb-core.git
cd ccb-core
python -m pip install -e '.[test]'
python -m pytest -q
```

The [CI workflow](.github/workflows/ci.yml) tests Python on Linux and includes a Windows workstation-relay lane. SQL and Supabase qualification additionally require an explicitly configured local PostgreSQL/Supabase/Docker environment; a Python unit-test pass does not replace those checks.

### Minimal library example: duplicate-admission window

```python
from radar.routing import DedupeWindow

window = DedupeWindow(window_ms=500)

assert window.accept("event-001", now_ms=1_000)
assert not window.accept("event-001", now_ms=1_250)
assert window.accept("event-001", now_ms=1_600)
```

The supplied times are monotonic milliseconds from the caller's clock. This checks one process-local idempotency window; it does not authenticate the origin of an event, ensure external delivery, or allocate a provider lease.

## Integration and public/private boundary

CCB Base is the canonical reusable-code repository. The related [chat-communication-bus](https://github.com/thebrazenbeard/chat-communication-bus) repository is **public coordination**, not a private data vault or an alternate implementation source of truth. See the [repository role contract](docs/operator/REPOSITORY_ROLES.md) and [public core boundary](PUBLIC_CORE_BOUNDARY.md).

Sensitive material—private message/mailbox history, writer-lane contents, identity records, credentials, trust anchors, private topology, and host receipts—belongs in **separately access-controlled** deployment storage, not this public source tree or a public coordination issue. A source commit cannot retroactively remove material already published in Git history.

The trusted projection and writer-lane validation tools accept explicit private-overlay inputs such as `--topology <path>` and `--cutover <path>`. Missing required inputs must fail closed. Do not embed personal workstation names, private network coordinates, tokens, or deployment-specific paths in reusable examples or submitted reports.

Keep three controls separate:

1. **Capability:** the exact source revision implements and passes a bounded operation.
2. **Advertisement:** a host can discover an appropriate typed, version-bound capability.
3. **Authority:** an independently trusted host verifies the caller, subject, privacy scope, and requested effect.

CCB Base source contracts and tests do not, by themselves, establish immutable host-owned provider bindings or a coherent authorization service. Likewise, a plaintext acknowledgement is not a confidentiality or authenticated-delivery guarantee. Protected actions still require separately verified runtime authority, idempotency/replay handling, and physical-provider qualification.

## Contributing and rights

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the repository license before proposing code or documentation. Contributions must respect provenance, third-party rights, and the repository's specific legal acceptance process. Public visibility alone does not grant commercial reuse rights.

The implementation, tests, and protocol documents describe **bounded software behavior**. They are not evidence of live host installation, authorization grants, security certification, or private-operator acceptance.

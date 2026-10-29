# Authenticated receiver ACK gate — source candidate

**Status: source-only; no deployed receiver, provider proof, Bus delivery, or model-response qualification.**

The existing workstation `ACK_VERIFIED` is a plaintext UI match and remains
unchanged. It must never be promoted by this module alone.

`radar.authenticated_delivery` introduces a separate host-only gate:

1. An independently configured reader verifies the exact canonical Bus message
   ID, commit, digest, recipient, subject, privacy scope and challenge nonce.
2. A host-injected provider verifier resolves an opaque inbound event into a
   real authenticated receiver principal/session bound to those exact values.
   Its trust anchors and credentials must never be model-visible or derived
   from the ACK body, browser title, URL, or GitHub issue author.
3. A different host authorizer checks this verified principal against the
   recipient, subject, privacy scope and permitted effect. A positive provider
   proof is not automatically authority.
4. A host-private SQLite ledger commits an exact ACK once, with uniqueness
   across receiver/message, nonce and provider proof ID. Replays, including
   after process restart, fail closed.

Only the immutable `AUTHENTICATED_RECEIVER_ACK_ONLY` receipt is returned.
`model_response_verified` stays false. A separately instrumented, authenticated
model invocation/response witness is required to establish model consumption.
The ledger's internal `_record_once` operation returns only a low-level
timestamp, not an authenticated receipt. Only the host-verified gate mints
the receipt after all checks pass. This source-level API distinction does not
secure arbitrary Python callers or direct SQLite writers; host process
isolation, ledger ACLs and real verifier custody remain mandatory.

This module deliberately does **not** select provider clients, open sockets,
discover actors, send messages, grant capabilities, migrate databases, or make
provider assertions. It is not wired into workstation relay, browser UI,
private writer branches, or the live Lappy process.

Negative tests cover forged exact plaintext ACK, mismatched canonical bindings,
unauthorized privacy scope, replay across SQLite restart, direct-sink assurance
bypass and false model claim.

Further admission gates before runtime use: independently qualify an actual
provider verifier and client credential custody, canonical source readback,
private ledger filesystem ACL and integrity, single-writer/concurrent racing,
authenticated model-run evidence, real-host deny controls and rollback. A fake
verifier in unit tests is **not** production authentication.

Coordination: `chat-communication-bus#397`,
`project_unfuck_runtime_coordination`.

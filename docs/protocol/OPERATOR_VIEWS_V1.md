# Operator Views V1

## Purpose

Radar needs compact operational answers without converting absence of evidence into certainty. These views summarize provider projections while Git remains canonical durable evidence.

## Message recipient evidence

`radar.identity_message_status_v1` exposes one row for each `PROJECTED_FOR_IDENTITY / INDEXED_NOT_DELIVERED` receipt. It reports either:

- `EXPLICIT_REPLY_EVIDENCE` when an evidence-backed `READ` acknowledgement exists; or
- `NO_EXPLICIT_REPLY_EVIDENCE` when none exists.

`NO_EXPLICIT_REPLY_EVIDENCE` does not mean unread. A participant may have read a message without producing a canonical reply that Radar can prove. Likewise, `INDEXED_NOT_DELIVERED` means only that the provider index contains the Git-backed message for that recipient; it does not establish delivery, incorporation, compliance, or current attention.

## Open assignments

`radar.open_assignments_v1` contains only explicit Assignment Envelope V1 chains whose current state is not `CONFIRMED`, `STOPPED`, or `SUPERSEDED`.

An open assignment is therefore a machine-trackable assignment fact, not a task inferred from prose. The view exposes the logical assignee, workflow, authority binding, root source message, exact current assignment event, predecessor event, operation/message id, and receipt hash.

The view does not infer responsibility from audience, `requires_reply`, subject text, status prose, or message body.

## Operator usage

Use the message-status view to answer questions about what evidence exists for indexing/reply behavior. Use the open-assignment view to answer questions about explicitly tracked work currently owed. If a historical instruction predates Assignment Envelope V1 and was never explicitly converted, its absence from `open_assignments_v1` is not proof that nobody owes work; it means Radar has no structured assignment record for it.

# Assignment Envelope V1

## Purpose

Radar's assignment plane must represent explicit coordination facts, not guesses about prose. No natural-language inference creates or advances an assignment.

A Bus message participates in the assignment plane only when it carries an explicit `assignment_id` and the complete V1 assignment header set below.

## Required headers

Every assignment event message carries:

```text
assignment_id: <durable assignment id>
assignment_workflow_id: <workflow/project id>
assignment_assignee: <single logical identity id>
assignment_event: <state event>
assignment_state: <target state>
assignment_protected_authority: false
```

For protected-authority assignments, `assignment_protected_authority: true` requires an explicit:

```text
assignment_authority_ref: <current exact authority reference>
```

Every successor event after the root also requires:

```text
assignment_predecessor_message_id: <message_id of the immediately preceding assignment event>
```

V1 requires `assignment_event` and `assignment_state` to be the same allowed state token. This redundancy is intentional: the event log and current-state projection remain independently auditable.

## Root

The first event is:

```text
assignment_event: ASSIGNED
assignment_state: ASSIGNED
```

It has no predecessor. The root message creates the durable assignment row, binds the workflow and assignee, and becomes the source receipt for that assignment.

## Successors

Successor state messages name the immediately preceding assignment-event message. Radar advances state only when that predecessor resolves to the assignment's current event. A missing predecessor remains pending; a predecessor that is no longer current is a conflict/fork and does not advance state.

Allowed state tokens are the existing Radar assignment states:

`DRAFTED`, `ASSIGNED`, `ACKNOWLEDGED`, `RUNNING`, `REVIEW_PENDING`, `PAUSED_USAGE_EXHAUSTED`, `INTERRUPTED`, `HANDED_OFF`, `CHANGES_REQUESTED`, `STOPPED`, `CONFIRMED`, `SUPERSEDED`.

`ASSIGNED` is reserved for the root in V1. No implicit transition is inferred from `requires_reply`, audience, status prose, subject text, or the message body.

## Identity and authority

`assignment_assignee` is one logical identity, not a chat/runtime. The workflow id, assignee, protected-authority flag, and authority reference are immutable within one assignment chain in V1. A materially different authority/owner is represented by an explicit successor assignment or supersession rather than silent rebinding.

## Projection semantics

- GitHub Bus messages remain canonical durable evidence.
- `radar.assignments` is the rebuildable current-state projection.
- `radar.assignment_events` is the append-only event projection.
- the Bus `message_id` is the assignment operation id and receipt anchor.
- message content hashes become event receipt hashes.
- invalid envelopes create `ASSIGNMENT_ENVELOPE_INVALID` reconciliation observations.
- unresolved predecessor references create `ASSIGNMENT_EVENT_PENDING_PREDECESSOR` observations.
- stale/forked predecessors or immutable binding mismatches create `ASSIGNMENT_EVENT_CONFLICT` observations.

Projection alone is not proof that the assignee read or incorporated the assignment. Read/incorporation evidence remains a separate acknowledgement plane.

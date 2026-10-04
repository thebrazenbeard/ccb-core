# Workstation Inter-chat Relay V1

Status: PROTOTYPE_ONLY

Authority: reusable CCB/Radar source contract. The Chat Communication Bus remains canonical authority for relayed material. Visible workstation text is a transport hint only.

## Scope

The relay exists to move a bounded, inert canary between already-open ChatGPT conversations on one authorized Windows workstation when no direct inter-chat transport exists.

It does not create direct ChatGPT API access, hidden-state access, conversation scraping authority, or any protected-effect authority.

Current producer ceiling: CANARY only.

## Envelope

Wire protocol: `BT2_WORKSTATION_RELAY_V1`.

Required semantic fields:

- sender and recipient logical lane identities;
- collision-resistant message ID and nonce;
- canonical Bus message ID and exact 40-character Bus commit;
- SHA-256 of UTF-8/LF canonical body;
- `requires_ack: true`;
- content class;
- body.

The machine-readable contract is `architecture/contracts/WORKSTATION_INTERCHAT_RELAY_V1.schema.json`.

## Target identity

A target is not “whatever has focus.”

A valid target requires two independent exact selectors:

1. normalized ChatGPT conversation URL path read from Firefox's accessible address-bar value;
2. expected visible chat identity/title read from the Firefox accessibility tree.

Discovery enumerates Firefox windows and tabs, explicitly selects each candidate tab, reads both selectors, and returns only exact matches.

The selected tab is carried as an opaque target token in addition to the native window handle. The token is bound into the selector digest. This matters because multiple ChatGPT tabs may share one Firefox top-level HWND.

Before composer write and immediately before submit, the relay re-reads both selectors on the exact bound tab. Any mismatch terminates `TARGET_CHANGED_PRE_SUBMIT` without submission.

## Sender states

Happy path:

`CREATED -> BUS_BOUND -> TARGET_DISCOVERED -> TARGET_VERIFIED_PREWRITE -> COMPOSER_POPULATED -> TARGET_REVERIFIED_PRESUBMIT -> SUBMITTED -> RENDERED_READBACK_VERIFIED -> ACK_PENDING -> ACK_VERIFIED`.

Terminal failure or uncertainty states:

- `TARGET_NOT_FOUND`;
- `TARGET_AMBIGUOUS`;
- `TARGET_CHANGED_PRE_SUBMIT`;
- `COMPOSER_WRITE_FAILED`;
- `SUBMIT_NOT_ESTABLISHED`;
- `SUBMITTED_UNVERIFIED`;
- `ACK_TIMEOUT`;
- `ACK_MISMATCH`;
- `SOURCE_BUS_UNVERIFIED`.

## Durable uncertainty rule

`SUBMITTED` is persisted before post-send readback begins.

If the visible rendered message cannot then be proven, the operation becomes `SUBMITTED_UNVERIFIED`. Re-invoking the same message ID and nonce returns the durable receipt rather than sending again.

A later retry requires reconciliation that separately proves the original message is absent. V1 does not implement an automatic replay path after `SUBMITTED`.

External source-verifier, discovery, and activation failures are converted into fail-closed terminal receipts rather than escaping and silently stranding an operation. Terminal state and its receipt are written in one SQLite transaction; the store does not first persist a terminal label without its evidence receipt.

## Recipient verification

Every relay text instructs the receiving chat to verify the referenced canonical Bus message and commit before acting.

A verified receiver replies with the exact `BT2_WORKSTATION_RELAY_ACK_V1` body bound to message ID, nonce, and body SHA-256.

If the Bus source cannot be verified, the receiver must return `HOLD_SOURCE_UNVERIFIED` and not act on the relayed body.

## Evidence receipt

Receipts preserve:

- protocol and logical identities;
- message ID, nonce, Bus message ID, and Bus commit;
- body SHA-256;
- every state transition and timestamp;
- window handle and exact tab token;
- prewrite and presubmit selector values and digests;
- rendered-message verification result and digest;
- observed ACK body, digest, and status when present;
- final state;
- `side_effect_beyond_visible_text: false`.

A receipt is evidence of the sender-side observation. It is not proof that the receiving model incorporated or acted on the content.

## Canary admission

Current source accepts only a single-line structured marker:

`BT2_CANARY: marker=<1-128 characters from A-Z, a-z, 0-9, dot, underscore, colon, or hyphen>`

Free-form prose, newlines, shell text, code, commands, and arbitrary instructions are not valid canaries. The qualification grammar is intentionally data-only rather than a command denylist.

Informational/work-bearing content is a future promotion, not current authority.

## Windows / Firefox implementation

The transport-neutral target contract lives in `src/radar/workstation_target.py`.

The Windows/Firefox adapter lives in `src/radar/windows_firefox_target.py`.

The concrete mechanism is Microsoft UI Automation through optional pinned dependency `uiautomation==2.0.29`. The dependency is optional so non-Windows CCB installs do not acquire Windows-only runtime requirements. Hosted Windows CI installs the dependency, imports the UIA backend, and runs the focused relay suite.

Live qualification must prove the actual Firefox/ChatGPT accessibility tree on the authorized workstation. Source tests and hosted Windows CI do not prove a real local browser session.

## Local configuration

Private target configuration belongs outside Git, for example:

`~/.bt2/workstation-relay-targets.json`

Commit only fake examples and schemas. Never commit real conversation URLs, cookies, browser-profile secrets, auth headers, bearer tokens, passwords, or session material.

The public-safe configuration schema is `architecture/contracts/WORKSTATION_RELAY_TARGETS_V1.schema.json`.

## Promotion

Current status remains `PROTOTYPE_ONLY` until the complete inert-canary qualification suite runs against the real authorized Windows/Firefox session.

Passing source/unit/hosted-CI tests alone does not establish `QUALIFIED_CANARY_ONLY`.

A later promotion to informational or work-bearing text requires a separate review and Patrick's explicit authorization. Protected effects require still-separate authority.

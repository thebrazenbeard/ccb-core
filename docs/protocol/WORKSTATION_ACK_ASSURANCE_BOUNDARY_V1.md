# Workstation relay ACK assurance boundary

Status: SOURCE-ONLY NEGATIVE ASSURANCE. This document adds no authority to the prototype.

`BT2_WORKSTATION_RELAY_ACK_V1` is an exact plaintext comparison contract. Its
`message_id`, `nonce`, and `body_sha256` bind the response *text* to one sent
canary; they do not authenticate the speaker. Anyone with the envelope fields
can construct an identical ACK without receiving a message, reading the
canonical Bus source, or running a model.

The current `ACK_VERIFIED` relay state means only that the sender-side target
returned text matching that contract. It **does not** mean:

- a cryptographically authenticated receiver principal acknowledged a Bus row;
- an authorized receiver persisted that row or a durable ACK transaction;
- the receiving model read the Bus source or produced an inference response;
- the message survived a process restart or was uniquely deduplicated end-to-end.

`RelayReceipt.side_effect_beyond_visible_text` remains `false`, including when
the prototype reports `ACK_VERIFIED`. Its UIA title/URL selectors identify a
visible tab candidate, not an authenticated receiver principal.

Do not promote this state to an authenticated-delivery or model-response PASS.
An eventual stronger channel requires a receiver principal bound to an
authenticated provider session, canonical message ID and digest readback, a
durably committed receipt bound to that principal, replay/forgery controls,
and a separately observed actual model response. Keep these evidence classes
distinct; do not infer them from a browser-text match.

See `WORKSTATION_INTERCHAT_RELAY_V1.md` for the prototype's bounded CANARY
scope and `docs/operator/REPOSITORY_ROLES.md` for the public/private split.

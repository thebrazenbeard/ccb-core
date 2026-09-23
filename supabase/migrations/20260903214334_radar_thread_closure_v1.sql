-- Additive evidence view for prospective conversational thread closure.
-- Git-backed message content remains canonical. This view does not claim private
-- awareness, does not alter assignment lifecycle, and does not mutate v1.

create or replace view radar.identity_message_status_v2
with (security_invoker = true)
as
select
  d.message_id,
  d.identity_id as recipient_identity_id,
  m.sender,
  m.created_at as message_created_at,
  m.requires_ack,
  m.payload->>'subject' as subject,
  case
    when m.payload->>'conversation_closed_by_sender' = 'true' then true
    else false
  end as conversation_closed_by_sender,
  d.event_id as delivery_event_id,
  d.event_type as delivery_event_type,
  d.outcome as delivery_outcome,
  d.detail as delivery_detail,
  case
    when a.acknowledgement_id is not null then 'REPLY_EVIDENCE_PRESENT'
    when m.payload->>'conversation_closed_by_sender' = 'true' then 'CLOSED_BY_ENDTHREAD'
    else 'NO_REPLY_EVIDENCE_YET'
  end as conversation_reply_state,
  a.acknowledgement_id as read_acknowledgement_id,
  a.evidence_ref,
  a.created_at as read_evidence_created_at,
  m.source_refs,
  m.content_hash,
  m.projection_status
from radar.delivery_events d
join radar.messages m
  on m.message_id = d.message_id
left join radar.acknowledgements a
  on a.message_id = d.message_id
 and a.identity_id = d.identity_id
 and a.acknowledgement_type = 'READ'
where d.event_type = 'PROJECTED_FOR_IDENTITY'
  and d.outcome = 'INDEXED_NOT_DELIVERED'
  and d.identity_id is not null;

revoke all on radar.identity_message_status_v2 from public;
revoke all on radar.identity_message_status_v2 from anon;
revoke all on radar.identity_message_status_v2 from authenticated;
grant select on radar.identity_message_status_v2 to service_role;

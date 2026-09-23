-- Evidence-honest operator views for Radar.
-- These views summarize projected facts; they do not raise the claim ceiling.

create or replace view radar.identity_message_status_v1
with (security_invoker = true)
as
select
  d.message_id,
  d.identity_id as recipient_identity_id,
  m.sender,
  m.created_at as message_created_at,
  m.requires_ack,
  m.payload->>'subject' as subject,
  d.event_id as delivery_event_id,
  d.event_type as delivery_event_type,
  d.outcome as delivery_outcome,
  d.detail as delivery_detail,
  case
    when a.acknowledgement_id is not null then 'EXPLICIT_REPLY_EVIDENCE'
    else 'NO_EXPLICIT_REPLY_EVIDENCE'
  end as reply_evidence_status,
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

create or replace view radar.open_assignments_v1
with (security_invoker = true)
as
with assignment_base as (
  select
    a.assignment_id,
    a.workflow_id,
    lower(a.current_state) as current_state,
    a.assignee_identity_id,
    a.authority_ref,
    a.protected_authority,
    a.source_message_id,
    a.current_event_id,
    a.created_at,
    a.updated_at,
    e.event_type as current_event_type,
    e.actor as current_event_actor,
    e.from_state as current_event_from_state,
    e.to_state as current_event_to_state,
    e.predecessor_event_id,
    e.operation_id,
    e.receipt_sha256,
    e.detail as current_event_detail,
    e.created_at as current_event_created_at
  from radar.assignments a
  left join radar.assignment_events e
    on e.event_id = a.current_event_id
)
select *
from assignment_base
where current_state not in ('confirmed','stopped','superseded');

revoke all on radar.identity_message_status_v1 from public;
revoke all on radar.identity_message_status_v1 from anon;
revoke all on radar.identity_message_status_v1 from authenticated;
grant select on radar.identity_message_status_v1 to service_role;

revoke all on radar.open_assignments_v1 from public;
revoke all on radar.open_assignments_v1 from anon;
revoke all on radar.open_assignments_v1 from authenticated;
grant select on radar.open_assignments_v1 to service_role;

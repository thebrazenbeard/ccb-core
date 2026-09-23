-- Evidence-backed Bus receipt projection.
--
-- Claim ceiling:
-- * PROJECTED_FOR_IDENTITY / INDEXED_NOT_DELIVERED means only that Radar's
--   rebuildable provider index contains a Git-backed message addressed to the
--   identity. It is explicitly not proof of delivery, reading, or incorporation.
-- * READ acknowledgements are derived only from a later Git-backed Bus message
--   whose reply_to/in_reply_to resolves unambiguously to one projected message.
-- * Ambiguous historical declared message ids fail closed and are surfaced as
--   reconciliation observations rather than arbitrarily crediting a read.

create unique index if not exists radar_delivery_projected_identity_unique_idx
  on radar.delivery_events(message_id, identity_id, event_type)
  where event_type = 'PROJECTED_FOR_IDENTITY' and identity_id is not null;

create or replace function radar.reconcile_bus_receipts_v1()
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  inserted_projection_events integer := 0;
  inserted_read_acks integer := 0;
  inserted_ambiguities integer := 0;
begin
  -- Serialize reconciliation so append-only observation rows remain idempotent
  -- even when several writer-lane projection jobs arrive at the same time.
  perform pg_advisory_xact_lock(hashtextextended('radar.reconcile_bus_receipts_v1', 0));

  insert into radar.delivery_events(
    message_id,
    identity_id,
    endpoint_id,
    event_type,
    outcome,
    detail
  )
  select distinct
    m.message_id,
    i.identity_id,
    null,
    'PROJECTED_FOR_IDENTITY',
    'INDEXED_NOT_DELIVERED',
    jsonb_build_object(
      'claim_ceiling', 'provider_index_only_not_delivery',
      'source_refs', to_jsonb(m.source_refs),
      'projection_status', m.projection_status
    )
  from radar.messages m
  cross join lateral unnest(m.audience) as audience(identity_id)
  join radar.identities i
    on i.identity_id = lower(btrim(audience.identity_id))
  where m.domain = 'chat_bus'
  on conflict (message_id, identity_id, event_type)
    where event_type = 'PROJECTED_FOR_IDENTITY' and identity_id is not null
  do nothing;
  get diagnostics inserted_projection_events = row_count;

  with reply_refs as (
    select distinct
      r.message_id as reply_message_id,
      lower(r.sender) as reply_sender,
      lower((matched.parts)[1]) as referenced_id,
      coalesce((r.source_refs)[1], r.message_id) as evidence_ref
    from radar.messages r
    cross join lateral regexp_matches(
      lower(
        coalesce(r.payload->>'reply_to', '') || ' ' ||
        coalesce(r.payload->>'in_reply_to', '')
      ),
      '([a-z][a-z0-9-]*-[0-9]{4}(~[0-9a-f]{16,64})?)',
      'g'
    ) as matched(parts)
    where r.domain = 'chat_bus'
  ),
  candidates as (
    select
      rr.reply_message_id,
      rr.reply_sender,
      rr.referenced_id,
      rr.evidence_ref,
      target.message_id as target_message_id,
      count(*) over (
        partition by rr.reply_message_id, rr.referenced_id
      ) as candidate_count
    from reply_refs rr
    join radar.messages target
      on target.domain = 'chat_bus'
     and (
       lower(target.message_id) = rr.referenced_id
       or lower(coalesce(target.payload->>'declared_message_id', '')) = rr.referenced_id
     )
  )
  insert into radar.acknowledgements(
    message_id,
    identity_id,
    acknowledgement_type,
    evidence_ref
  )
  select
    c.target_message_id,
    i.identity_id,
    'READ',
    c.evidence_ref
  from candidates c
  join radar.identities i
    on i.identity_id = c.reply_sender
  where c.candidate_count = 1
    and c.target_message_id <> c.reply_message_id
  on conflict (message_id, identity_id, acknowledgement_type) do nothing;
  get diagnostics inserted_read_acks = row_count;

  with reply_refs as (
    select distinct
      r.message_id as reply_message_id,
      lower(r.sender) as reply_sender,
      lower((matched.parts)[1]) as referenced_id,
      coalesce((r.source_refs)[1], r.message_id) as evidence_ref
    from radar.messages r
    cross join lateral regexp_matches(
      lower(
        coalesce(r.payload->>'reply_to', '') || ' ' ||
        coalesce(r.payload->>'in_reply_to', '')
      ),
      '([a-z][a-z0-9-]*-[0-9]{4}(~[0-9a-f]{16,64})?)',
      'g'
    ) as matched(parts)
    where r.domain = 'chat_bus'
  ),
  candidates as (
    select
      rr.reply_message_id,
      rr.reply_sender,
      rr.referenced_id,
      rr.evidence_ref,
      target.message_id as target_message_id,
      count(*) over (
        partition by rr.reply_message_id, rr.referenced_id
      ) as candidate_count
    from reply_refs rr
    join radar.messages target
      on target.domain = 'chat_bus'
     and (
       lower(target.message_id) = rr.referenced_id
       or lower(coalesce(target.payload->>'declared_message_id', '')) = rr.referenced_id
     )
  ),
  ambiguous as (
    select
      reply_message_id,
      reply_sender,
      referenced_id,
      evidence_ref,
      array_agg(target_message_id order by target_message_id) as candidate_message_ids
    from candidates
    where candidate_count > 1
    group by reply_message_id, reply_sender, referenced_id, evidence_ref
  )
  insert into radar.reconciliation_events(code, repair_class, source_ref, detail)
  select
    'AMBIGUOUS_REPLY_REFERENCE',
    'OBSERVATION_ONLY',
    a.evidence_ref,
    jsonb_build_object(
      'reply_message_id', a.reply_message_id,
      'reply_sender', a.reply_sender,
      'referenced_id', a.referenced_id,
      'candidate_message_ids', to_jsonb(a.candidate_message_ids),
      'resolution', 'no_read_acknowledgement_emitted'
    )
  from ambiguous a
  where not exists (
    select 1
    from radar.reconciliation_events e
    where e.code = 'AMBIGUOUS_REPLY_REFERENCE'
      and e.detail->>'reply_message_id' = a.reply_message_id
      and e.detail->>'referenced_id' = a.referenced_id
  );
  get diagnostics inserted_ambiguities = row_count;

  return jsonb_build_object(
    'projection_events_inserted', inserted_projection_events,
    'read_acknowledgements_inserted', inserted_read_acks,
    'ambiguous_reply_observations_inserted', inserted_ambiguities
  );
end;
$$;

create or replace function radar.bus_message_receipts_after_insert_v1()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
begin
  perform radar.reconcile_bus_receipts_v1();
  return null;
end;
$$;

drop trigger if exists radar_messages_receipt_projection on radar.messages;
create trigger radar_messages_receipt_projection
after insert on radar.messages
for each statement execute function radar.bus_message_receipts_after_insert_v1();

revoke all on function radar.reconcile_bus_receipts_v1() from public;
revoke all on function radar.reconcile_bus_receipts_v1() from anon;
revoke all on function radar.reconcile_bus_receipts_v1() from authenticated;
grant execute on function radar.reconcile_bus_receipts_v1() to service_role;

revoke all on function radar.bus_message_receipts_after_insert_v1() from public;
revoke all on function radar.bus_message_receipts_after_insert_v1() from anon;
revoke all on function radar.bus_message_receipts_after_insert_v1() from authenticated;
grant execute on function radar.bus_message_receipts_after_insert_v1() to service_role;

-- One idempotent backfill brings the already-projected historical Bus corpus
-- into the new receipt plane without rewriting Git or existing message rows.
select radar.reconcile_bus_receipts_v1();

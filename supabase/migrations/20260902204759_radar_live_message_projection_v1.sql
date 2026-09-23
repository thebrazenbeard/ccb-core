-- Radar live Bus message projection V1.
-- Git is canonical; Supabase is a metadata-only live projection.

create or replace function radar.project_bus_message_v1(
  p_message_id text,
  p_created_at timestamptz,
  p_sender text,
  p_audience text[],
  p_requires_ack boolean,
  p_source_refs text[],
  p_content_hash text,
  p_idempotency_key text,
  p_payload jsonb
)
returns text
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  existing_hash text;
  existing_domain text;
  existing_sources text[];
begin
  if p_message_id is null or btrim(p_message_id) = '' then
    raise exception 'MESSAGE_ID_REQUIRED' using errcode = '22023';
  end if;
  if p_sender is null or btrim(p_sender) = '' then
    raise exception 'SENDER_REQUIRED' using errcode = '22023';
  end if;
  if p_created_at is null then
    raise exception 'CREATED_AT_REQUIRED' using errcode = '22023';
  end if;
  if p_content_hash is null or p_content_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'INVALID_CONTENT_HASH' using errcode = '22023';
  end if;
  if p_payload is null then
    raise exception 'PAYLOAD_REQUIRED' using errcode = '22023';
  end if;

  -- Serialize concurrent projections of the same semantic message id.
  perform pg_advisory_xact_lock(hashtextextended(p_message_id, 0));

  select content_hash, domain, source_refs
    into existing_hash, existing_domain, existing_sources
  from radar.messages
  where message_id = p_message_id;

  if found then
    if existing_hash = p_content_hash and existing_domain = 'chat_bus' then
      return 'IDEMPOTENT';
    end if;

    insert into radar.reconciliation_events(code, repair_class, source_ref, detail)
    values (
      'MESSAGE_PROJECTION_CONFLICT',
      'AMBIGUOUS_DURABLE_CONFLICT',
      coalesce((p_source_refs)[1], p_message_id),
      jsonb_build_object(
        'message_id', p_message_id,
        'existing_content_hash', existing_hash,
        'incoming_content_hash', p_content_hash,
        'existing_domain', existing_domain,
        'existing_source_refs', coalesce(to_jsonb(existing_sources), '[]'::jsonb),
        'incoming_source_refs', coalesce(to_jsonb(p_source_refs), '[]'::jsonb)
      )
    );
    return 'CONFLICT';
  end if;

  insert into radar.messages (
    message_id,
    schema_version,
    created_at,
    sender,
    audience,
    domain,
    intent,
    priority,
    root_task_id,
    correlation_id,
    causal_parent_id,
    requires_ack,
    expires_at,
    authority_ref,
    source_refs,
    content_hash,
    idempotency_key,
    payload,
    projection_status
  ) values (
    p_message_id,
    1,
    p_created_at,
    lower(btrim(p_sender)),
    coalesce(p_audience, '{}'::text[]),
    'chat_bus',
    'bus_message',
    3,
    null,
    null,
    null,
    coalesce(p_requires_ack, false),
    null,
    null,
    coalesce(p_source_refs, '{}'::text[]),
    p_content_hash,
    p_idempotency_key,
    p_payload,
    'ACCEPTED'
  );

  return 'INSERTED';
end;
$$;

revoke all on function radar.project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb) from public;
revoke all on function radar.project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb) from anon;
revoke all on function radar.project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb) from authenticated;
grant execute on function radar.project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb) to service_role;

-- PostgREST normally exposes `public`, not the isolated `radar` schema. This
-- narrow service-role-only bridge keeps all data/effects in radar while giving
-- the Edge Function a stable RPC surface. It carries no independent authority.
create or replace function public.radar_project_bus_message_v1(
  p_message_id text,
  p_created_at timestamptz,
  p_sender text,
  p_audience text[],
  p_requires_ack boolean,
  p_source_refs text[],
  p_content_hash text,
  p_idempotency_key text,
  p_payload jsonb
)
returns text
language sql
security invoker
set search_path = pg_catalog, radar
as $$
  select radar.project_bus_message_v1(
    p_message_id,
    p_created_at,
    p_sender,
    p_audience,
    p_requires_ack,
    p_source_refs,
    p_content_hash,
    p_idempotency_key,
    p_payload
  );
$$;

revoke all on function public.radar_project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb) from public;
revoke all on function public.radar_project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb) from anon;
revoke all on function public.radar_project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb) from authenticated;
grant execute on function public.radar_project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb) to service_role;

-- Realtime is a live/query surface, not canonical custody.
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'radar'
      and tablename = 'messages'
  ) then
    alter publication supabase_realtime add table radar.messages;
  end if;

  if to_regclass('radar.identity_visuals') is not null and not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'radar'
      and tablename = 'identity_visuals'
  ) then
    alter publication supabase_realtime add table radar.identity_visuals;
  end if;
end
$$;

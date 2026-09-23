-- Preserve colliding historical Bus messages without rewriting Git history.
-- The declared message id remains in payload provenance. The first observed
-- row keeps the plain id; later different-content rows receive a stable,
-- content-qualified projection id and an explicit reconciliation event.

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
  collision_id text;
  collision_hash text;
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

  perform pg_advisory_xact_lock(hashtextextended(p_message_id, 0));

  select content_hash, domain, source_refs
    into existing_hash, existing_domain, existing_sources
  from radar.messages
  where message_id = p_message_id;

  if found then
    if existing_hash = p_content_hash and existing_domain = 'chat_bus' then
      return 'IDEMPOTENT';
    end if;

    collision_id := p_message_id || '~' || substring(p_content_hash from 1 for 16);

    select content_hash into collision_hash
    from radar.messages
    where message_id = collision_id;

    if found then
      if collision_hash = p_content_hash then
        return 'CONFLICT_IDEMPOTENT:' || collision_id;
      end if;
      -- A prefix collision is fantastically unlikely, but correctness should
      -- not depend on probability. Escalate deterministically to the full hash.
      collision_id := p_message_id || '~' || p_content_hash;
      select content_hash into collision_hash
      from radar.messages
      where message_id = collision_id;
      if found then
        if collision_hash = p_content_hash then
          return 'CONFLICT_IDEMPOTENT:' || collision_id;
        end if;
        raise exception 'QUALIFIED_MESSAGE_ID_COLLISION' using errcode = '23505';
      end if;
    end if;

    if not exists (
      select 1
      from radar.reconciliation_events
      where code = 'MESSAGE_PROJECTION_CONFLICT'
        and detail->>'message_id' = p_message_id
        and detail->>'incoming_content_hash' = p_content_hash
    ) then
      insert into radar.reconciliation_events(code, repair_class, source_ref, detail)
      values (
        'MESSAGE_PROJECTION_CONFLICT',
        'AMBIGUOUS_DURABLE_CONFLICT',
        coalesce((p_source_refs)[1], p_message_id),
        jsonb_build_object(
          'message_id', p_message_id,
          'qualified_message_id', collision_id,
          'existing_content_hash', existing_hash,
          'incoming_content_hash', p_content_hash,
          'existing_domain', existing_domain,
          'existing_source_refs', coalesce(to_jsonb(existing_sources), '[]'::jsonb),
          'incoming_source_refs', coalesce(to_jsonb(p_source_refs), '[]'::jsonb),
          'resolution', 'preserved_as_content_qualified_projection_id'
        )
      );
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
      collision_id,
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
      p_payload || jsonb_build_object(
        'declared_message_id', p_message_id,
        'projection_collision', true,
        'collision_with_message_id', p_message_id,
        'qualified_message_id', collision_id
      ),
      'ACCEPTED'
    );

    return 'CONFLICT_INSERTED:' || collision_id;
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

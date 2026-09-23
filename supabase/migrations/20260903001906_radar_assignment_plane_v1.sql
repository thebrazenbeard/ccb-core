-- Explicit, evidence-backed assignment projection for Chat Communication Bus.
--
-- No natural-language inference occurs here. Only messages with explicit
-- assignment_* headers participate. Git remains canonical; these tables are a
-- rebuildable operational projection.

alter table radar.assignments
  add column if not exists assignee_identity_id text references radar.identities(identity_id) on delete restrict,
  add column if not exists source_message_id text references radar.messages(message_id) on delete restrict;

create index if not exists radar_assignments_assignee_state_idx
  on radar.assignments(assignee_identity_id, current_state, updated_at desc);

create unique index if not exists radar_assignments_source_message_unique_idx
  on radar.assignments(source_message_id)
  where source_message_id is not null;

create or replace function radar.reconcile_bus_assignments_v1()
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
declare
  rec record;
  assignment_row record;
  predecessor_event_id bigint;
  new_event_id bigint;
  assignment_id_value text;
  workflow_id_value text;
  assignee_value text;
  event_value text;
  state_value text;
  predecessor_message_value text;
  authority_ref_value text;
  protected_text text;
  protected_value boolean;
  source_ref_value text;
  progress boolean;
  inserted_events integer := 0;
  inserted_assignments integer := 0;
  invalid_observations integer := 0;
  pending_observations integer := 0;
  conflict_observations integer := 0;
  allowed_states constant text[] := array[
    'DRAFTED','ASSIGNED','ACKNOWLEDGED','RUNNING','REVIEW_PENDING',
    'PAUSED_USAGE_EXHAUSTED','INTERRUPTED','HANDED_OFF','CHANGES_REQUESTED',
    'STOPPED','CONFIRMED','SUPERSEDED'
  ];
begin
  perform pg_advisory_xact_lock(hashtextextended('radar.reconcile_bus_assignments_v1', 0));

  -- Invalid explicit envelopes are durable observations, never guessed into
  -- assignment state. Ordinary messages without assignment_* keys are ignored.
  for rec in
    select m.*
    from radar.messages m
    where m.domain = 'chat_bus'
      and m.payload ?| array[
        'assignment_id','assignment_workflow_id','assignment_assignee',
        'assignment_event','assignment_state','assignment_predecessor_message_id',
        'assignment_authority_ref','assignment_protected_authority'
      ]
    order by m.created_at, m.message_id
  loop
    assignment_id_value := btrim(coalesce(rec.payload->>'assignment_id', ''));
    workflow_id_value := btrim(coalesce(rec.payload->>'assignment_workflow_id', ''));
    assignee_value := lower(btrim(coalesce(rec.payload->>'assignment_assignee', '')));
    event_value := upper(btrim(coalesce(rec.payload->>'assignment_event', '')));
    state_value := upper(btrim(coalesce(rec.payload->>'assignment_state', '')));
    predecessor_message_value := btrim(coalesce(rec.payload->>'assignment_predecessor_message_id', ''));
    authority_ref_value := nullif(btrim(coalesce(rec.payload->>'assignment_authority_ref', '')), '');
    protected_text := lower(btrim(coalesce(rec.payload->>'assignment_protected_authority', 'false')));
    source_ref_value := coalesce((rec.source_refs)[1], rec.message_id);

    if protected_text in ('true','yes','1') then
      protected_value := true;
    elsif protected_text in ('false','no','0','') then
      protected_value := false;
    else
      protected_value := null;
    end if;

    if assignment_id_value = ''
       or workflow_id_value = ''
       or assignee_value = ''
       or event_value = ''
       or state_value = ''
       or protected_value is null
       or not (state_value = any(allowed_states))
       or event_value <> state_value
       or (event_value = 'ASSIGNED' and predecessor_message_value <> '')
       or (event_value <> 'ASSIGNED' and predecessor_message_value = '')
       or (protected_value and authority_ref_value is null)
       or not exists (
         select 1 from radar.identities i where i.identity_id = assignee_value
       )
    then
      if not exists (
        select 1
        from radar.reconciliation_events e
        where e.code = 'ASSIGNMENT_ENVELOPE_INVALID'
          and e.detail->>'message_id' = rec.message_id
      ) then
        insert into radar.reconciliation_events(code, repair_class, source_ref, detail)
        values (
          'ASSIGNMENT_ENVELOPE_INVALID',
          'OBSERVATION_ONLY',
          source_ref_value,
          jsonb_build_object(
            'message_id', rec.message_id,
            'assignment_id', nullif(assignment_id_value, ''),
            'assignment_workflow_id', nullif(workflow_id_value, ''),
            'assignment_assignee', nullif(assignee_value, ''),
            'assignment_event', nullif(event_value, ''),
            'assignment_state', nullif(state_value, ''),
            'assignment_predecessor_message_id', nullif(predecessor_message_value, ''),
            'assignment_protected_authority', protected_text,
            'resolution', 'assignment_state_not_projected'
          )
        );
        invalid_observations := invalid_observations + 1;
      end if;
    end if;
  end loop;

  -- Iterate to a fixed point so out-of-arrival-order messages can become
  -- processable once their explicit predecessor has been projected.
  loop
    progress := false;

    for rec in
      select m.*
      from radar.messages m
      where m.domain = 'chat_bus'
        and m.payload ? 'assignment_id'
        and not exists (
          select 1
          from radar.reconciliation_events e
          where e.code = 'ASSIGNMENT_ENVELOPE_INVALID'
            and e.detail->>'message_id' = m.message_id
        )
      order by m.created_at, m.message_id
    loop
      assignment_id_value := btrim(coalesce(rec.payload->>'assignment_id', ''));
      workflow_id_value := btrim(coalesce(rec.payload->>'assignment_workflow_id', ''));
      assignee_value := lower(btrim(coalesce(rec.payload->>'assignment_assignee', '')));
      event_value := upper(btrim(coalesce(rec.payload->>'assignment_event', '')));
      state_value := upper(btrim(coalesce(rec.payload->>'assignment_state', '')));
      predecessor_message_value := btrim(coalesce(rec.payload->>'assignment_predecessor_message_id', ''));
      authority_ref_value := nullif(btrim(coalesce(rec.payload->>'assignment_authority_ref', '')), '');
      protected_text := lower(btrim(coalesce(rec.payload->>'assignment_protected_authority', 'false')));
      protected_value := protected_text in ('true','yes','1');
      source_ref_value := coalesce((rec.source_refs)[1], rec.message_id);

      if exists (
        select 1
        from radar.assignment_events ae
        where ae.assignment_id = assignment_id_value
          and ae.operation_id = rec.message_id
      ) then
        continue;
      end if;

      select a.* into assignment_row
      from radar.assignments a
      where a.assignment_id = assignment_id_value;

      if event_value = 'ASSIGNED' then
        if found then
          if not exists (
            select 1
            from radar.reconciliation_events e
            where e.code = 'ASSIGNMENT_EVENT_CONFLICT'
              and e.detail->>'message_id' = rec.message_id
          ) then
            insert into radar.reconciliation_events(code, repair_class, source_ref, detail)
            values (
              'ASSIGNMENT_EVENT_CONFLICT',
              'AMBIGUOUS_DURABLE_CONFLICT',
              source_ref_value,
              jsonb_build_object(
                'message_id', rec.message_id,
                'assignment_id', assignment_id_value,
                'reason', 'assignment_root_already_exists',
                'current_event_id', assignment_row.current_event_id,
                'resolution', 'state_not_advanced'
              )
            );
            conflict_observations := conflict_observations + 1;
          end if;
          continue;
        end if;

        insert into radar.assignments(
          assignment_id,
          workflow_id,
          current_state,
          authority_ref,
          protected_authority,
          current_event_id,
          assignee_identity_id,
          source_message_id,
          created_at,
          updated_at
        ) values (
          assignment_id_value,
          workflow_id_value,
          'DRAFTED',
          authority_ref_value,
          protected_value,
          null,
          assignee_value,
          rec.message_id,
          rec.created_at,
          rec.created_at
        );

        insert into radar.assignment_events(
          assignment_id,
          event_type,
          actor,
          from_state,
          to_state,
          predecessor_event_id,
          operation_id,
          receipt_sha256,
          detail,
          created_at
        ) values (
          assignment_id_value,
          event_value,
          rec.sender,
          'DRAFTED',
          state_value,
          null,
          rec.message_id,
          rec.content_hash,
          jsonb_build_object(
            'message_id', rec.message_id,
            'source_refs', to_jsonb(rec.source_refs),
            'assignee_identity_id', assignee_value,
            'workflow_id', workflow_id_value,
            'claim_basis', 'explicit_assignment_envelope_v1'
          ),
          rec.created_at
        )
        returning event_id into new_event_id;

        update radar.assignments
        set current_state = state_value,
            current_event_id = new_event_id,
            updated_at = greatest(updated_at, rec.created_at)
        where assignment_id = assignment_id_value;

        inserted_assignments := inserted_assignments + 1;
        inserted_events := inserted_events + 1;
        progress := true;
        continue;
      end if;

      if not found then
        continue;
      end if;

      -- V1 bindings are immutable inside one assignment chain. A change in
      -- workflow, assignee, or protected authority is a conflict, not a silent
      -- reassignment.
      if assignment_row.workflow_id <> workflow_id_value
         or assignment_row.assignee_identity_id is distinct from assignee_value
         or assignment_row.protected_authority <> protected_value
         or assignment_row.authority_ref is distinct from authority_ref_value
      then
        if not exists (
          select 1
          from radar.reconciliation_events e
          where e.code = 'ASSIGNMENT_EVENT_CONFLICT'
            and e.detail->>'message_id' = rec.message_id
        ) then
          insert into radar.reconciliation_events(code, repair_class, source_ref, detail)
          values (
            'ASSIGNMENT_EVENT_CONFLICT',
            'AMBIGUOUS_DURABLE_CONFLICT',
            source_ref_value,
            jsonb_build_object(
              'message_id', rec.message_id,
              'assignment_id', assignment_id_value,
              'reason', 'immutable_assignment_binding_mismatch',
              'resolution', 'state_not_advanced'
            )
          );
          conflict_observations := conflict_observations + 1;
        end if;
        continue;
      end if;

      select ae.event_id into predecessor_event_id
      from radar.assignment_events ae
      where ae.assignment_id = assignment_id_value
        and ae.operation_id = predecessor_message_value;

      if not found then
        continue;
      end if;

      if assignment_row.current_event_id is distinct from predecessor_event_id then
        if not exists (
          select 1
          from radar.reconciliation_events e
          where e.code = 'ASSIGNMENT_EVENT_CONFLICT'
            and e.detail->>'message_id' = rec.message_id
        ) then
          insert into radar.reconciliation_events(code, repair_class, source_ref, detail)
          values (
            'ASSIGNMENT_EVENT_CONFLICT',
            'AMBIGUOUS_DURABLE_CONFLICT',
            source_ref_value,
            jsonb_build_object(
              'message_id', rec.message_id,
              'assignment_id', assignment_id_value,
              'predecessor_message_id', predecessor_message_value,
              'predecessor_event_id', predecessor_event_id,
              'current_event_id', assignment_row.current_event_id,
              'reason', 'predecessor_is_not_current_assignment_event',
              'resolution', 'state_not_advanced'
            )
          );
          conflict_observations := conflict_observations + 1;
        end if;
        continue;
      end if;

      insert into radar.assignment_events(
        assignment_id,
        event_type,
        actor,
        from_state,
        to_state,
        predecessor_event_id,
        operation_id,
        receipt_sha256,
        detail,
        created_at
      ) values (
        assignment_id_value,
        event_value,
        rec.sender,
        assignment_row.current_state,
        state_value,
        predecessor_event_id,
        rec.message_id,
        rec.content_hash,
        jsonb_build_object(
          'message_id', rec.message_id,
          'source_refs', to_jsonb(rec.source_refs),
          'predecessor_message_id', predecessor_message_value,
          'claim_basis', 'explicit_assignment_envelope_v1'
        ),
        rec.created_at
      )
      on conflict (assignment_id, operation_id) do nothing
      returning event_id into new_event_id;

      if new_event_id is not null then
        update radar.assignments
        set current_state = state_value,
            current_event_id = new_event_id,
            updated_at = greatest(updated_at, rec.created_at)
        where assignment_id = assignment_id_value;

        inserted_events := inserted_events + 1;
        progress := true;
      end if;
      new_event_id := null;
    end loop;

    exit when not progress;
  end loop;

  -- Any otherwise valid successor still lacking an event after the fixed-point
  -- pass is waiting on an explicit predecessor/root that is not yet projected.
  for rec in
    select m.*
    from radar.messages m
    where m.domain = 'chat_bus'
      and m.payload ? 'assignment_id'
      and upper(btrim(coalesce(m.payload->>'assignment_event', ''))) <> 'ASSIGNED'
      and not exists (
        select 1 from radar.assignment_events ae
        where ae.assignment_id = btrim(m.payload->>'assignment_id')
          and ae.operation_id = m.message_id
      )
      and not exists (
        select 1 from radar.reconciliation_events e
        where e.detail->>'message_id' = m.message_id
          and e.code in ('ASSIGNMENT_ENVELOPE_INVALID','ASSIGNMENT_EVENT_CONFLICT')
      )
    order by m.created_at, m.message_id
  loop
    assignment_id_value := btrim(coalesce(rec.payload->>'assignment_id', ''));
    predecessor_message_value := btrim(coalesce(rec.payload->>'assignment_predecessor_message_id', ''));
    source_ref_value := coalesce((rec.source_refs)[1], rec.message_id);

    if not exists (
      select 1
      from radar.reconciliation_events e
      where e.code = 'ASSIGNMENT_EVENT_PENDING_PREDECESSOR'
        and e.detail->>'message_id' = rec.message_id
    ) then
      insert into radar.reconciliation_events(code, repair_class, source_ref, detail)
      values (
        'ASSIGNMENT_EVENT_PENDING_PREDECESSOR',
        'OBSERVATION_ONLY',
        source_ref_value,
        jsonb_build_object(
          'message_id', rec.message_id,
          'assignment_id', assignment_id_value,
          'predecessor_message_id', nullif(predecessor_message_value, ''),
          'resolution', 'await_explicit_predecessor_then_reconcile'
        )
      );
      pending_observations := pending_observations + 1;
    end if;
  end loop;

  return jsonb_build_object(
    'assignments_inserted', inserted_assignments,
    'events_inserted', inserted_events,
    'invalid_observations_inserted', invalid_observations,
    'pending_observations_inserted', pending_observations,
    'conflict_observations_inserted', conflict_observations
  );
end;
$$;

create or replace function radar.bus_assignment_reconcile_after_insert_v1()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, radar
as $$
begin
  perform radar.reconcile_bus_assignments_v1();
  return null;
end;
$$;

drop trigger if exists radar_messages_assignment_projection on radar.messages;
create trigger radar_messages_assignment_projection
after insert on radar.messages
for each statement execute function radar.bus_assignment_reconcile_after_insert_v1();

revoke all on function radar.reconcile_bus_assignments_v1() from public;
revoke all on function radar.reconcile_bus_assignments_v1() from anon;
revoke all on function radar.reconcile_bus_assignments_v1() from authenticated;
grant execute on function radar.reconcile_bus_assignments_v1() to service_role;

revoke all on function radar.bus_assignment_reconcile_after_insert_v1() from public;
revoke all on function radar.bus_assignment_reconcile_after_insert_v1() from anon;
revoke all on function radar.bus_assignment_reconcile_after_insert_v1() from authenticated;
grant execute on function radar.bus_assignment_reconcile_after_insert_v1() to service_role;

select radar.reconcile_bus_assignments_v1();

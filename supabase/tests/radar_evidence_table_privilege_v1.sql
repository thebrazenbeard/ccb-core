begin;

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'delivery_events',
    'acknowledgements',
    'reconciliation_events'
  ]
  loop
    if has_table_privilege('service_role', 'radar.' || table_name, 'INSERT')
       or has_table_privilege('service_role', 'radar.' || table_name, 'UPDATE')
       or has_table_privilege('service_role', 'radar.' || table_name, 'DELETE')
    then
      raise exception 'RADAR_TEST_EVIDENCE_DIRECT_DML_STILL_GRANTED:%', table_name;
    end if;
    if not has_table_privilege('service_role', 'radar.' || table_name, 'SELECT') then
      raise exception 'RADAR_TEST_EVIDENCE_SELECT_MISSING:%', table_name;
    end if;
  end loop;
  if not has_function_privilege(
    'service_role', 'radar.reconcile_bus_receipts_v1()', 'EXECUTE'
  ) then
    raise exception 'RADAR_TEST_RECEIPT_RECONCILE_EXECUTE_MISSING';
  end if;
  if not has_function_privilege(
    'service_role', 'radar.reconcile_bus_assignments_v1()', 'EXECUTE'
  ) then
    raise exception 'RADAR_TEST_ASSIGNMENT_RECONCILE_EXECUTE_MISSING';
  end if;
end
$$;

rollback;

-- Keep derived provider evidence read-only to ordinary service-role callers.

revoke insert, update, delete on table radar.delivery_events from service_role;
grant select on table radar.delivery_events to service_role;

revoke insert, update, delete on table radar.acknowledgements from service_role;
grant select on table radar.acknowledgements to service_role;

revoke insert, update, delete on table radar.reconciliation_events from service_role;
grant select on table radar.reconciliation_events to service_role;

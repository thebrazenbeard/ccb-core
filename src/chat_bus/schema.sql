-- Schema for chat-communication-bus assignment/transition state machine
-- Purpose: store assignments, allowed state transitions, transition events,
-- and final acceptance receipts. Keep triggers and constraints to guarantee
-- projection consistency and legal transitions.
PRAGMA foreign_keys = ON;
PRAGMA recursive_triggers = ON;
PRAGMA trusted_schema = OFF;

-- ======================================================================
-- assignments: projection of current state plus context fields
-- ======================================================================
CREATE TABLE IF NOT EXISTS assignments (
    assignment_id TEXT PRIMARY KEY CHECK (length(assignment_id) > 0),
    workflow_id TEXT NOT NULL CHECK (length(workflow_id) > 0),
    architecture_mode TEXT NOT NULL CHECK (
        architecture_mode IN ('NATIVE_MULTI_AGENT', 'FALLBACK_COORDINATOR_RELAY')
    ),
    current_state TEXT NOT NULL CHECK (
        current_state IN (
            'DRAFTED', 'ASSIGNED', 'ACKNOWLEDGED', 'RUNNING',
            'REVIEW_PENDING', 'PAUSED_USAGE_EXHAUSTED', 'INTERRUPTED',
            'HANDED_OFF', 'CHANGES_REQUESTED', 'STOPPED',
            'CONFIRMED', 'SUPERSEDED'
        )
    ) DEFAULT 'DRAFTED',
    current_transition_event_id INTEGER,
    pre_pause_state TEXT,
    pre_interrupt_state TEXT,
    nested_pause_state TEXT,
    superseded_by_assignment_id TEXT,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),

    FOREIGN KEY (current_transition_event_id) REFERENCES transition_events(event_id) ON DELETE RESTRICT,
    FOREIGN KEY (superseded_by_assignment_id) REFERENCES assignments(assignment_id) ON DELETE SET NULL,

    -- state-dependent invariants:
    CHECK (
        (current_state = 'PAUSED_USAGE_EXHAUSTED' AND pre_pause_state IS NOT NULL)
        OR (current_state <> 'PAUSED_USAGE_EXHAUSTED' AND pre_pause_state IS NULL)
    ),
    CHECK (
        (current_state = 'INTERRUPTED' AND pre_interrupt_state IS NOT NULL)
        OR (current_state <> 'INTERRUPTED' AND pre_interrupt_state IS NULL)
    ),
    CHECK (
        nested_pause_state IS NULL
        OR (current_state = 'INTERRUPTED' AND pre_interrupt_state = 'PAUSED_USAGE_EXHAUSTED')
    ),
    CHECK (superseded_by_assignment_id IS NULL OR current_state = 'SUPERSEDED')
);

-- Useful indexes for common queries
CREATE INDEX IF NOT EXISTS idx_assignments_workflow_id ON assignments (workflow_id);
CREATE INDEX IF NOT EXISTS idx_assignments_current_state ON assignments (current_state);
CREATE INDEX IF NOT EXISTS idx_assignments_superseded_by ON assignments (superseded_by_assignment_id);

-- ======================================================================
-- transition_rules: the allowed transitions and their contextual effects
-- ======================================================================
CREATE TABLE IF NOT EXISTS transition_rules (
    transition_type_id TEXT PRIMARY KEY CHECK (length(transition_type_id) > 0),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('COORDINATOR', 'PRODUCER', 'REVIEWER', 'OPERATOR')),
    context_action TEXT NOT NULL CHECK (
        context_action IN (
            'NONE', 'SET_PAUSE', 'RESUME_PAUSE', 'SET_INTERRUPT',
            'SET_INTERRUPT_FROM_PAUSE', 'RECOVER_INTERRUPT', 'CLEAR_CONTEXT',
            'SUPERSEDE'
        )
    )
);

-- Index for lookups by from_state + to_state + actor (helps validation lookups)
CREATE INDEX IF NOT EXISTS idx_transition_rules_from_to_actor ON transition_rules (from_state, to_state, actor);

-- ======================================================================
-- transition_events: immutable record of transitions (append-only)
-- ======================================================================
CREATE TABLE IF NOT EXISTS transition_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id TEXT NOT NULL CHECK (length(assignment_id) > 0),
    transition_type_id TEXT NOT NULL CHECK (length(transition_type_id) > 0),
    actor TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    predecessor_event_id INTEGER,
    operation_id TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK (
        length(receipt_sha256) = 64
        AND receipt_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    target_assignment_id TEXT,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),

    UNIQUE (assignment_id, operation_id),

    FOREIGN KEY (assignment_id) REFERENCES assignments(assignment_id) ON DELETE RESTRICT,
    FOREIGN KEY (transition_type_id) REFERENCES transition_rules(transition_type_id) ON DELETE RESTRICT,
    FOREIGN KEY (predecessor_event_id) REFERENCES transition_events(event_id) ON DELETE RESTRICT,
    FOREIGN KEY (target_assignment_id) REFERENCES assignments(assignment_id) ON DELETE RESTRICT
);

-- Indexes to speed lookups (e.g., list events for assignment, find predecessor)
CREATE INDEX IF NOT EXISTS idx_transition_events_assignment_id ON transition_events (assignment_id);
CREATE INDEX IF NOT EXISTS idx_transition_events_predecessor_id ON transition_events (predecessor_event_id);

-- ======================================================================
-- final_acceptance_receipts: final acceptance metadata bound to CONFIRMED assignment
-- ======================================================================
CREATE TABLE IF NOT EXISTS final_acceptance_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id TEXT NOT NULL UNIQUE CHECK (length(assignment_id) > 0),
    architecture_mode TEXT NOT NULL CHECK (
        architecture_mode IN ('NATIVE_MULTI_AGENT', 'FALLBACK_COORDINATOR_RELAY')
    ),
    disposition TEXT NOT NULL CHECK (disposition IN ('PASS', 'FAIL')),
    readback_confirmed INTEGER NOT NULL CHECK (readback_confirmed IN (0, 1)),
    evidence_sha256 TEXT NOT NULL CHECK (
        length(evidence_sha256) = 64
        AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),

    FOREIGN KEY (assignment_id) REFERENCES assignments(assignment_id) ON DELETE RESTRICT
);

-- ======================================================================
-- TRIGGERS: validations and projection maintenance
-- Note: Keep the triggers but document the invariants for maintainability.
-- ======================================================================

-- Validate transition events before insert:
-- - transition must be allowed for the assignment's current state and the rule
-- - predecessor must match current_transition_event_id (or both NULL)
-- - predecessor must belong to the same assignment if provided
-- - SUPERSEDE must have a valid target and not self-target
-- - other targeted constraints for resume/recover actions
CREATE TRIGGER IF NOT EXISTS transition_event_validate_before_insert
BEFORE INSERT ON transition_events
BEGIN
    -- legal transition for the assignment and the rule
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM assignments a
        JOIN transition_rules r ON r.transition_type_id = NEW.transition_type_id
        WHERE a.assignment_id = NEW.assignment_id
          AND a.current_state = NEW.from_state
          AND r.from_state = NEW.from_state
          AND r.to_state = NEW.to_state
          AND r.actor = NEW.actor
    ) THEN RAISE(ABORT, 'ILLEGAL_TRANSITION') END;

    -- predecessor must match the current_projection's current_transition_event_id,
    -- supporting fast-fail for stale updates (compare -1 for NULL equivalence)
    SELECT CASE WHEN COALESCE(
        (SELECT current_transition_event_id FROM assignments WHERE assignment_id = NEW.assignment_id),
        -1
    ) <> COALESCE(NEW.predecessor_event_id, -1)
    THEN RAISE(ABORT, 'STALE_PREDECESSOR') END;

    -- predecessor must belong to same assignment
    SELECT CASE WHEN NEW.predecessor_event_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM transition_events p
        WHERE p.event_id = NEW.predecessor_event_id
          AND p.assignment_id = NEW.assignment_id
    ) THEN RAISE(ABORT, 'CROSS_ASSIGNMENT_PREDECESSOR') END;

    -- SUPERSEDE must reference a different existing assignment
    SELECT CASE WHEN NEW.transition_type_id = 'SUPERSEDE' AND (
        NEW.target_assignment_id IS NULL
        OR NEW.target_assignment_id = NEW.assignment_id
        OR NOT EXISTS (
            SELECT 1 FROM assignments t
            WHERE t.assignment_id = NEW.target_assignment_id
        )
    ) THEN RAISE(ABORT, 'INVALID_SUPERSESSION_TARGET') END;

    -- Non-SUPERSEDE must not specify a target assignment
    SELECT CASE WHEN NEW.transition_type_id <> 'SUPERSEDE'
        AND NEW.target_assignment_id IS NOT NULL
    THEN RAISE(ABORT, 'UNEXPECTED_TARGET_ASSIGNMENT') END;

    -- RESUME_PAUSE must restore to the recorded pre_pause_state
    SELECT CASE WHEN (
        SELECT context_action FROM transition_rules
        WHERE transition_type_id = NEW.transition_type_id
    ) = 'RESUME_PAUSE' AND NEW.to_state <> (
        SELECT pre_pause_state FROM assignments WHERE assignment_id = NEW.assignment_id
    ) THEN RAISE(ABORT, 'PAUSE_RESUME_TARGET_MISMATCH') END;

    -- RECOVER_INTERRUPT must target the expected stored pre_interrupt_state
    SELECT CASE WHEN (
        SELECT context_action FROM transition_rules
        WHERE transition_type_id = NEW.transition_type_id
    ) = 'RECOVER_INTERRUPT' AND NEW.to_state <> (
        SELECT CASE
            WHEN pre_interrupt_state = 'PAUSED_USAGE_EXHAUSTED'
                THEN 'PAUSED_USAGE_EXHAUSTED'
            ELSE pre_interrupt_state
        END
        FROM assignments WHERE assignment_id = NEW.assignment_id
    ) THEN RAISE(ABORT, 'INTERRUPT_RECOVERY_TARGET_MISMATCH') END;
END;

-- Guard against accidental mutation of immutable identity columns and projection
-- changes are only allowed when they are backed by a corresponding transition event.
CREATE TRIGGER IF NOT EXISTS assignment_projection_guard_before_update
BEFORE UPDATE ON assignments
BEGIN
    -- identity fields must be immutable
    SELECT CASE WHEN NEW.assignment_id <> OLD.assignment_id
        OR NEW.workflow_id <> OLD.workflow_id
        OR NEW.architecture_mode <> OLD.architecture_mode
        OR NEW.created_at_ms <> OLD.created_at_ms
    THEN RAISE(ABORT, 'IMMUTABLE_ASSIGNMENT_IDENTITY') END;

    -- projection update must be explainable by an existing transition event
    SELECT CASE WHEN (
        NEW.current_state <> OLD.current_state
        OR COALESCE(NEW.current_transition_event_id, -1) <> COALESCE(OLD.current_transition_event_id, -1)
        OR COALESCE(NEW.pre_pause_state, '') <> COALESCE(OLD.pre_pause_state, '')
        OR COALESCE(NEW.pre_interrupt_state, '') <> COALESCE(OLD.pre_interrupt_state, '')
        OR COALESCE(NEW.nested_pause_state, '') <> COALESCE(OLD.nested_pause_state, '')
        OR COALESCE(NEW.superseded_by_assignment_id, '') <> COALESCE(OLD.superseded_by_assignment_id, '')
    ) AND NOT EXISTS (
        SELECT 1 FROM transition_events e
        WHERE e.event_id = NEW.current_transition_event_id
          AND e.assignment_id = OLD.assignment_id
          -- ensure the event's predecessor links as expected to enforce monotonic projection
          AND e.predecessor_event_id IS OLD.current_transition_event_id
          AND e.from_state = OLD.current_state
          AND e.to_state = NEW.current_state
          AND (
              NEW.superseded_by_assignment_id IS OLD.superseded_by_assignment_id
              OR (
                  e.transition_type_id = 'SUPERSEDE'
                  AND NEW.superseded_by_assignment_id = e.target_assignment_id
              )
          )
    ) THEN RAISE(ABORT, 'PROJECTION_REQUIRES_BOUND_TRANSITION') END;
END;

-- After a new transition event is inserted, update the assignments projection.
-- This keeps the projection in sync; complex context changes (pre_pause/pre_interrupt)
-- are set according to the transition_rules' declared context_action.
CREATE TRIGGER IF NOT EXISTS transition_apply_after_insert
AFTER INSERT ON transition_events
BEGIN
    UPDATE assignments
    SET
        current_state = NEW.to_state,
        current_transition_event_id = NEW.event_id,
        pre_pause_state = CASE
            WHEN (SELECT context_action FROM transition_rules WHERE transition_type_id = NEW.transition_type_id) = 'SET_PAUSE'
                THEN NEW.from_state
            WHEN (SELECT context_action FROM transition_rules WHERE transition_type_id = NEW.transition_type_id) = 'RECOVER_INTERRUPT'
                 AND OLD.pre_interrupt_state = 'PAUSED_USAGE_EXHAUSTED'
                THEN OLD.nested_pause_state
            ELSE NULL
        END,
        pre_interrupt_state = CASE
            WHEN (SELECT context_action FROM transition_rules WHERE transition_type_id = NEW.transition_type_id) = 'SET_INTERRUPT'
                THEN NEW.from_state
            WHEN (SELECT context_action FROM transition_rules WHERE transition_type_id = NEW.transition_type_id) = 'SET_INTERRUPT_FROM_PAUSE'
                THEN 'PAUSED_USAGE_EXHAUSTED'
            ELSE NULL
        END,
        nested_pause_state = CASE
            WHEN (SELECT context_action FROM transition_rules WHERE transition_type_id = NEW.transition_type_id) = 'SET_INTERRUPT_FROM_PAUSE'
                THEN OLD.pre_pause_state
            ELSE NULL
        END,
        superseded_by_assignment_id = CASE
            WHEN NEW.transition_type_id = 'SUPERSEDE' THEN NEW.target_assignment_id
            ELSE OLD.superseded_by_assignment_id
        END
    FROM assignments AS OLD
    WHERE assignments.assignment_id = NEW.assignment_id
      AND OLD.assignment_id = NEW.assignment_id;
END;

-- Validate final acceptance receipts are only inserted for CONFIRMED assignments
-- and PASS requires readback_confirmed = 1
CREATE TRIGGER IF NOT EXISTS final_acceptance_validate_before_insert
BEFORE INSERT ON final_acceptance_receipts
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM assignments a
        WHERE a.assignment_id = NEW.assignment_id
          AND a.current_state = 'CONFIRMED'
          AND a.architecture_mode = NEW.architecture_mode
    ) THEN RAISE(ABORT, 'ACCEPTANCE_ASSIGNMENT_MODE_OR_STATE_MISMATCH') END;

    SELECT CASE WHEN NEW.disposition = 'PASS' AND NEW.readback_confirmed <> 1
    THEN RAISE(ABORT, 'PASS_REQUIRES_READBACK') END;
END;

-- ======================================================================
-- Seed the transition_rules (insert in a single transaction)
-- ======================================================================
BEGIN TRANSACTION;
INSERT OR IGNORE INTO transition_rules
(transition_type_id, from_state, to_state, actor, context_action)
VALUES
('ASSIGN', 'DRAFTED', 'ASSIGNED', 'COORDINATOR', 'NONE'),
('ACKNOWLEDGE', 'ASSIGNED', 'ACKNOWLEDGED', 'PRODUCER', 'NONE'),
('START', 'ACKNOWLEDGED', 'RUNNING', 'PRODUCER', 'NONE'),
('REQUEST_REVIEW', 'RUNNING', 'REVIEW_PENDING', 'PRODUCER', 'NONE'),
('CONFIRM', 'REVIEW_PENDING', 'CONFIRMED', 'REVIEWER', 'CLEAR_CONTEXT'),
('CHANGES_REQUEST', 'REVIEW_PENDING', 'CHANGES_REQUESTED', 'REVIEWER', 'CLEAR_CONTEXT'),
('CORRECT', 'CHANGES_REQUESTED', 'RUNNING', 'PRODUCER', 'CLEAR_CONTEXT'),
('HANDOFF', 'RUNNING', 'HANDED_OFF', 'PRODUCER', 'NONE'),
('REOPEN_HANDOFF', 'HANDED_OFF', 'RUNNING', 'COORDINATOR', 'NONE'),
('PAUSE_FROM_ACKNOWLEDGED', 'ACKNOWLEDGED', 'PAUSED_USAGE_EXHAUSTED', 'OPERATOR', 'SET_PAUSE'),
('PAUSE_FROM_RUNNING', 'RUNNING', 'PAUSED_USAGE_EXHAUSTED', 'OPERATOR', 'SET_PAUSE'),
('PAUSE_FROM_REVIEW_PENDING', 'REVIEW_PENDING', 'PAUSED_USAGE_EXHAUSTED', 'OPERATOR', 'SET_PAUSE'),
('RESUME_TO_ACKNOWLEDGED', 'PAUSED_USAGE_EXHAUSTED', 'ACKNOWLEDGED', 'OPERATOR', 'RESUME_PAUSE'),
('RESUME_TO_RUNNING', 'PAUSED_USAGE_EXHAUSTED', 'RUNNING', 'OPERATOR', 'RESUME_PAUSE'),
('RESUME_TO_REVIEW_PENDING', 'PAUSED_USAGE_EXHAUSTED', 'REVIEW_PENDING', 'OPERATOR', 'RESUME_PAUSE'),
('INTERRUPT_FROM_ACKNOWLEDGED', 'ACKNOWLEDGED', 'INTERRUPTED', 'OPERATOR', 'SET_INTERRUPT'),
('INTERRUPT_FROM_RUNNING', 'RUNNING', 'INTERRUPTED', 'OPERATOR', 'SET_INTERRUPT'),
('INTERRUPT_FROM_REVIEW_PENDING', 'REVIEW_PENDING', 'INTERRUPTED', 'OPERATOR', 'SET_INTERRUPT'),
('INTERRUPT_FROM_PAUSED', 'PAUSED_USAGE_EXHAUSTED', 'INTERRUPTED', 'OPERATOR', 'SET_INTERRUPT_FROM_PAUSE'),
('RECOVER_TO_ACKNOWLEDGED', 'INTERRUPTED', 'ACKNOWLEDGED', 'OPERATOR', 'RECOVER_INTERRUPT'),
('RECOVER_TO_RUNNING', 'INTERRUPTED', 'RUNNING', 'OPERATOR', 'RECOVER_INTERRUPT'),
('RECOVER_TO_REVIEW_PENDING', 'INTERRUPTED', 'REVIEW_PENDING', 'OPERATOR', 'RECOVER_INTERRUPT'),
('RECOVER_TO_PAUSED', 'INTERRUPTED', 'PAUSED_USAGE_EXHAUSTED', 'OPERATOR', 'RECOVER_INTERRUPT'),
('STOP_FROM_DRAFTED', 'DRAFTED', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('STOP_FROM_ASSIGNED', 'ASSIGNED', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('STOP_FROM_ACKNOWLEDGED', 'ACKNOWLEDGED', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('STOP_FROM_RUNNING', 'RUNNING', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('STOP_FROM_REVIEW_PENDING', 'REVIEW_PENDING', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('STOP_FROM_PAUSED', 'PAUSED_USAGE_EXHAUSTED', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('STOP_FROM_INTERRUPTED', 'INTERRUPTED', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('STOP_FROM_HANDED_OFF', 'HANDED_OFF', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('STOP_FROM_CHANGES_REQUESTED', 'CHANGES_REQUESTED', 'STOPPED', 'OPERATOR', 'CLEAR_CONTEXT'),
('SUPERSEDE', 'DRAFTED', 'SUPERSEDED', 'COORDINATOR', 'SUPERSEDE');
COMMIT;

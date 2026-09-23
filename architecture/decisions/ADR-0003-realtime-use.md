# ADR-0003 — Realtime accelerates Radar; it does not define truth

Status: ACCEPTED — 2026-09-02

Radar uses durable database rows for control state, Realtime Broadcast/Presence where appropriate for ephemeral live routing/liveness, and Postgres Changes selectively for durable projection updates. Every Realtime-dependent path must tolerate disconnect/reconnect and reconcile from durable state.

Priority changes scheduling only. Presence changes node liveness only. Neither grants authority or changes identity existence. High-volume fanout should prefer Broadcast over making every client an RLS-checked Postgres Changes subscriber when practical.
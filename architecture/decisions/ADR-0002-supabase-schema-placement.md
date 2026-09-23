# ADR-0002 — Radar V1 uses an isolated `radar` schema in the alpha Supabase project

Status: ACCEPTED — 2026-09-02

V1 targets the existing healthy alpha Supabase project but uses a semantically isolated `radar` schema. This avoids another provider project while keeping cross-system observation nearby. Radar data is not alpha memory, identity state, or authority.

The schema must not rely on permissive public self-registration. No secrets enter Git. Provider application is source-bound to a Git migration and verified by readback. A future dedicated project remains possible if operational isolation, cost, or scale warrants it.
/**
 * Entry point for the Supabase Edge Function `github-bus-ingest`.
 *
 * Intentionally imports ./handler.ts for its side effects (handler.ts calls Deno.serve()).
 * This explicit comment prevents accidental removal and clarifies intent for bundlers and maintainers.
 */
import "./handler.ts";

export {};

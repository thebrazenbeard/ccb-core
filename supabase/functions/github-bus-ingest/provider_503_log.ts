/** Redacted, execution-correlatable diagnostic for handled provider 503s. */

const PROVIDER_503_CODES = new Set([
  "OIDC_JWKS_UNAVAILABLE",
  "OIDC_JWKS_INVALID",
  "TRUSTED_PROJECTOR_SHA_INVALID",
  "PROJECT_CONCURRENCY_INVALID",
  "PROVIDER_CONFIGURATION_MISSING",
]);

export function provider503LogEvent(status: number, code: unknown): string | null {
  if (status !== 503) return null;
  const safeCode = typeof code === "string" && PROVIDER_503_CODES.has(code)
    ? code
    : "UNKNOWN";
  return `RADAR_PROVIDER_503:${safeCode}`;
}

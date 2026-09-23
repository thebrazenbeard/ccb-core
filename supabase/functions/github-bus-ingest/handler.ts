import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const OIDC_ISSUER = "https://token.actions.githubusercontent.com";
const JWKS_URL = `${OIDC_ISSUER}/.well-known/jwks`;
const AUDIENCE_PREFIX = "chat-communication-bus-supabase:";
const EXPECTED_REPOSITORY = "example/ccb-core";
const EXPECTED_REPOSITORY_ID = "1324505476";
const EXPECTED_REPOSITORY_OWNER_ID = "234958733";
const EXPECTED_CALLER_WORKFLOW_REF = `${EXPECTED_REPOSITORY}/.github/workflows/radar-central-dispatcher.yml@refs/heads/main`;
const EXPECTED_JOB_WORKFLOW_PATH = `${EXPECTED_REPOSITORY}/.github/workflows/radar-trusted-projector.yml`;
const MAX_FILES = 500;
const MAX_FILE_BYTES = 512 * 1024;
const MAX_BATCH_BYTES = 5 * 1024 * 1024;
const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
const DEFAULT_PROJECT_CONCURRENCY = 4;
const MAX_PROJECT_CONCURRENCY = 16;
const JWKS_CACHE_TTL_MS = 10 * 60 * 1000;
const JWKS_REFRESH_FLOOR_MS = 60 * 1000;
const SHA40_RE = /^[0-9a-f]{40}$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const PATH_SEGMENT_RE = /^[A-Za-z0-9._-]+$/;

type GithubJwk = JsonWebKey & { kid?: string };
type RpcError = { code?: string } | null;
type RadarRpcResult = { data: unknown; error: RpcError };
type RadarRpcClient = {
  rpc(name: string, args?: Record<string, unknown>): PromiseLike<RadarRpcResult>;
};

let jwksCache: { expiresAt: number; keys: GithubJwk[] } | null = null;
let jwksFetchPromise: Promise<GithubJwk[]> | null = null;
let jwksLastFetchAttemptAt: number | null = null;

type Claims = Record<string, unknown>;
type Headers = Record<string, string>;
type SourceFile = {
  path: string;
  source_commit: string;
  source_committed_at: string;
  blob_sha: string;
  content_base64: string;
};
type ProjectionBatch = {
  repository: string;
  branch: string;
  ref: string;
  branch_head: string;
  lane_identity: string;
  batch_id: string;
  claim_token: string;
  files: SourceFile[];
};

class HttpError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

function response(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function requireString(value: unknown, code: string): string {
  if (typeof value !== "string" || !value.trim()) throw new HttpError(422, code);
  return value.trim();
}

function requireSha40(value: unknown, code: string): string {
  const text = requireString(value, code).toLowerCase();
  if (!SHA40_RE.test(text)) throw new HttpError(422, code);
  return text;
}

function requireObject(value: unknown, code: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new HttpError(422, code);
  return value as Record<string, unknown>;
}

function b64urlBytes(value: string): Uint8Array {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
  try {
    const binary = atob(padded);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  } catch {
    throw new HttpError(401, "OIDC_BASE64_INVALID");
  }
}

function b64urlJson(value: string): Record<string, unknown> {
  try {
    return JSON.parse(new TextDecoder().decode(b64urlBytes(value)));
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(401, "OIDC_JSON_INVALID");
  }
}

async function getJwks(forceRefresh = false): Promise<GithubJwk[]> {
  const now = Date.now();
  if (!forceRefresh && jwksCache && jwksCache.expiresAt > now) return jwksCache.keys;
  if (jwksFetchPromise) {
    const keys = await jwksFetchPromise;
    if (keys) return keys;
  }

  if (
    jwksLastFetchAttemptAt !== null &&
    now - jwksLastFetchAttemptAt < JWKS_REFRESH_FLOOR_MS
  ) {
    if (jwksCache && jwksCache.expiresAt > now) return jwksCache.keys;
    throw new HttpError(503, "OIDC_JWKS_UNAVAILABLE");
  }

  jwksLastFetchAttemptAt = now;
  jwksFetchPromise = (async () => {
    try {
      const result = await fetch(JWKS_URL, { headers: { accept: "application/json" } });
      if (!result.ok) throw new HttpError(503, "OIDC_JWKS_UNAVAILABLE");
      const data = await result.json();
      if (!data || !Array.isArray(data.keys)) throw new HttpError(503, "OIDC_JWKS_INVALID");
      jwksCache = { expiresAt: Date.now() + JWKS_CACHE_TTL_MS, keys: data.keys as GithubJwk[] };
      return jwksCache.keys;
    } finally {
      jwksFetchPromise = null;
    }
  })();

  return jwksFetchPromise!;
}

function stringClaim(claims: Claims, name: string): string {
  const value = claims[name];
  if (typeof value !== "string" || !value) throw new HttpError(403, `OIDC_${name.toUpperCase()}_MISSING`);
  return value;
}

function audienceIncludes(value: unknown, expected: string): boolean {
  if (typeof value === "string") return value === expected;
  return Array.isArray(value) && value.some((item) => item === expected);
}

function trustedProjectorSha(): string {
  const value = Deno.env.get("RADAR_TRUSTED_PROJECTOR_SHA") ?? "";
  if (!SHA40_RE.test(value)) throw new HttpError(503, "TRUSTED_PROJECTOR_SHA_INVALID");
  return value;
}

async function verifyGithubOidc(token: string, expectedAudience: string): Promise<Claims> {
  const parts = token.split(".");
  if (parts.length !== 3) throw new HttpError(401, "OIDC_TOKEN_SHAPE_INVALID");
  const header = b64urlJson(parts[0]);
  const claims = b64urlJson(parts[1]);
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw new HttpError(401, "OIDC_ALGORITHM_INVALID");

  let jwks = await getJwks();
  let jwk = jwks.find((candidate) => candidate.kid === header.kid);
  if (!jwk) {
    jwks = await getJwks(true);
    jwk = jwks.find((candidate) => candidate.kid === header.kid);
  }
  if (!jwk) throw new HttpError(401, "OIDC_KEY_NOT_FOUND");
  const key = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const ok = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    toArrayBuffer(b64urlBytes(parts[2])),
    toArrayBuffer(new TextEncoder().encode(`${parts[0]}.${parts[1]}`)),
  );
  if (!ok) throw new HttpError(401, "OIDC_SIGNATURE_INVALID");

  const now = Math.floor(Date.now() / 1000);
  if (claims.iss !== OIDC_ISSUER) throw new HttpError(403, "OIDC_ISSUER_INVALID");
  if (!audienceIncludes(claims.aud, expectedAudience)) throw new HttpError(403, "OIDC_AUDIENCE_INVALID");
  if (typeof claims.exp !== "number" || claims.exp <= now) throw new HttpError(403, "OIDC_EXPIRED");
  if (typeof claims.nbf === "number" && claims.nbf > now + 30) throw new HttpError(403, "OIDC_NOT_YET_VALID");
  if (typeof claims.iat !== "number" || claims.iat > now + 30 || claims.iat < now - 15 * 60) {
    throw new HttpError(403, "OIDC_ISSUED_AT_INVALID");
  }

  stringClaim(claims, "jti");
  if (stringClaim(claims, "repository") !== EXPECTED_REPOSITORY) throw new HttpError(403, "OIDC_REPOSITORY_INVALID");
  if (stringClaim(claims, "repository_id") !== EXPECTED_REPOSITORY_ID) {
    throw new HttpError(403, "OIDC_REPOSITORY_ID_INVALID");
  }
  if (stringClaim(claims, "repository_owner_id") !== EXPECTED_REPOSITORY_OWNER_ID) {
    throw new HttpError(403, "OIDC_REPOSITORY_OWNER_ID_INVALID");
  }
  const eventName = stringClaim(claims, "event_name");
  if (eventName !== "workflow_run" && eventName !== "schedule") throw new HttpError(403, "OIDC_EVENT_INVALID");
  if (stringClaim(claims, "workflow_ref") !== EXPECTED_CALLER_WORKFLOW_REF) {
    throw new HttpError(403, "OIDC_WORKFLOW_INVALID");
  }
  if (stringClaim(claims, "ref") !== "refs/heads/main") throw new HttpError(403, "OIDC_REF_INVALID");

  const expectedSha = trustedProjectorSha();
  const expectedJobRef = `${EXPECTED_JOB_WORKFLOW_PATH}@${expectedSha}`;
  if (stringClaim(claims, "job_workflow_ref") !== expectedJobRef) {
    throw new HttpError(403, "OIDC_JOB_WORKFLOW_INVALID");
  }
  if (stringClaim(claims, "job_workflow_sha") !== expectedSha) {
    throw new HttpError(403, "OIDC_JOB_WORKFLOW_SHA_INVALID");
  }
  return claims;
}

async function consumeGithubOidcJti(
  supabase: RadarRpcClient,
  claims: Claims,
): Promise<void> {
  const jti = stringClaim(claims, "jti");
  const exp = claims.exp;
  if (typeof exp !== "number" || !Number.isFinite(exp)) throw new HttpError(403, "OIDC_EXP_INVALID");
  const { data, error } = await supabase.rpc("radar_consume_github_oidc_jti_v1", {
    p_jti: jti,
    p_expires_at: new Date(exp * 1000).toISOString(),
  });
  if (error) throw new HttpError(502, `OIDC_JTI_GUARD_FAILED:${error.code ?? "UNKNOWN"}`);
  const outcome = String(data);
  if (outcome === "REPLAY") throw new HttpError(403, "OIDC_JTI_REPLAYED");
  if (outcome !== "CONSUMED") throw new HttpError(502, "OIDC_JTI_GUARD_INVALID_RESPONSE");
}

function hex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}

async function sha256(bytes: Uint8Array): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", toArrayBuffer(bytes)));
}

async function readRequestBodyBounded(req: Request, maxBytes: number): Promise<Uint8Array> {
  const reader = req.body?.getReader();
  if (!reader) return new Uint8Array();

  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        try {
          await reader.cancel();
        } catch {
          // Best-effort cancellation only; the size violation remains authoritative.
        }
        throw new HttpError(413, "REQUEST_TOO_LARGE");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

async function gitBlobSha1(bytes: Uint8Array): Promise<string> {
  const prefix = new TextEncoder().encode(`blob ${bytes.length}\0`);
  const combined = new Uint8Array(prefix.length + bytes.length);
  combined.set(prefix);
  combined.set(bytes, prefix.length);
  return hex(await crypto.subtle.digest("SHA-1", toArrayBuffer(combined)));
}

function base64Bytes(value: string): Uint8Array {
  try {
    const binary = atob(value);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  } catch {
    throw new HttpError(422, "SOURCE_CONTENT_BASE64_INVALID");
  }
}

function estimatedBase64Size(base64: string): number {
  const len = base64.length;
  if (len === 0) return 0;
  const padding = base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0;
  return Math.floor((len * 3) / 4) - padding;
}

function unwrapScalar(value: string): string {
  const text = value.trim();
  for (const pair of [["`", "`"], ['"', '"'], ["'", "'"]] as const) {
    if (text.length >= 2 && text.startsWith(pair[0]) && text.endsWith(pair[1])) return text.slice(1, -1).trim();
  }
  return text;
}

function normalizeIdentity(value: string): string {
  const key = unwrapScalar(value).toLowerCase();
  if (["beta-legacy"].includes(key)) return "beta";
  if (["alpha/core", "alpha/core", "alpha-core"].includes(key)) return "alpha";
  if (key.startsWith("epsilon / fabulous nitpicking gremlin")) return "epsilon";
  return key;
}

function laneIdentityFromRef(ref: string): string | null {
  const match = /^refs\/heads\/bus\/(.+)-v\d+$/.exec(ref);
  if (!match) return null;
  const rawIdentity = match[1];
  if (!/^[a-z0-9-]+$/.test(rawIdentity)) return null;
  return normalizeIdentity(rawIdentity);
}

function parseBool(value: string | undefined, code: string): boolean {
  if (value === undefined || value.trim() === "") return false;
  const normalized = unwrapScalar(value).toLowerCase();
  if (["true", "yes", "1"].includes(normalized)) return true;
  if (["false", "no", "0"].includes(normalized)) return false;
  throw new HttpError(422, code);
}

function normalizedTimestamp(value: string): string | null {
  let candidate = unwrapScalar(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(candidate)) candidate = `${candidate}T00:00:00Z`;
  else if (!/(Z|[+-]\d{2}:\d{2})$/i.test(candidate)) return null;
  return Number.isFinite(Date.parse(candidate)) ? candidate : null;
}

function lineRecords(text: string): { content: string; start: number; end: number }[] {
  const records: { content: string; start: number; end: number }[] = [];
  let start = 0;
  while (start < text.length) {
    const newline = text.indexOf("\n", start);
    const end = newline === -1 ? text.length : newline + 1;
    let content = text.slice(start, newline === -1 ? text.length : newline);
    if (content.endsWith("\r")) content = content.slice(0, -1);
    records.push({ content, start, end });
    start = end;
  }
  if (text.length === 0) records.push({ content: "", start: 0, end: 0 });
  return records;
}

function putHeader(headers: Headers, raw: string): boolean {
  const match = /^([A-Za-z0-9_-]+)\s*:\s*(.*)$/.exec(raw);
  if (!match) return false;
  const key = match[1].toLowerCase().replaceAll("-", "_");
  if (Object.prototype.hasOwnProperty.call(headers, key)) throw new HttpError(422, `DUPLICATE_HEADER_${key.toUpperCase()}`);
  headers[key] = match[2].trim();
  return true;
}

function splitYamlFrontmatter(text: string): { headers: Headers; body: string } | null {
  const lines = lineRecords(text);
  let index = 0;
  while (index < lines.length && lines[index].content.trim() === "") index += 1;
  if (lines[index]?.content.trim() !== "---") return null;

  const headers: Headers = {};
  index += 1;
  for (; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.content.trim();
    if (trimmed === "---") {
      let bodyStart = line.end;
      index += 1;
      while (index < lines.length && lines[index].content.trim() === "") {
        bodyStart = lines[index].end;
        index += 1;
      }
      if (Object.keys(headers).length === 0) throw new HttpError(422, "HEADER_BLOCK_REQUIRED");
      return { headers, body: text.slice(bodyStart) };
    }
    if (line.content.startsWith(" ") || line.content.startsWith("\t") || trimmed.startsWith("-")) continue;
    if (trimmed !== "" && !putHeader(headers, line.content)) throw new HttpError(422, "INVALID_FRONTMATTER_HEADER");
  }
  throw new HttpError(422, "UNTERMINATED_FRONTMATTER");
}

function splitDocument(text: string): { headers: Headers; body: string } {
  const yaml = splitYamlFrontmatter(text);
  if (yaml) return yaml;

  const lines = lineRecords(text);
  let index = 0;
  while (index < lines.length && lines[index].content.trim() === "") index += 1;
  if (lines[index]?.content.trimStart().startsWith("#")) {
    index += 1;
    while (index < lines.length && lines[index].content.trim() === "") index += 1;
  }

  const headers: Headers = {};
  let started = false;
  let bodyStart = text.length;
  for (; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.content.trim() === "") {
      if (started) {
        bodyStart = line.end;
        break;
      }
      continue;
    }
    if (!putHeader(headers, line.content)) {
      if (!started) throw new HttpError(422, "HEADER_BLOCK_REQUIRED");
      bodyStart = line.start;
      break;
    }
    started = true;
  }
  if (!started) throw new HttpError(422, "HEADER_BLOCK_REQUIRED");
  return { headers, body: text.slice(bodyStart) };
}

function conversationClosedBySender(body: string): boolean {
  const trimmed = body.trimEnd();
  if (!trimmed) return false;
  const lastLine = trimmed.slice(trimmed.lastIndexOf("\n") + 1).replace(/\r$/, "");
  return lastLine === "#ENDTHREAD";
}

function intendedRecipient(headers: Headers): string {
  const singular = headers.intended_recipient;
  const plural = headers.intended_recipients;
  if (singular !== undefined && plural !== undefined) {
    throw new HttpError(422, "CONFLICTING_INTENDED_RECIPIENT_HEADERS");
  }
  return unwrapScalar(singular ?? plural ?? "");
}

function parseProjectConcurrency(value: string | undefined): number {
  const raw = (value ?? String(DEFAULT_PROJECT_CONCURRENCY)).trim();
  if (!/^\d+$/.test(raw)) throw new HttpError(503, "PROJECT_CONCURRENCY_INVALID");
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > MAX_PROJECT_CONCURRENCY) {
    throw new HttpError(503, "PROJECT_CONCURRENCY_INVALID");
  }
  return parsed;
}

async function mapWithConcurrency<T, R>(items: T[], fn: (t: T, i: number) => Promise<R>, concurrency: number): Promise<R[]> {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > MAX_PROJECT_CONCURRENCY) {
    throw new HttpError(503, "PROJECT_CONCURRENCY_INVALID");
  }
  const results = new Array<R>(items.length);
  let i = 0;
  const workers = new Array(Math.min(concurrency, items.length)).fill(0).map(async () => {
    while (true) {
      const idx = i++;
      if (idx >= items.length) break;
      results[idx] = await fn(items[idx], idx);
    }
  });
  await Promise.all(workers);
  return results;
}

function parseSourceFiles(value: unknown): SourceFile[] {
  if (!Array.isArray(value) || value.length > MAX_FILES) throw new HttpError(422, "BATCH_FILES_INVALID");
  return value.map((item): SourceFile => {
    const file = requireObject(item, "SOURCE_FILE_INVALID");
    const path = requireString(file.path, "SOURCE_PATH_REQUIRED");
    if (!path.startsWith("messages/") || !path.endsWith(".md") || path.includes("..")) {
      throw new HttpError(422, "SOURCE_PATH_INVALID");
    }
    const segments = path.split("/").slice(1);
    if (segments.length === 0 || segments.some((segment) => !PATH_SEGMENT_RE.test(segment))) {
      throw new HttpError(422, "SOURCE_PATH_INVALID");
    }
    return {
      path,
      source_commit: requireSha40(file.source_commit, "SOURCE_GIT_SHA_INVALID"),
      source_committed_at: requireString(file.source_committed_at, "SOURCE_COMMITTED_AT_REQUIRED"),
      blob_sha: requireSha40(file.blob_sha, "SOURCE_GIT_SHA_INVALID"),
      content_base64: requireString(file.content_base64, "SOURCE_CONTENT_REQUIRED"),
    };
  });
}

function parseProjectionBatch(raw: Record<string, unknown>): ProjectionBatch {
  const repository = requireString(raw.repository, "BATCH_REPOSITORY_REQUIRED");
  if (repository !== EXPECTED_REPOSITORY) throw new HttpError(403, "BATCH_REPOSITORY_MISMATCH");
  const branch = requireString(raw.branch, "BATCH_BRANCH_REQUIRED");
  const ref = requireString(raw.ref, "BATCH_REF_REQUIRED");
  if (ref !== `refs/heads/${branch}`) throw new HttpError(403, "BATCH_REF_MISMATCH");
  const branchHead = requireSha40(raw.branch_head, "BATCH_HEAD_REQUIRED");
  const laneIdentity = normalizeIdentity(requireString(raw.lane_identity, "LANE_IDENTITY_REQUIRED"));
  const identity = normalizeIdentity(requireString(raw.identity, "IDENTITY_REQUIRED"));
  if (identity !== laneIdentity) throw new HttpError(403, "LANE_IDENTITY_MISMATCH");
  const refIdentity = laneIdentityFromRef(ref);
  if (!refIdentity || refIdentity !== laneIdentity) throw new HttpError(403, "LANE_IDENTITY_REF_MISMATCH");
  const journal = requireObject(raw.journal, "JOURNAL_REQUIRED");
  const batchId = requireString(journal.batch_id, "JOURNAL_BATCH_ID_REQUIRED");
  const claimToken = requireString(journal.claim_token, "JOURNAL_CLAIM_TOKEN_REQUIRED");
  const files = parseSourceFiles(raw.files);
  return {
    repository,
    branch,
    ref,
    branch_head: branchHead,
    lane_identity: laneIdentity,
    batch_id: batchId,
    claim_token: claimToken,
    files,
  };
}

function projectionResultState(result: string): "PROJECTED" | "IDEMPOTENT" {
  if (result === "INSERTED" || result.startsWith("CONFLICT_INSERTED:")) return "PROJECTED";
  if (result === "IDEMPOTENT" || result.startsWith("CONFLICT_IDEMPOTENT:")) return "IDEMPOTENT";
  throw new HttpError(502, "PROJECTION_RPC_RESULT_INVALID");
}

async function projectFile(
  supabase: RadarRpcClient,
  batch: ProjectionBatch,
  file: SourceFile,
): Promise<{ path: string; result: string }> {
  const bytes = base64Bytes(file.content_base64);
  if (bytes.length > MAX_FILE_BYTES) throw new HttpError(422, "MESSAGE_FILE_TOO_LARGE");
  if (await gitBlobSha1(bytes) !== file.blob_sha) throw new HttpError(422, "GIT_BLOB_SHA_MISMATCH");

  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new HttpError(422, "MESSAGE_NOT_UTF8");
  }

  const { headers, body } = splitDocument(text);
  const messageId = unwrapScalar(requireString(headers.message_id, "MESSAGE_ID_REQUIRED"));
  const writer = normalizeIdentity(requireString(headers.writer, "WRITER_REQUIRED"));
  if (writer !== batch.lane_identity) throw new HttpError(422, "WRITER_LANE_MISMATCH");

  const sourceTime = normalizedTimestamp(file.source_committed_at);
  if (!sourceTime) throw new HttpError(422, "INVALID_SOURCE_COMMITTED_AT");
  let createdAt = sourceTime;
  let createdAtBasis = "source_commit";
  if (headers.created_at !== undefined) {
    const declaredTime = normalizedTimestamp(headers.created_at);
    if (!declaredTime) throw new HttpError(422, "INVALID_CREATED_AT");
    createdAt = declaredTime;
    createdAtBasis = "message_header";
  }

  const intended = intendedRecipient(headers);
  const audience = intended
    .split(",")
    .map((value) => unwrapScalar(value))
    .filter(Boolean)
    .map(normalizeIdentity);
  const requiresAck = parseBool(headers.requires_reply, "INVALID_REQUIRES_REPLY") ||
    parseBool(headers.requires_ack, "INVALID_REQUIRES_ACK");

  const bodyBytes = new TextEncoder().encode(body);
  const fullHash = await sha256(bytes);
  const bodyHash = await sha256(bodyBytes);
  const sourceRefs = [
    `github://${batch.repository}@${file.source_commit}/${file.path}`,
    `github-ref://${batch.repository}/${batch.ref}@${batch.branch_head}`,
    `git-blob:${file.blob_sha}`,
  ];
  const metadata: Record<string, unknown> = {
    ...headers,
    admitted_lane_identity: batch.lane_identity,
    created_at_basis: createdAtBasis,
    source_repository: batch.repository,
    source_ref: batch.ref,
    source_branch_head: batch.branch_head,
    source_path: file.path,
    source_commit: file.source_commit,
    source_blob_sha: file.blob_sha,
    body_sha256: bodyHash,
    body_length: bodyBytes.length,
    conversation_closed_by_sender: conversationClosedBySender(body),
  };

  const { data, error } = await supabase.rpc("radar_projection_project_file_v1", {
    p_batch_id: batch.batch_id,
    p_claim_token: batch.claim_token,
    p_path: file.path,
    p_message_id: messageId,
    p_created_at: createdAt,
    p_sender: batch.lane_identity,
    p_audience: audience,
    p_requires_ack: requiresAck,
    p_source_refs: sourceRefs,
    p_content_hash: fullHash,
    p_idempotency_key: `chat-bus:${messageId}:${fullHash}`,
    p_payload: metadata,
  });
  if (error) throw new HttpError(502, `PROJECTION_FILE_RPC_FAILED:${error.code ?? "UNKNOWN"}`);

  const result = String(data);
  projectionResultState(result);
  return { path: file.path, result };
}

async function handleOperation(
  supabase: RadarRpcClient,
  raw: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (raw.protocol_version !== 2) throw new HttpError(422, "PROTOCOL_VERSION_INVALID");
  const operation = requireString(raw.operation, "OPERATION_REQUIRED");

  if (operation === "cursor") {
    const identity = normalizeIdentity(requireString(raw.identity, "IDENTITY_REQUIRED"));
    const branch = requireString(raw.branch, "BRANCH_REQUIRED");
    const { data, error } = await supabase.rpc("radar_projection_get_lane_v1", {
      p_identity_id: identity,
      p_branch: branch,
    });
    if (error) throw new HttpError(502, `JOURNAL_CURSOR_FAILED:${error.code ?? "UNKNOWN"}`);
    return { ok: true, lane: data ?? null };
  }

  if (operation === "set_control_cut") {
    const identity = normalizeIdentity(requireString(raw.identity, "IDENTITY_REQUIRED"));
    const branch = requireString(raw.branch, "BRANCH_REQUIRED");
    const controlSha = requireSha40(raw.control_sha, "CONTROL_SHA_INVALID");
    const topologySha = requireSha40(raw.topology_sha, "TOPOLOGY_SHA_INVALID");
    const { error } = await supabase.rpc("radar_projection_set_control_cut_v1", {
      p_identity_id: identity,
      p_branch: branch,
      p_control_sha: controlSha,
      p_topology_sha: topologySha,
    });
    if (error) throw new HttpError(502, `JOURNAL_CONTROL_CUT_FAILED:${error.code ?? "UNKNOWN"}`);
    return { ok: true };
  }

  if (operation === "claim_batch") {
    const identity = normalizeIdentity(requireString(raw.identity, "IDENTITY_REQUIRED"));
    const branch = requireString(raw.branch, "BRANCH_REQUIRED");
    const expected = raw.expected_projected_head === null
      ? null
      : requireSha40(raw.expected_projected_head, "EXPECTED_HEAD_INVALID");
    const target = requireSha40(raw.target_head, "TARGET_HEAD_INVALID");
    const controlSha = requireSha40(raw.control_sha, "CONTROL_SHA_INVALID");
    const topologySha = requireSha40(raw.topology_sha, "TOPOLOGY_SHA_INVALID");
    if (!Array.isArray(raw.files) || raw.files.some((path) => typeof path !== "string" || !path)) {
      throw new HttpError(422, "CLAIM_FILES_INVALID");
    }
    const owner = requireString(raw.owner, "CLAIM_OWNER_REQUIRED");
    const leaseSeconds = raw.lease_seconds;
    if (!Number.isInteger(leaseSeconds) || (leaseSeconds as number) <= 0 || (leaseSeconds as number) > 3600) {
      throw new HttpError(422, "CLAIM_LEASE_INVALID");
    }
    const { data, error } = await supabase.rpc("radar_projection_claim_batch_v1", {
      p_identity_id: identity,
      p_branch: branch,
      p_expected_projected_head_sha: expected,
      p_target_head_sha: target,
      p_control_sha: controlSha,
      p_topology_sha: topologySha,
      p_files: raw.files,
      p_claim_owner: owner,
      p_lease_seconds: leaseSeconds,
    });
    if (error) throw new HttpError(409, `JOURNAL_CLAIM_FAILED:${error.code ?? "UNKNOWN"}`);
    return { ok: true, batch: data };
  }

  if (operation === "project_batch") {
    const batch = parseProjectionBatch(raw);
    let totalBytes = 0;
    for (const file of batch.files) {
      totalBytes += estimatedBase64Size(file.content_base64);
      if (totalBytes > MAX_BATCH_BYTES) throw new HttpError(413, "BATCH_TOO_LARGE");
    }
    const concurrency = parseProjectConcurrency(Deno.env.get("PROJECT_CONCURRENCY"));
    const results = await mapWithConcurrency(
      batch.files,
      async (file) => projectFile(supabase, batch, file),
      concurrency,
    );
    return { ok: true, projected: results.length, results };
  }

  if (operation === "finalize_batch") {
    const batchId = requireString(raw.batch_id, "JOURNAL_BATCH_ID_REQUIRED");
    const claimToken = requireString(raw.claim_token, "JOURNAL_CLAIM_TOKEN_REQUIRED");
    const { data, error } = await supabase.rpc("radar_projection_finalize_batch_v1", {
      p_batch_id: batchId,
      p_claim_token: claimToken,
    });
    if (error) throw new HttpError(409, `JOURNAL_FINALIZE_FAILED:${error.code ?? "UNKNOWN"}`);
    return { ok: true, lane: data };
  }

  throw new HttpError(422, "OPERATION_INVALID");
}

Deno.serve(async (req: Request) => {
  try {
    if (req.method !== "POST") throw new HttpError(405, "METHOD_NOT_ALLOWED");
    const auth = req.headers.get("authorization") ?? "";
    const match = /^Bearer\s+(.+)$/i.exec(auth);
    if (!match) throw new HttpError(401, "OIDC_BEARER_REQUIRED");

    const digestHeader = (req.headers.get("x-radar-body-sha256") ?? "").toLowerCase();
    if (!SHA256_RE.test(digestHeader)) throw new HttpError(401, "BODY_SHA256_HEADER_INVALID");
    const rawBody = await readRequestBodyBounded(req, MAX_REQUEST_BYTES);
    const actualDigest = await sha256(rawBody);
    if (actualDigest !== digestHeader) throw new HttpError(401, "BODY_SHA256_MISMATCH");

    const claims = await verifyGithubOidc(match[1], `${AUDIENCE_PREFIX}${actualDigest}`);

    const supabaseUrl = Deno.env.get("SUPABASE_URL");
    const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceKey) throw new HttpError(503, "PROVIDER_CONFIGURATION_MISSING");
    const supabase = createClient(supabaseUrl, serviceKey, {
      auth: { persistSession: false, autoRefreshToken: false },
    }) as unknown as RadarRpcClient;

    // A cryptographically valid, exact-body-bound token is burned before any
    // semantic parsing. Malformed bound requests cannot be replayed indefinitely.
    await consumeGithubOidcJti(supabase, claims);

    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(rawBody);
    } catch {
      throw new HttpError(400, "JSON_BODY_UTF8_INVALID");
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new HttpError(400, "JSON_BODY_INVALID");
    }
    const raw = requireObject(parsed, "REQUEST_OBJECT_REQUIRED");
    const result = await handleOperation(supabase, raw);
    return response(200, result);
  } catch (error) {
    if (error instanceof HttpError) return response(error.status, { ok: false, error: error.code });
    console.error(error);
    return response(500, { ok: false, error: "INTERNAL_ERROR" });
  }
});
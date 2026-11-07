// Small helper for calling the FastAPI backend from the browser.
// Requests go to the same-origin route /api/backend/<path>, which adds the login token
// (kept in an httpOnly cookie) and forwards to FastAPI. See app/api/backend/[...path]/route.ts.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public fields: Record<string, string> = {}, // field name -> message, from a validation error
    public retryAfter: number | null = null, // seconds, from a rate limit
  ) {
    super(message);
  }
}

function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{ loc, msg, ... }]
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return `Request failed with status ${status}`;
}

function fieldErrors(body: unknown): Record<string, string> {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) return {};
  const fields: Record<string, string> = {};
  for (const error of detail as { loc?: unknown[]; msg?: string }[]) {
    const field = error.loc?.at(-1);
    if (typeof field === "string" && error.msg) fields[field] = error.msg;
  }
  return fields;
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  // For FormData the browser sets "multipart/form-data; boundary=..." itself
  const isForm = init.body instanceof FormData;
  const res = await fetch(url, {
    ...init,
    headers: isForm ? init.headers : { "Content-Type": "application/json", ...init.headers },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const retryAfter = Number(res.headers.get("retry-after")) || null;
    throw new ApiError(res.status, errorMessage(body, res.status), fieldErrors(body), retryAfter);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// Calls FastAPI `${path}` and returns the parsed JSON body.
// Throws ApiError when the response status is not 2xx; on 401 it also sends the user to /login.
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  try {
    return await request<T>(`/api/backend${path}`, init);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && typeof window !== "undefined") {
      const next = window.location.pathname + window.location.search;
      // Full page load (not router.push): this helper is not a component, and it drops all client state
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = `/login?next=${encodeURIComponent(next)}`;
    }
    throw err;
  }
}

const json = (body: unknown) => JSON.stringify(body);

// ---------- Health ----------

export type Health = { status: string; database: string; db_engine: string };

export function getHealth() {
  return apiFetch<Health>("/health");
}

// ---------- Auth ----------

export type User = { id: number; name: string; email: string; created_at: string };

export function login(email: string, password: string, remember: boolean) {
  return request<{ ok: true }>("/api/auth/login", { method: "POST", body: json({ email, password, remember }) });
}

export type Registration = { name: string; email: string; password: string; confirm_password: string };

// These go straight through the backend route: the caller is not logged in, so `apiFetch`'s 401 redirect must not apply.
// The account starts unverified; the emailed code is confirmed on /verify-email.
function publicPost<T>(path: string, body: unknown) {
  return request<T>(`/api/backend/auth/${path}`, { method: "POST", body: json(body) });
}

export function register(data: Registration) {
  return publicPost<User>("register", data);
}

export function verifyEmail(email: string, code: string) {
  return publicPost<{ message: string }>("verify-email", { email, code });
}

export function resendCode(email: string) {
  return publicPost<{ message: string }>("resend-code", { email });
}

export function forgotPassword(email: string) {
  return publicPost<{ message: string }>("forgot-password", { email });
}

export function resetPassword(token: string, password: string, confirmPassword: string) {
  return publicPost<{ message: string }>("reset-password", { token, password, confirm_password: confirmPassword });
}

export function logout() {
  return request<{ ok: true }>("/api/auth/logout", { method: "POST" });
}

export function getMe() {
  return apiFetch<User>("/auth/me");
}

// ---------- Projects ----------

export type Project = {
  id: number;
  name: string;
  description: string;
  scope_text: string;
  modules: ProjectModule[];
  owner_id: number;
  created_at: string;
  updated_at: string;
};

export type ProjectModule = { name: string; description: string };

export type ProjectInput = Pick<Project, "name" | "description" | "scope_text">;

export function listProjects() {
  return apiFetch<Project[]>("/projects");
}

export function createProject(data: ProjectInput) {
  return apiFetch<Project>("/projects", { method: "POST", body: json(data) });
}

export function getProject(id: number | string) {
  return apiFetch<Project>(`/projects/${id}`);
}

export function updateProject(id: number | string, data: Partial<ProjectInput> & Pick<Partial<Project>, "modules">) {
  return apiFetch<Project>(`/projects/${id}`, { method: "PATCH", body: json(data) });
}

export function deleteProject(id: number | string) {
  return apiFetch<void>(`/projects/${id}`, { method: "DELETE" });
}

// ---------- Team ----------

export const ROLES = ["lead", "developer", "qa"] as const;
export type Role = (typeof ROLES)[number];

export type TeamMember = {
  id: number;
  project_id: number;
  name: string;
  email: string;
  skills: string[];
  role: Role;
  created_at: string;
};

export type TeamMemberInput = Pick<TeamMember, "name" | "email" | "skills" | "role">;

export function listTeam(projectId: number | string) {
  return apiFetch<TeamMember[]>(`/projects/${projectId}/team`);
}

export function addMember(projectId: number | string, data: TeamMemberInput) {
  return apiFetch<TeamMember>(`/projects/${projectId}/team`, { method: "POST", body: json(data) });
}

export function updateMember(projectId: number | string, memberId: number, data: Partial<TeamMemberInput>) {
  return apiFetch<TeamMember>(`/projects/${projectId}/team/${memberId}`, { method: "PATCH", body: json(data) });
}

export function deleteMember(projectId: number | string, memberId: number) {
  return apiFetch<void>(`/projects/${projectId}/team/${memberId}`, { method: "DELETE" });
}

// ---------- Sources and uploads ----------

export const API_SOURCES = ["jira", "slack", "gmail", "telegram", "discord"] as const;
export const MESSAGE_SOURCES = ["whatsapp", "messenger", "email", "meeting_notes", ...API_SOURCES] as const;
export type MessageSource = (typeof MESSAGE_SOURCES)[number];

export type Source = {
  id: number;
  type: string;
  display_name: string;
  config: Record<string, unknown>;
  last_synced_at: string | null;
  created_at: string;
  sync_status: "ok" | "error" | "needs_auth" | null;
  sync_error: string | null;
  message_count: number;
  needs_auth: boolean; // Gmail source without a connected Google account
};

export type FileImportResult = {
  filename: string;
  type: string | null;
  imported: number;
  duplicates: number;
  skipped: number;
  error: string | null;
};

export type UploadResult = {
  imported: number;
  duplicates: number;
  skipped: number;
  files: FileImportResult[];
};

export function listSources(projectId: number | string) {
  return apiFetch<Source[]>(`/projects/${projectId}/sources`);
}

export function uploadFiles(projectId: number | string, files: File[]) {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return apiFetch<UploadResult>(`/projects/${projectId}/uploads`, { method: "POST", body: form });
}

// Permanently deletes the source and every message imported from it
export function deleteSource(projectId: number | string, sourceId: number) {
  return apiFetch<void>(`/projects/${projectId}/sources/${sourceId}`, { method: "DELETE" });
}

// ---------- API connectors ----------

// `missing_env` holds the names of .env variables the backend still needs (never their values)
export type Connector = { type: string; label: string; configured: boolean; missing_env: string[] };

export type SourceCreate = { type: string; display_name: string; settings: Record<string, unknown> };

export type SyncResult = { imported: number; duplicates: number; sync_status: string; last_synced_at: string | null };

export type SourceStatus = {
  id: number;
  type: string;
  configured: boolean;
  needs_auth: boolean;
  sync_status: Source["sync_status"];
  sync_error: string | null;
  last_synced_at: string | null;
  message_count: number;
};

export function listConnectors() {
  return apiFetch<Connector[]>("/connectors");
}

export function createSource(projectId: number | string, data: SourceCreate) {
  return apiFetch<Source>(`/projects/${projectId}/sources`, { method: "POST", body: json(data) });
}

export function syncSource(projectId: number | string, sourceId: number) {
  return apiFetch<SyncResult>(`/projects/${projectId}/sources/${sourceId}/sync`, { method: "POST" });
}

export function getSourceStatus(projectId: number | string, sourceId: number) {
  return apiFetch<SourceStatus>(`/projects/${projectId}/sources/${sourceId}/status`);
}

// Google consent page for a Gmail source; the browser is sent there
export function gmailAuthorizeUrl(projectId: number | string, sourceId: number) {
  return apiFetch<{ url: string }>(`/projects/${projectId}/sources/${sourceId}/gmail/authorize`);
}

// ---------- Messages ----------

export type Message = {
  id: number;
  source_id: number | null;
  source: string;
  external_id: string;
  channel: string;
  author: string;
  sent_at: string;
  text: string;
  imported_at: string;
};

export type MessagePage = { items: Message[]; total: number; page: number; page_size: number };

export type MessageFilters = {
  source?: string;
  author?: string;
  date_from?: string; // YYYY-MM-DD
  date_to?: string; // YYYY-MM-DD, inclusive
  q?: string;
  page?: number;
  page_size?: number;
};

export function listMessages(projectId: number | string, filters: MessageFilters = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return apiFetch<MessagePage>(`/projects/${projectId}/messages?${params}`);
}

export function deleteMessage(projectId: number | string, messageId: number) {
  return apiFetch<void>(`/projects/${projectId}/messages/${messageId}`, { method: "DELETE" });
}

// ---------- Requirements ----------

export const REQUIREMENT_STATUSES = ["suggested", "accepted", "edited", "rejected", "merged"] as const;
export type RequirementStatus = (typeof REQUIREMENT_STATUSES)[number];
export type RequirementType = "functional" | "non_functional";
export type NfrCategory = "performance" | "security" | "usability" | "reliability" | "other";

export type RequirementVersion = {
  version: number;
  statement: string;
  type: RequirementType;
  nfr_category: NfrCategory | null;
  actor: string;
  confidence: number | null; // AI confidence 0-1; null for versions written by a user
  ai_model: string | null; // "provider:model" or "rule-based"; null for versions written by a user
  origin: "ai" | "user";
  created_by_id: number | null;
  created_by_name: string | null; // null for AI versions and deleted users
  created_at: string;
};

export type RequirementSource = {
  message_id: number;
  source_sentence: string; // exactly as the client wrote it
  source: string;
  channel: string;
  author: string;
  sent_at: string;
  merged_from_id: number | null; // the requirement this sentence came from through a merge
};

export type Requirement = {
  id: number;
  code: string;
  origin: "ai" | "user";
  status: RequirementStatus;
  merged_into_id: number | null;
  reject_reason: string | null; // only while rejected
  created_at: string;
  updated_at: string;
  current: RequirementVersion;
  version_count: number;
  sources: RequirementSource[];
  quality_score: number | null; // 0-100; null until the first validation
  quality_failed: FailedCriterion[];
  module_name: string | null; // best-matching module
  module_score: number | null; // 0-1
  open_findings: number; // findings not rejected by a person
  has_issues: boolean; // open requirement with findings or failed criteria
};

export type RequirementDetail = Omit<Requirement, "sources"> & {
  sources: (RequirementSource & { message_text: string })[];
  versions: RequirementVersion[]; // oldest first
  findings: ValidationFinding[];
};

export type RequirementUpdate = {
  statement?: string;
  type?: RequirementType;
  nfr_category?: NfrCategory | null;
  actor?: string;
};

export type FailedCriterion = { criterion: string; points: number; reason: string };

export type FindingCategory = "vague" | "missing_info" | "out_of_scope" | "module_alignment";
export type FindingSeverity = "low" | "medium" | "high";

export type ValidationFinding = {
  id: number;
  requirement_id: number;
  version: number; // the requirement version that was checked
  category: FindingCategory;
  rule: string;
  severity: FindingSeverity;
  reason: string;
  evidence: string; // the matched word or the client's sentence
  origin: "ai" | "user";
  status: "suggested" | "accepted" | "edited" | "rejected";
  created_at: string;
};

export type ValidateResult = { validated: number; with_issues: number; findings: number };

export const BULK_LIMIT = 200;
export type BulkAction = "accept" | "reject" | "undo";

export type BulkResult = {
  updated: Requirement[];
  skipped: { id: number; code: string; reason: string }[]; // merged, already in that status, nothing to undo
};

export type ExtractResult = {
  messages_processed: number;
  messages_small_talk: number;
  messages_remaining: number;
  requirements_created: number;
  llm_calls: number;
  rule_based_batches: number; // batches done with cue-word rules because no AI model answered
  notice: string | null; // "AI unavailable — used rule-based extraction." when rules were used
  ai_error: string | null; // why the AI was not used
};

// ai_model of requirements found by the rule-based fallback (text = the client's sentence)
export const RULE_BASED_MODEL = "rule-based";
export const RULE_BASED_NOTICE = "AI unavailable — used rule-based extraction.";

// Reads messages not processed yet; force=true reads all again (only untouched AI suggestions are replaced)
export function extractRequirements(projectId: number | string, options: { force?: boolean } = {}) {
  return apiFetch<ExtractResult>(`/projects/${projectId}/requirements/extract`, {
    method: "POST",
    body: json({ force: options.force ?? false }),
  });
}

// Runs the validation rules on every requirement of the project
export function validateRequirements(projectId: number | string) {
  return apiFetch<ValidateResult>(`/projects/${projectId}/requirements/validate`, { method: "POST" });
}

export function listRequirements(
  projectId: number | string,
  filters: { status?: RequirementStatus; type?: RequirementType } = {},
) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return apiFetch<Requirement[]>(`/projects/${projectId}/requirements?${params}`);
}

export function getRequirement(projectId: number | string, id: number) {
  return apiFetch<RequirementDetail>(`/projects/${projectId}/requirements/${id}`);
}

// Saved as a new version; the older versions stay as they are
export function editRequirement(projectId: number | string, id: number, data: RequirementUpdate) {
  return apiFetch<RequirementDetail>(`/projects/${projectId}/requirements/${id}`, {
    method: "PATCH",
    body: json(data),
  });
}

function requirementAction(projectId: number | string, id: number, action: string, body?: unknown) {
  return apiFetch<RequirementDetail>(`/projects/${projectId}/requirements/${id}/${action}`, {
    method: "POST",
    body: body === undefined ? undefined : json(body),
  });
}

export function acceptRequirement(projectId: number | string, id: number) {
  return requirementAction(projectId, id, "accept");
}

export function rejectRequirement(projectId: number | string, id: number, reason: string) {
  return requirementAction(projectId, id, "reject", { reason });
}

// Takes back the last accept / reject: the status returns to what it was
export function undoRequirement(projectId: number | string, id: number) {
  return requirementAction(projectId, id, "undo");
}

// Rejected or merged back to "suggested"
export function restoreRequirement(projectId: number | string, id: number) {
  return requirementAction(projectId, id, "restore");
}

// Folds `otherIds` into `id`; their source sentences are kept on it
export function mergeRequirements(projectId: number | string, id: number, otherIds: number[]) {
  return requirementAction(projectId, id, "merge", { other_ids: otherIds });
}

export function bulkRequirements(projectId: number | string, ids: number[], action: BulkAction, reason?: string) {
  return apiFetch<BulkResult>(`/projects/${projectId}/requirements/bulk`, {
    method: "POST",
    body: json({ ids, action, reason }),
  });
}

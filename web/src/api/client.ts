/**
 * CareFlow API client for browsers.
 *
 * - The access token is kept in memory only (never localStorage), so a page reload signs in
 *   again silently using the HttpOnly refresh cookie set by the server.
 * - The refresh call sends the CSRF token (readable cookie) in a header: double-submit protection.
 * - An expired access token is refreshed once (single-flight) and the request retried.
 * - Every request has a timeout; network problems become clear ApiErrors.
 * - The server is the authority on permissions; this client only reports what it says.
 */
import type { Me } from "./types";

export const API_BASE = "/api/v1";
const TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: unknown = null,
    public requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let accessToken: string | null = null;
let refreshing: Promise<Me> | null = null;
let onSessionExpired: (() => void) | null = null;

export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function readCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(`${API_BASE}${path}`, { ...init, signal: controller.signal, credentials: "same-origin" });
  } catch (err) {
    if (controller.signal.aborted) {
      throw new ApiError(0, "timeout", "CareFlow took too long to respond. Please try again.");
    }
    throw new ApiError(0, "network_error", "Cannot reach CareFlow. Check your internet connection.");
  } finally {
    clearTimeout(timer);
  }
}

async function toError(resp: Response): Promise<ApiError> {
  try {
    const body = await resp.json();
    const e = body.error ?? {};
    return new ApiError(resp.status, e.code ?? "error", e.message ?? "The request failed.", e.details ?? null,
      e.request_id ?? null);
  } catch {
    if (resp.status >= 500) return new ApiError(resp.status, "server_error", "CareFlow had a problem. Please try again.");
    return new ApiError(resp.status, "error", `The request failed (HTTP ${resp.status}).`);
  }
}

function expire(): void {
  const wasSignedIn = accessToken !== null;
  accessToken = null;
  if (wasSignedIn) onSessionExpired?.();
}

async function doRefresh(): Promise<Me> {
  const resp = await send("/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": readCookie("careflow_csrf") ?? "" },
    body: "{}",
  });
  if (!resp.ok) throw await toError(resp);
  const data = await resp.json();
  accessToken = data.access_token;
  return data.user as Me;
}

/** Single-flight refresh: concurrent 401s and session restores share one refresh request. */
function refreshOnce(): Promise<Me> {
  if (!refreshing) {
    refreshing = doRefresh().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

export async function ensureFreshToken(): Promise<void> {
  try {
    await refreshOnce();
  } catch (err) {
    if (err instanceof ApiError && err.status !== 0) expire();
    throw err;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== null && v !== undefined && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function api<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  for (let attempt = 1; attempt <= 2; attempt++) {
    if (!accessToken) throw new ApiError(401, "session_ended", "Please sign in.");
    const headers: Record<string, string> = { Authorization: `Bearer ${accessToken}` };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const resp = await send(withQuery(path, query), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (resp.status === 401) {
      const err = await toError(resp);
      if (attempt === 1 && err.code === "token_expired") {
        await ensureFreshToken();
        continue;
      }
      expire();
      throw err;
    }
    if (!resp.ok) throw await toError(resp);
    if (resp.status === 204) return undefined as T;
    return (await resp.json()) as T;
  }
  throw new ApiError(401, "session_ended", "Please sign in.");
}

export interface RegisterPatientInput {
  email: string;
  password: string;
  full_name: string;
  phone?: string | null;
  date_of_birth?: string | null;
}

export interface RegisterHospitalAdminInput {
  hospital_name: string;
  hospital_code: string;
  hospital_slug?: string | null;
  timezone?: string;
  address?: string | null;
  admin_name: string;
  admin_email: string;
  admin_password: string;
  phone?: string | null;
  starter_tokens?: number;
}

export interface HospitalRegistrationResult {
  hospital_id: string;
  hospital_code: string;
  hospital_name: string;
  hospital_slug: string;
  user_id: string;
  admin_email: string;
  admin_name: string;
  starter_tokens: number;
  token?: {
    access_token: string;
    user: Me;
  };
}

export async function registerPatient(input: RegisterPatientInput): Promise<Me> {
  const resp = await send("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: input.email.trim(),
      password: input.password,
      full_name: input.full_name.trim(),
      phone: input.phone?.trim() || null,
      date_of_birth: input.date_of_birth || null,
    }),
  });
  if (!resp.ok) throw await toError(resp);
  return await login(input.email.trim(), input.password);
}

export async function registerHospitalAdmin(input: RegisterHospitalAdminInput): Promise<HospitalRegistrationResult> {
  const resp = await send("/auth/register-hospital", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...input,
      client_type: "web",
    }),
  });
  if (!resp.ok) throw await toError(resp);
  const data = (await resp.json()) as HospitalRegistrationResult;
  if (data.token?.access_token) {
    accessToken = data.token.access_token;
  }
  return data;
}

export async function login(email: string, password: string): Promise<Me> {
  const resp = await send("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, client_type: "web" }),
  });
  if (!resp.ok) throw await toError(resp);
  const data = await resp.json();
  accessToken = data.access_token;
  return data.user as Me;
}

/** On page load: use the refresh cookie (if any) to start a session without asking for the password. */
export async function restoreSession(): Promise<Me | null> {
  if (!readCookie("careflow_csrf")) return null;
  try {
    return await refreshOnce();
  } catch (err) {
    if (err instanceof ApiError && err.status === 0) throw err; // offline: let the caller say so
    return null;
  }
}

export async function logout(): Promise<void> {
  try {
    if (accessToken) await api<void>("POST", "/auth/logout");
  } catch {
    // The session is forgotten locally regardless; the server session expires on its own.
  } finally {
    accessToken = null;
  }
}

/** Test helper. */
export function _setAccessTokenForTests(token: string | null): void {
  accessToken = token;
}

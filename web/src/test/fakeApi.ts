/** A tiny fake of the CareFlow API for tests: routes "METHOD /path" to handlers, records requests. */
import { vi } from "vitest";

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (call: Call) => unknown | Response;

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function apiError(status: number, code: string, message = "Problem"): Response {
  return json(status, { error: { code, message, details: null, request_id: "req-1" } });
}

/** An SSE stream that announces "ready" and then stays open. */
function readyStream(): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("retry: 5000\n\nevent: ready\ndata: {}\n\n"));
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = (init.method ?? "GET").toUpperCase();
    const headers = Object.fromEntries(new Headers(init.headers).entries());
    const call: Call = { method, path, query: url.searchParams, headers,
      body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (path === "/stream") return readyStream();
    const handler = routes[`${method} ${path}`];
    if (!handler) return apiError(404, "not_found", `No fake route for ${method} ${path}`);
    const result = handler(call);
    return result instanceof Response ? result : json(200, result);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock, find: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path) };
}

/**
 * Live updates over Server-Sent Events, read with fetch so the access token travels in the
 * Authorization header (EventSource cannot set headers, and tokens must never go in URLs).
 *
 * Events are hints that something changed; the UI re-fetches data over the REST API.
 * Reconnects with backoff; reports "connecting" | "live" | "offline".
 */
import { API_BASE, ApiError, ensureFreshToken, getAccessToken } from "./client";
import type { LiveEvent } from "./types";

export type StreamState = "connecting" | "live" | "offline";

/** Incremental SSE parser: feed text chunks, get complete events. */
export class SseParser {
  private buffer = "";
  private event = "message";
  private data: string[] = [];

  feed(chunk: string): Array<{ event: string; data: string }> {
    this.buffer += chunk;
    const out: Array<{ event: string; data: string }> = [];
    let newline: number;
    while ((newline = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, newline).replace(/\r$/, "");
      this.buffer = this.buffer.slice(newline + 1);
      if (line === "") {
        if (this.data.length) out.push({ event: this.event, data: this.data.join("\n") });
        this.event = "message";
        this.data = [];
      } else if (line.startsWith(":")) {
        continue;
      } else if (line.startsWith("event:")) {
        this.event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        this.data.push(line.slice(5).replace(/^ /, ""));
      }
    }
    return out;
  }
}

export function subscribe(onEvent: (e: LiveEvent) => void, onState: (s: StreamState) => void): () => void {
  const controller = new AbortController();
  let stopped = false;

  const run = async () => {
    let backoff = 1000;
    while (!stopped) {
      onState("connecting");
      try {
        const token = getAccessToken();
        if (!token) throw new ApiError(401, "session_ended", "Signed out");
        const resp = await fetch(`${API_BASE}/stream`, {
          headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
          signal: controller.signal,
          credentials: "same-origin",
        });
        if (resp.status === 401) {
          await ensureFreshToken(); // throws if the session is over
          continue;
        }
        if (!resp.ok || !resp.body) throw new Error(`stream HTTP ${resp.status}`);
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          for (const { event, data } of parser.feed(decoder.decode(value, { stream: true }))) {
            if (event === "ready") {
              onState("live");
              backoff = 1000;
            } else if (event === "session_ended") {
              await ensureFreshToken();
            } else {
              try {
                onEvent({ type: event, ...JSON.parse(data || "{}") });
              } catch {
                // malformed event: ignore it; the periodic refresh keeps data correct
              }
            }
          }
        }
      } catch (err) {
        if (stopped) return;
        if (err instanceof ApiError && err.status === 401) {
          onState("offline");
          return; // signed out: the app returns to the sign-in page
        }
      }
      if (stopped) return;
      onState("offline");
      await new Promise((r) => setTimeout(r, backoff));
      backoff = Math.min(backoff * 2, 30_000);
    }
  };
  void run();
  return () => {
    stopped = true;
    controller.abort();
  };
}

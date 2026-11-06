import { expect, it } from "vitest";

import { SseParser } from "./events";

it("parses events split across arbitrary chunk boundaries", () => {
  const parser = new SseParser();
  const text = 'retry: 5000\n\nevent: ready\ndata: {}\n\n: keepalive\n\nid: 1\nevent: queue_entry\ndata: {"a":1}\n\n';
  const events = [];
  for (let i = 0; i < text.length; i += 7) events.push(...parser.feed(text.slice(i, i + 7)));
  expect(events).toEqual([
    { event: "ready", data: "{}" },
    { event: "queue_entry", data: '{"a":1}' },
  ]);
});

it("handles CRLF line endings and multi-line data", () => {
  const parser = new SseParser();
  expect(parser.feed("event: x\r\ndata: a\r\ndata: b\r\n\r\n")).toEqual([{ event: "x", data: "a\nb" }]);
});

import { expect, it } from "vitest";

import { formatTime, todayIn, waitText } from "./format";

it("uses the hospital's timezone for 'today', not the device's", () => {
  const lateEveningUtc = new Date("2026-11-02T23:30:00Z"); // already 3 Nov in Lagos (UTC+1)
  expect(todayIn("Africa/Lagos", lateEveningUtc)).toBe("2026-11-03");
  expect(todayIn("UTC", lateEveningUtc)).toBe("2026-11-02");
});

it("formats times in the hospital's timezone", () => {
  expect(formatTime("2026-11-02T08:00:00+00:00", "Africa/Lagos")).toBe("09:00");
});

it("describes estimates honestly", () => {
  const base = { min_minutes: 15, max_minutes: 30, provisional: false, explanation: "", basis: {} };
  expect(waitText({ ...base, state: "estimated" })).toBe("15–30 min");
  expect(waitText({ ...base, state: "estimated", provisional: true })).toBe("15–30 min (provisional)");
  expect(waitText({ ...base, state: "unavailable" })).toBe("Not available");
});

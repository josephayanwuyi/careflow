import type { Estimate } from "./api/types";

const LABELS: Record<string, string> = {
  waiting: "Waiting",
  called: "Called",
  in_consultation: "In consultation",
  completed: "Completed",
  missed: "Missed call",
  cancelled: "Cancelled",
  transferred: "Transferred",
  requested: "Pending approval",
  approved: "Approved",
  rejected: "Rejected",
  reschedule_proposed: "New time proposed",
  expired: "Expired",
  checked_in: "Checked in",
  no_show: "No-show",
  off_duty: "Not checked in",
  available: "Available",
  on_break: "On break",
  temporarily_unavailable: "Temporarily unavailable",
  checked_out: "Left for the day",
  expected: "Expected",
  consultation_in_progress: "Consultation in progress",
  unavailable: "Unavailable",
};

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "";
  return LABELS[status] ?? status.replace(/_/g, " ");
}

export function formatTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(iso));
}

export function formatDateTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone,
  }).format(new Date(iso));
}

/** Today's date (YYYY-MM-DD) in the hospital's timezone, not the device's. */
export function todayIn(timeZone: string, now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

export function minutesSince(iso: string | null | undefined, now: Date = new Date()): number | null {
  if (!iso) return null;
  return Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60_000));
}

export function minutesUntil(iso: string | null | undefined, now: Date = new Date()): number {
  if (!iso) return 0;
  return Math.round((new Date(iso).getTime() - now.getTime()) / 60_000);
}

export function waitText(estimate: Estimate | undefined): string {
  if (!estimate) return "";
  switch (estimate.state) {
    case "next":
      return "Next";
    case "estimated":
      return `${estimate.min_minutes}–${estimate.max_minutes} min${estimate.provisional ? " (provisional)" : ""}`;
    case "called":
      return "Called";
    case "being_seen":
      return "With you";
    case "unavailable":
      return "Not available";
    default:
      return "";
  }
}

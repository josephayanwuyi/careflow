/** Doctor-facing API calls. The server scopes all of these to the signed-in doctor. */
import { api } from "./client";
import type { Appointment, DoctorAvailability, Hospital, Page, QueueEntry, QueueSummary, StatusEvent } from "./types";

export const doctorApi = {
  hospital: (hid: string) => api<Hospital>("GET", `/hospitals/${hid}`),
  myStatus: (hid: string) => api<DoctorAvailability>("GET", "/doctors/me/status", undefined, { hospital_id: hid }),
  setStatus: (hid: string, status: string, extra: { note?: string | null; expected_return_at?: string | null } = {}) =>
    api<DoctorAvailability>("POST", "/doctors/me/status", { hospital_id: hid, status, ...extra }),
  statusHistory: (doctorId: string) => api<StatusEvent[]>("GET", `/doctors/${doctorId}/status-history`),
  myQueues: (hid: string) => api<QueueSummary[]>("GET", `/hospitals/${hid}/queues`),
  entries: (queueId: string, includeFinished = false) =>
    api<QueueEntry[]>("GET", `/queues/${queueId}/entries`, undefined, { include_finished: includeFinished }),
  callNext: (queueId: string) => api<QueueEntry>("POST", `/queues/${queueId}/call-next`),
  entryAction: (entryId: string, action: string, body: Record<string, unknown> = {}) =>
    api<QueueEntry>("POST", `/queue-entries/${entryId}/${action}`, body),
  pause: (queueId: string, reason: string | null) => api<QueueSummary>("POST", `/queues/${queueId}/pause`, { reason }),
  resume: (queueId: string) => api<QueueSummary>("POST", `/queues/${queueId}/resume`),
  todaysAppointments: (hid: string, day: string) =>
    api<Page<Appointment>>("GET", `/hospitals/${hid}/appointments`, undefined, {
      date_from: day, date_to: day, limit: 200,
    }),
};

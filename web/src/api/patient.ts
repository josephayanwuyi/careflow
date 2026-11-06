/** Patient-facing API calls. Scoped to the signed-in patient or public hospital data. */
import { api } from "./client";
import type {
  Appointment,
  Department,
  DoctorAvailability,
  DoctorPublic,
  Hospital,
  NotificationItem,
  Page,
  PatientProfile,
  QueueEntry,
  SlotDay,
} from "./types";

export const patientApi = {
  hospitals: () => api<Hospital[]>("GET", "/hospitals"),
  hospital: (hid: string) => api<Hospital>("GET", `/hospitals/${hid}`),
  departments: (hid: string) => api<Department[]>("GET", `/hospitals/${hid}/departments`),
  doctors: (hid: string, departmentId?: string | null) =>
    api<DoctorPublic[]>("GET", `/hospitals/${hid}/doctors`, undefined, departmentId ? { department_id: departmentId } : undefined),
  doctorAvailability: (hid: string) => api<DoctorAvailability[]>("GET", `/hospitals/${hid}/doctors/availability`),
  doctorSlots: (doctorId: string, date: string) =>
    api<SlotDay>("GET", `/doctors/${doctorId}/slots`, undefined, { date }),

  // Appointments
  requestAppointment: (data: {
    doctor_id: string;
    department_id?: string | null;
    start_at: string;
    reason_for_visit?: string | null;
  }) => api<Appointment>("POST", "/appointments", data),
  myAppointments: (status?: string[], limit = 50) =>
    api<Page<Appointment>>("GET", "/appointments/mine", undefined, {
      status: status && status.length > 0 ? status.join(",") : undefined,
      limit,
    }),
  appointment: (id: string) => api<Appointment>("GET", `/appointments/${id}`),
  acceptReschedule: (id: string, expectedVersion?: number) =>
    api<Appointment>("POST", `/appointments/${id}/accept-reschedule`, { expected_version: expectedVersion }),
  declineReschedule: (id: string, reason?: string | null, expectedVersion?: number) =>
    api<Appointment>("POST", `/appointments/${id}/decline-reschedule`, { reason, expected_version: expectedVersion }),
  cancelAppointment: (id: string, reason?: string | null, expectedVersion?: number) =>
    api<Appointment>("POST", `/appointments/${id}/cancel`, { reason, expected_version: expectedVersion }),

  // Queue
  myQueue: () => api<QueueEntry[]>("GET", "/queue/me"),
  checkIn: (appointmentId: string) => api<QueueEntry>("POST", `/appointments/${appointmentId}/check-in`),
  cancelQueueEntry: (entryId: string, reason?: string | null, expectedVersion?: number) =>
    api<QueueEntry>("POST", `/queue-entries/${entryId}/cancel`, { reason, expected_version: expectedVersion }),

  // Notifications
  notifications: (unreadOnly = false, limit = 50) =>
    api<{ items: NotificationItem[]; unread_count: number; next_cursor: string | null }>(
      "GET",
      "/notifications",
      undefined,
      { unread_only: unreadOnly, limit },
    ),
  markRead: (id: string) => api<NotificationItem>("POST", `/notifications/${id}/read`),
  markAllRead: () => api<{ marked: number }>("POST", "/notifications/read-all"),

  // Profile
  myProfile: () => api<PatientProfile>("GET", "/patients/me"),
  updateProfile: (data: Partial<PatientProfile>) => api<PatientProfile>("PATCH", "/patients/me", data),
};

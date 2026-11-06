import type { DoctorAvailability, Me, QueueEntry, QueueSummary } from "../api/types";

export const HID = "h-1";
export const DOCTOR_ID = "d-1";

export const doctorUser: Me = {
  id: "u-1", email: "doc@example.test", full_name: "Dr Test", phone: null, patient_id: null,
  memberships: [{ hospital_id: HID, hospital_name: "Test Hospital", roles: ["doctor"],
    permissions: ["queue.consult", "availability.self"], doctor_id: DOCTOR_ID }],
};

export const deskUser: Me = {
  ...doctorUser,
  memberships: [{ ...doctorUser.memberships[0], roles: ["receptionist"], doctor_id: null }],
};

export const PATIENT_ID = "p-1";

export const patientUser: Me = {
  id: "u-2",
  email: "patient@example.test",
  full_name: "Ada Patient",
  phone: "+2348031234567",
  patient_id: PATIENT_ID,
  memberships: [],
};

export function availability(status: string, extra: Partial<DoctorAvailability> = {}): DoctorAvailability {
  return {
    doctor_id: DOCTOR_ID, name: "Dr Test", department_id: "dep", department_name: "General", status,
    public_status: status === "available" ? "available" : "expected", status_source: "self_reported",
    status_updated_at: "2026-11-02T08:00:00+00:00", expected_return_at: null, note: null,
    arrival_verified_today: false, arrival_verified_at: null, ...extra,
  };
}

export const queueSummary: QueueSummary = {
  id: "q-1", service_date: "2026-11-02", department_id: "dep",
  doctor: { id: DOCTOR_ID, name: "Dr Test", public_status: "available" }, doctor_status: "available",
  is_paused: false, paused_reason: null, counts: { waiting: 2 },
};

export function entry(extra: Partial<QueueEntry> = {}): QueueEntry {
  return {
    id: "e-1", queue_id: "q-1", service_date: "2026-11-02", hospital_timezone: "Africa/Lagos", ticket_number: 1,
    status: "waiting", position: 1, queue_paused: false, checked_in_at: "2026-11-02T07:50:00+00:00",
    estimate: { state: "next", min_minutes: 0, max_minutes: 5, provisional: true, explanation: "", basis: {} },
    doctor: { id: DOCTOR_ID, name: "Dr Test", public_status: "available" },
    allowed_actions: ["call", "cancel"], version: 1,
    patient: { id: "p-1", full_name: "Ada Patient", hospital_patient_number: "TEST-000001" },
    kind: "appointment", appointment_start_at: "2026-11-02T08:00:00+00:00", reason_for_visit: "Cough",
    priority_level: 0, check_in_method: "staff", arrival_confirmed_at: "2026-11-02T07:50:00+00:00",
    ...extra,
  };
}

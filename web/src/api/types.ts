export interface Membership {
  hospital_id: string;
  hospital_name: string;
  roles: string[];
  permissions: string[];
  doctor_id: string | null;
}

export interface Me {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  patient_id: string | null;
  memberships: Membership[];
}

export interface Estimate {
  state: "estimated" | "next" | "called" | "being_seen" | "unavailable" | "not_waiting";
  min_minutes: number | null;
  max_minutes: number | null;
  provisional: boolean;
  explanation: string;
  basis: Record<string, unknown>;
}

export interface PatientBrief {
  id: string;
  full_name: string;
  hospital_patient_number: string | null;
}

export interface QueueEntry {
  id: string;
  queue_id: string;
  service_date: string;
  hospital_timezone: string;
  ticket_number: number;
  status: "waiting" | "called" | "in_consultation" | "completed" | "missed" | "cancelled" | "transferred";
  position: number | null;
  queue_paused: boolean;
  checked_in_at: string;
  estimate: Estimate;
  doctor: { id: string; name: string; public_status: string };
  allowed_actions: string[];
  version: number;
  patient: PatientBrief | null;
  kind?: "appointment" | "walk_in";
  appointment_id?: string | null;
  appointment_start_at?: string | null;
  reason_for_visit?: string | null;
  priority_level?: number;
  priority_reason?: string | null;
  check_in_method?: "self" | "staff";
  arrival_confirmed_at?: string | null;
  called_at?: string | null;
  call_count?: number;
  missed_count?: number;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface QueueSummary {
  id: string;
  service_date: string;
  department_id: string;
  doctor: { id: string; name: string; public_status: string };
  doctor_status: string;
  is_paused: boolean;
  paused_reason: string | null;
  counts: Record<string, number>;
}

export interface DoctorAvailability {
  doctor_id: string;
  name: string;
  department_id: string;
  department_name: string;
  status: string;
  public_status: string;
  status_source: string | null;
  status_updated_at: string | null;
  expected_return_at: string | null;
  note: string | null;
  arrival_verified_today: boolean;
  arrival_verified_at: string | null;
}

export interface StatusEvent {
  status: string;
  source: string;
  note: string | null;
  expected_return_at: string | null;
  actor_user_id: string | null;
  occurred_at: string;
}

export interface Appointment {
  id: string;
  status: string;
  hospital_id: string;
  hospital_name: string;
  hospital_timezone: string;
  department_id: string;
  department_name: string;
  doctor_id: string;
  doctor_name: string;
  start_at: string;
  end_at: string;
  proposed_start_at: string | null;
  proposed_end_at: string | null;
  status_reason: string | null;
  reason_for_visit: string | null;
  source: string;
  version: number;
  created_at: string;
  allowed_actions: string[];
  patient: PatientBrief | null;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Hospital {
  id: string;
  code: string;
  name: string;
  slug: string;
  timezone: string;
  address: string | null;
}

export interface Department {
  id: string;
  hospital_id: string;
  name: string;
  description: string | null;
}

export interface DoctorPublic {
  id: string;
  hospital_id: string;
  department_id: string;
  department_name: string;
  display_name: string;
  title: string | null;
  specialty: string | null;
  is_accepting_appointments: boolean;
}

export interface Slot {
  start_at: string;
  end_at: string;
  available: boolean;
}

export interface SlotDay {
  doctor_id: string;
  date: string;
  timezone: string;
  slots: Slot[];
}

export interface NotificationItem {
  id: string;
  user_id: string;
  type: string;
  title: string;
  body: string;
  created_at: string;
  read_at: string | null;
  appointment_id?: string | null;
  queue_entry_id?: string | null;
}

export interface PatientProfile {
  id: string;
  full_name: string;
  phone: string | null;
  date_of_birth: string | null;
  sex: string | null;
}

export interface LiveEvent {
  id?: string;
  type: string;
  action?: string | null;
  queue_id?: string | null;
  entry_id?: string | null;
  appointment_id?: string | null;
  doctor_id?: string | null;
  user_id?: string | null;
  status?: string | null;
}

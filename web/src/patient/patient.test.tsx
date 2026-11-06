import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { _setAccessTokenForTests } from "../api/client";
import type { Appointment, Department, DoctorPublic, Hospital, NotificationItem, PatientProfile, QueueEntry, SlotDay } from "../api/types";
import { App } from "../App";
import { AuthProvider } from "../auth/AuthContext";
import { mockApi } from "../test/fakeApi";
import { DOCTOR_ID, HID, PATIENT_ID, patientUser } from "../test/fixtures";

const hospital: Hospital = {
  id: HID,
  code: "TEST",
  name: "Test Hospital",
  slug: "test-hospital",
  timezone: "Africa/Lagos",
  address: "1 Hospital Rd",
};

const department: Department = {
  id: "dep-1",
  hospital_id: HID,
  name: "General Practice",
  description: "Primary health",
};

const doctor: DoctorPublic = {
  id: DOCTOR_ID,
  hospital_id: HID,
  department_id: "dep-1",
  department_name: "General Practice",
  display_name: "Dr. Test Okafor",
  title: "Dr.",
  specialty: "Family Medicine",
  is_accepting_appointments: true,
};

const slotDay: SlotDay = {
  doctor_id: DOCTOR_ID,
  date: "2026-11-02",
  timezone: "Africa/Lagos",
  slots: [
    { start_at: "2026-11-02T08:00:00+00:00", end_at: "2026-11-02T08:30:00+00:00", available: true },
    { start_at: "2026-11-02T08:30:00+00:00", end_at: "2026-11-02T09:00:00+00:00", available: false },
  ],
};

const patientProfile: PatientProfile = {
  id: PATIENT_ID,
  full_name: "Ada Patient",
  phone: "+2348031234567",
  date_of_birth: "1990-05-15",
  sex: "female",
};

function sampleEntry(extra: Partial<QueueEntry> = {}): QueueEntry {
  return {
    id: "e-1",
    queue_id: "q-1",
    service_date: "2026-11-02",
    hospital_timezone: "Africa/Lagos",
    ticket_number: 12,
    status: "waiting",
    position: 2,
    queue_paused: false,
    checked_in_at: "2026-11-02T08:15:00+00:00",
    estimate: {
      state: "estimated",
      min_minutes: 15,
      max_minutes: 30,
      provisional: true,
      explanation: "Based on 1 patient ahead",
      basis: {},
    },
    doctor: { id: DOCTOR_ID, name: "Dr. Test Okafor", public_status: "available" },
    allowed_actions: ["cancel"],
    version: 1,
    patient: { id: PATIENT_ID, full_name: "Ada Patient", hospital_patient_number: "TEST-0001" },
    ...extra,
  };
}

function sampleAppointment(extra: Partial<Appointment> = {}): Appointment {
  return {
    id: "a-1",
    status: "approved",
    hospital_id: HID,
    hospital_name: "Test Hospital",
    hospital_timezone: "Africa/Lagos",
    department_id: "dep-1",
    department_name: "General Practice",
    doctor_id: DOCTOR_ID,
    doctor_name: "Dr. Test Okafor",
    start_at: "2026-11-02T09:00:00+00:00",
    end_at: "2026-11-02T09:30:00+00:00",
    proposed_start_at: null,
    proposed_end_at: null,
    status_reason: null,
    reason_for_visit: "General checkup",
    source: "patient_request",
    version: 1,
    created_at: "2026-11-01T10:00:00+00:00",
    allowed_actions: ["cancel"],
    patient: { id: PATIENT_ID, full_name: "Ada Patient", hospital_patient_number: "TEST-0001" },
    ...extra,
  };
}

function renderSignedIn(path = "/patient") {
  document.cookie = "careflow_csrf=c; path=/";
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function patientRoutes(
  state: {
    entries: QueueEntry[];
    appointments: Appointment[];
    notifications?: NotificationItem[];
    profile?: PatientProfile;
  },
  extra: Record<string, (c: never) => unknown> = {},
) {
  return mockApi({
    "POST /auth/refresh": () => ({ access_token: "a1", user: patientUser }),
    "GET /queue/me": () => state.entries,
    "GET /appointments/mine": () => ({ items: state.appointments, next_cursor: null }),
    "GET /notifications": () => ({ items: state.notifications ?? [], unread_count: (state.notifications ?? []).filter((n) => !n.read_at).length, next_cursor: null }),
    "GET /patients/me": () => state.profile ?? patientProfile,
    "GET /hospitals": () => [hospital],
    [`GET /hospitals/${HID}/departments`]: () => [department],
    [`GET /hospitals/${HID}/doctors`]: () => [doctor],
    [`GET /hospitals/${HID}/doctors/availability`]: () => [
      {
        doctor_id: DOCTOR_ID,
        name: "Dr. Test Okafor",
        department_id: "dep-1",
        department_name: "General Practice",
        status: "available",
        public_status: "available",
        status_source: "self_reported",
        status_updated_at: "2026-11-02T08:00:00+00:00",
        expected_return_at: null,
        note: null,
        arrival_verified_today: true,
        arrival_verified_at: "2026-11-02T08:05:00+00:00",
      },
    ],
    [`GET /doctors/${DOCTOR_ID}/slots`]: () => slotDay,
    ...extra,
  });
}

beforeEach(() => {
  _setAccessTokenForTests(null);
});

describe("patient area", () => {
  it("restores the session and opens the patient queue", async () => {
    patientRoutes({ entries: [], appointments: [] });
    renderSignedIn("/patient");

    expect(await screen.findByRole("heading", { name: "Live Queue" })).toBeInTheDocument();
    expect(screen.getByText("You are not currently waiting in any hospital queue.")).toBeInTheDocument();
  });

  it("shows active queue status with ticket, wait time, and position", async () => {
    const entry = sampleEntry();
    patientRoutes({ entries: [entry], appointments: [] });
    renderSignedIn("/patient");

    expect(await screen.findByRole("heading", { name: "Your Queue Status" })).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("1 patient(s) ahead of you")).toBeInTheDocument();
    expect(screen.getByText(/Estimated wait: 15–30 minutes/)).toBeInTheDocument();
    expect(screen.getByText("Dr. Test Okafor")).toBeInTheDocument();
  });

  it("allows patient to leave the queue with confirmation", async () => {
    const user = userEvent.setup();
    const entry = sampleEntry();
    let cancelled = false;

    patientRoutes(
      { entries: [entry], appointments: [] },
      {
        "POST /queue-entries/e-1/cancel": () => {
          cancelled = true;
          return { ...entry, status: "cancelled" };
        },
      },
    );

    renderSignedIn("/patient");
    const leaveBtn = await screen.findByRole("button", { name: "Leave queue" });
    await user.click(leaveBtn);

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Leave the queue\?/)).toBeInTheDocument();

    const confirmBtn = within(dialog).getByRole("button", { name: "Leave queue" });
    await user.click(confirmBtn);

    await waitFor(() => expect(cancelled).toBe(true));
  });

  it("shows appointments and accepts a reschedule proposal", async () => {
    const user = userEvent.setup();
    const proposed = sampleAppointment({
      id: "a-prop",
      status: "reschedule_proposed",
      proposed_start_at: "2026-11-03T10:00:00+00:00",
      status_reason: "Doctor on emergency duty",
    });

    let accepted = false;
    patientRoutes(
      { entries: [], appointments: [proposed] },
      {
        "POST /appointments/a-prop/accept-reschedule": () => {
          accepted = true;
          return { ...proposed, status: "approved" };
        },
      },
    );

    renderSignedIn("/patient/appointments");
    expect(await screen.findByText("Hospital proposed a new time:")).toBeInTheDocument();
    expect(screen.getByText("Reason: Doctor on emergency duty")).toBeInTheDocument();

    const acceptBtn = screen.getByRole("button", { name: "Accept new time" });
    await user.click(acceptBtn);

    await waitFor(() => expect(accepted).toBe(true));
  });

  it("cancels an appointment with confirmation dialog", async () => {
    const user = userEvent.setup();
    const appt = sampleAppointment({ id: "a-cancel", status: "requested" });
    let cancelled = false;

    patientRoutes(
      { entries: [], appointments: [appt] },
      {
        "POST /appointments/a-cancel/cancel": () => {
          cancelled = true;
          return { ...appt, status: "cancelled" };
        },
      },
    );

    renderSignedIn("/patient/appointments");
    const cancelBtn = await screen.findByRole("button", { name: "Cancel appointment" });
    await user.click(cancelBtn);

    const dialog = screen.getByRole("dialog");
    const confirmBtn = within(dialog).getByRole("button", { name: "Cancel appointment" });
    await user.click(confirmBtn);

    await waitFor(() => expect(cancelled).toBe(true));
  });

  it("discovers doctors, selects a slot, and submits an appointment request", async () => {
    const user = userEvent.setup();
    let requested = false;

    patientRoutes(
      { entries: [], appointments: [] },
      {
        "POST /appointments": ((c: { body: { doctor_id: string; start_at: string } }) => {
          expect(c.body.doctor_id).toBe(DOCTOR_ID);
          expect(c.body.start_at).toBe("2026-11-02T08:00:00+00:00");
          requested = true;
          return sampleAppointment();
        }) as never,
      },
    );

    renderSignedIn("/patient/book");

    // Select doctor
    const doctorCard = await screen.findByText("Dr. Test Okafor");
    await user.click(doctorCard);

    // Pick slot
    const slotBtn = await screen.findByRole("radio", { name: /09:00/ }); // 08:00 UTC = 09:00 Lagos
    await user.click(slotBtn);

    // Fill reason
    const reasonInput = screen.getByLabelText(/Reason for visit/);
    await user.type(reasonInput, "Headache");

    // Submit
    const submitBtn = screen.getByRole("button", { name: "Submit Appointment Request" });
    await user.click(submitBtn);

    await waitFor(() => expect(requested).toBe(true));
  });

  it("views notifications and marks read", async () => {
    const user = userEvent.setup();
    const notif: NotificationItem = {
      id: "n-1",
      user_id: "u-2",
      type: "appointment_approved",
      title: "Appointment Approved",
      body: "Your appointment with Dr. Okafor has been approved.",
      created_at: "2026-11-02T07:00:00+00:00",
      read_at: null,
    };

    let marked = false;
    patientRoutes(
      { entries: [], appointments: [], notifications: [notif] },
      {
        "POST /notifications/n-1/read": () => {
          marked = true;
          return { ...notif, read_at: "2026-11-02T08:00:00+00:00" };
        },
      },
    );

    renderSignedIn("/patient/notifications");
    expect(await screen.findByText("Appointment Approved")).toBeInTheDocument();

    const markBtn = screen.getByRole("button", { name: "Mark read" });
    await user.click(markBtn);

    await waitFor(() => expect(marked).toBe(true));
  });

  it("updates patient profile", async () => {
    const user = userEvent.setup();
    let updatedPhone = "";

    patientRoutes(
      { entries: [], appointments: [] },
      {
        "PATCH /patients/me": ((c: { body: { phone: string } }) => {
          updatedPhone = c.body.phone;
          return { ...patientProfile, phone: c.body.phone };
        }) as never,
      },
    );

    renderSignedIn("/patient/profile");
    const phoneInput = await screen.findByLabelText("Phone Number");
    await user.clear(phoneInput);
    await user.type(phoneInput, "0809 999 8888");

    const saveBtn = screen.getByRole("button", { name: "Save Changes" });
    await user.click(saveBtn);

    await waitFor(() => expect(updatedPhone).toBe("0809 999 8888"));
  });
});

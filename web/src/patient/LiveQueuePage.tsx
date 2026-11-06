import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { patientApi } from "../api/patient";
import type { Appointment, QueueEntry } from "../api/types";
import { ConfirmDialog, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { formatDateTime, formatTime } from "../format";
import {
  getNotificationPermission,
  notifyPatientCalled,
  notifyPatientNext,
  requestNotificationPermission,
  type NotificationPermissionState,
} from "../notifications/browserNotifications";
import type { PatientData } from "./usePatientData";

interface Props {
  data: PatientData;
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
}

export function LiveQueuePage({ data, run, busy }: Props) {
  const [cancellingEntry, setCancellingEntry] = useState<QueueEntry | null>(null);
  const [cancellingAppt, setCancellingAppt] = useState<Appointment | null>(null);
  const [permState, setPermState] = useState<NotificationPermissionState>(() => getNotificationPermission());
  const prevStatus = useRef<string | null>(null);
  const prevPosition = useRef<number | null>(null);

  const activeEntry = data.queueEntries.find(
    (e) => e.status === "waiting" || e.status === "called" || e.status === "in_consultation",
  );

  useEffect(() => {
    if (!activeEntry) return;

    if (activeEntry.status === "called" && prevStatus.current && prevStatus.current !== "called") {
      notifyPatientCalled(activeEntry.ticket_number, activeEntry.doctor.name);
    } else if (
      activeEntry.status === "waiting" &&
      activeEntry.position === 1 &&
      prevPosition.current !== null &&
      prevPosition.current !== 1
    ) {
      notifyPatientNext(activeEntry.ticket_number, activeEntry.doctor.name);
    }

    prevStatus.current = activeEntry.status;
    prevPosition.current = activeEntry.position ?? null;
  }, [activeEntry]);

  if (data.loading && data.queueEntries.length === 0 && data.appointments.length === 0) {
    return <Spinner label="Loading your queue status…" />;
  }

  // Check if patient has an approved appointment for today that isn't checked in yet
  const today = new Date().toISOString().slice(0, 10);
  const todaysApproved = data.appointments.find(
    (a) =>
      a.status === "approved" &&
      a.start_at.slice(0, 10) === today &&
      !data.queueEntries.some((q) => q.appointment_id === a.id),
  );

  const checkIn = (appointmentId: string) =>
    run(() => patientApi.checkIn(appointmentId), "Checked in successfully! You are now in the queue.");

  const cancelEntry = (entry: QueueEntry, reason?: string | null) =>
    run(
      () => patientApi.cancelQueueEntry(entry.id, reason, entry.version),
      "You have left the queue.",
    );

  const cancelAppt = (appt: Appointment, reason?: string | null) =>
    run(
      () => patientApi.cancelAppointment(appt.id, reason, appt.version),
      "Appointment cancelled.",
    );

  return (
    <div className="stack">
      {data.error != null && <ErrorBox error={data.error} onRetry={() => void data.reload()} />}

      {activeEntry ? (
        <section className="card stack" aria-labelledby="queue-heading">
          <div className="card-header">
            <h2 id="queue-heading">Your Queue Status</h2>
            <StatusBadge status={activeEntry.status} />
          </div>

          <div style={{ textAlign: "center", padding: "1.25rem 0" }}>
            <p className="muted" style={{ margin: 0 }}>Your Ticket</p>
            <div
              style={{
                fontSize: "clamp(2.4rem, 8vw, 3.4rem)",
                fontWeight: 800,
                color: "var(--blue)",
                lineHeight: 1.1,
                margin: "0.25rem 0",
              }}
              aria-label={`Ticket number ${activeEntry.ticket_number}`}
            >
              {activeEntry.ticket_number}
            </div>
            <p className="small muted">
              Checked in at {formatTime(activeEntry.checked_in_at, activeEntry.hospital_timezone)}
            </p>
          </div>

          {permState === "default" && (
            <div
              style={{
                background: "#f0f8ff",
                border: "1px solid #c8e1ff",
                borderRadius: 8,
                padding: "0.85rem 1rem",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "0.75rem",
                flexWrap: "wrap",
              }}
            >
              <div style={{ flex: "1 1 200px" }}>
                <strong>🔔 Push & Sound Alerts</strong>
                <p className="small muted" style={{ margin: "0.2rem 0 0" }}>
                  Get notified when your turn is called, even if your phone or browser tab is in the background.
                </p>
              </div>
              <button
                type="button"
                className="primary"
                style={{ padding: "0.5rem 0.85rem", fontSize: "0.9rem", flex: "1 1 auto" }}
                onClick={async () => {
                  const res = await requestNotificationPermission();
                  setPermState(res);
                }}
              >
                Enable Notifications
              </button>
            </div>
          )}

          {permState === "granted" && (
            <div
              style={{
                background: "#f6ffed",
                border: "1px solid #b7eb8f",
                borderRadius: 6,
                padding: "0.4rem 0.8rem",
                fontSize: "0.85rem",
                color: "#274916",
                display: "flex",
                alignItems: "center",
              }}
            >
              <span>🔔 Push notifications active</span>
            </div>
          )}

          {activeEntry.status === "called" && (
            <div
              className="banner"
              style={{
                background: "#ffe8cc",
                border: "2px solid #e07a00",
                borderRadius: 8,
                padding: "1rem",
                textAlign: "center",
              }}
              role="alert"
            >
              <h3 style={{ margin: "0 0 0.5rem", color: "#8a4b00" }}>It's your turn!</h3>
              <p style={{ margin: 0, fontWeight: 600 }}>
                Please proceed to the consultation room for {activeEntry.doctor.name}.
              </p>
            </div>
          )}

          {activeEntry.status === "waiting" && (activeEntry.position === 1 || activeEntry.estimate?.state === "next") && (
            <div
              className="banner"
              style={{
                background: "#e8f4fd",
                border: "2px solid #0969da",
                borderRadius: 8,
                padding: "1rem",
                textAlign: "center",
              }}
              role="alert"
            >
              <h3 style={{ margin: "0 0 0.5rem", color: "#0969da" }}>You are next in line!</h3>
              <p style={{ margin: 0, fontWeight: 600 }}>
                Please make your way towards the consultation room for {activeEntry.doctor.name}.
              </p>
            </div>
          )}

          {activeEntry.status === "in_consultation" && (
            <div className="banner" style={{ background: "#dff6dd", borderRadius: 8, padding: "1rem" }} role="status">
              <p style={{ margin: 0, fontWeight: 600, color: "#1e5e1e" }}>
                Consultation in progress with {activeEntry.doctor.name}.
              </p>
            </div>
          )}

          {activeEntry.status === "waiting" && (
            <div className="card" style={{ background: "#f8fafc", border: "1px solid #d0d7de" }}>
              <h3 style={{ margin: "0 0 0.5rem", fontSize: "1.05rem" }}>Line Position & Wait</h3>
              <p style={{ fontSize: "1.1rem", fontWeight: 700 }}>
                {activeEntry.position === 1
                  ? "You are next in line!"
                  : activeEntry.position != null
                    ? `${activeEntry.position - 1} patient(s) ahead of you`
                    : "Calculating position…"}
              </p>

              {activeEntry.queue_paused ? (
                <p className="error-text" role="status">
                  The doctor's queue is temporarily paused. Waiting times may be longer.
                </p>
              ) : (
                <>
                  <p style={{ fontWeight: 600, color: "var(--blue)" }}>
                    {activeEntry.estimate.min_minutes != null && activeEntry.estimate.max_minutes != null
                      ? `Estimated wait: ${activeEntry.estimate.min_minutes}–${activeEntry.estimate.max_minutes} minutes`
                      : activeEntry.estimate.explanation || "Wait time estimate will update shortly."}
                  </p>
                  {activeEntry.estimate.provisional && (
                    <p className="small muted">
                      Estimate is provisional and based on current consultation durations.
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          <div className="stack" style={{ fontSize: "0.95rem" }}>
            <p>
              <strong>Doctor:</strong> {activeEntry.doctor.name}
            </p>
            <p className="small muted">
              📱 SMS text alerts and real-time push updates are active for this ticket.
            </p>
            {activeEntry.check_in_method === "self" && !activeEntry.arrival_confirmed_at && (
              <p className="small muted" style={{ background: "#fff4ce", padding: "0.5rem", borderRadius: 6 }}>
                Reminder: Please let reception know when you arrive at the hospital.
              </p>
            )}
          </div>

          <button
            type="button"
            className="danger big"
            style={{ marginTop: "0.5rem" }}
            disabled={busy || activeEntry.status === "in_consultation"}
            onClick={() => setCancellingEntry(activeEntry)}
          >
            Leave queue
          </button>
        </section>
      ) : (
        <section className="card stack" aria-labelledby="no-queue-heading">
          <h2 id="no-queue-heading">Live Queue</h2>
          <p className="muted">You are not currently waiting in any hospital queue.</p>

          {todaysApproved ? (
            <div
              className="card stack"
              style={{ background: "#f0f7ff", border: "1px solid #cce3ff", marginTop: "0.5rem" }}
            >
              <h3>You have an appointment today</h3>
              <p>
                <strong>{todaysApproved.doctor_name}</strong> · {todaysApproved.department_name}
              </p>
              <p className="small muted">
                {todaysApproved.hospital_name} · {formatDateTime(todaysApproved.start_at, todaysApproved.hospital_timezone)}
              </p>
              <button
                type="button"
                className="primary big"
                disabled={busy}
                onClick={() => checkIn(todaysApproved.id)}
              >
                Check in now
              </button>
            </div>
          ) : (
            <div style={{ marginTop: "0.5rem" }}>
              <p className="small muted">
                When you have an approved appointment or check in at the hospital, your live ticket number and waiting
                time will appear here.
              </p>
              <Link to="/patient/book" className="button primary big" style={{ display: "block", textAlign: "center", textDecoration: "none" }}>
                Book an appointment
              </Link>
            </div>
          )}
        </section>
      )}

      {/* Upcoming Approved Appointments if any */}
      {!activeEntry && data.appointments.some((a) => a.status === "approved" && a.id !== todaysApproved?.id) && (
        <section className="card" aria-labelledby="upcoming-heading">
          <h2 id="upcoming-heading">Upcoming Approved Appointments</h2>
          <ul className="plain-list">
            {data.appointments
              .filter((a) => a.status === "approved" && a.id !== todaysApproved?.id)
              .map((a) => (
                <li
                  key={a.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: "0.75rem",
                    padding: "0.75rem 0",
                  }}
                >
                  <div style={{ flex: "1 1 200px" }}>
                    <p style={{ margin: 0, fontWeight: 600 }}>{a.doctor_name}</p>
                    <p className="small muted" style={{ margin: "0.2rem 0 0" }}>
                      {formatDateTime(a.start_at, a.hospital_timezone)} · {a.hospital_name}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    style={{ minHeight: "38px", flexShrink: 0 }}
                    onClick={() => setCancellingAppt(a)}
                  >
                    Cancel
                  </button>
                </li>
              ))}
          </ul>
        </section>
      )}

      {cancellingEntry && (
        <ConfirmDialog
          title="Leave the queue?"
          confirmLabel="Leave queue"
          danger
          askReason="optional"
          message={`You will forfeit Ticket #${cancellingEntry.ticket_number} with ${cancellingEntry.doctor.name}.`}
          onCancel={() => setCancellingEntry(null)}
          onConfirm={(reason) => {
            const entry = cancellingEntry;
            setCancellingEntry(null);
            void cancelEntry(entry, reason);
          }}
        />
      )}

      {cancellingAppt && (
        <ConfirmDialog
          title="Cancel appointment?"
          confirmLabel="Cancel appointment"
          danger
          askReason="optional"
          message={`Are you sure you want to cancel your appointment with ${cancellingAppt.doctor_name}?`}
          onCancel={() => setCancellingAppt(null)}
          onConfirm={(reason) => {
            const appt = cancellingAppt;
            setCancellingAppt(null);
            void cancelAppt(appt, reason);
          }}
        />
      )}
    </div>
  );
}

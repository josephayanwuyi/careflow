import { useState } from "react";
import { Link } from "react-router-dom";

import { patientApi } from "../api/patient";
import type { Appointment } from "../api/types";
import { ConfirmDialog, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { formatDateTime, minutesUntil } from "../format";
import type { PatientData } from "./usePatientData";

interface Props {
  data: PatientData;
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
}

export function AppointmentsPage({ data, run, busy }: Props) {
  const [tab, setTab] = useState<"upcoming" | "past">("upcoming");
  const [cancelling, setCancelling] = useState<Appointment | null>(null);
  const [declining, setDeclining] = useState<Appointment | null>(null);

  if (data.loading && data.appointments.length === 0) {
    return <Spinner label="Loading appointments…" />;
  }

  const active = data.appointments.filter((a) =>
    ["requested", "approved", "reschedule_proposed"].includes(a.status),
  );
  const past = data.appointments.filter(
    (a) => !["requested", "approved", "reschedule_proposed"].includes(a.status),
  );

  const displayed = tab === "upcoming" ? active : past;

  const acceptReschedule = (a: Appointment) =>
    run(
      () => patientApi.acceptReschedule(a.id, a.version),
      "Rescheduled appointment accepted and confirmed.",
    );

  const declineReschedule = (a: Appointment, reason?: string | null) =>
    run(
      () => patientApi.declineReschedule(a.id, reason, a.version),
      "Reschedule proposal declined. Appointment cancelled.",
    );

  const cancelAppointment = (a: Appointment, reason?: string | null) =>
    run(
      () => patientApi.cancelAppointment(a.id, reason, a.version),
      "Appointment cancelled.",
    );

  const checkIn = (appointmentId: string) =>
    run(() => patientApi.checkIn(appointmentId), "Checked in successfully! You are now in the queue.");

  return (
    <div className="stack">
      {data.error != null && <ErrorBox error={data.error} onRetry={() => void data.reload()} />}

      <div className="appointments-nav">
        <div style={{ display: "flex", gap: "0.5rem", flex: "1 1 auto" }} role="tablist" aria-label="Appointment views">
          <button
            type="button"
            className={tab === "upcoming" ? "primary" : undefined}
            style={{ flex: "1 1 auto", minHeight: "44px" }}
            onClick={() => setTab("upcoming")}
            role="tab"
            aria-selected={tab === "upcoming"}
          >
            Upcoming ({active.length})
          </button>
          <button
            type="button"
            className={tab === "past" ? "primary" : undefined}
            style={{ flex: "1 1 auto", minHeight: "44px" }}
            onClick={() => setTab("past")}
            role="tab"
            aria-selected={tab === "past"}
          >
            Past ({past.length})
          </button>
        </div>
        <Link
          to="/patient/book"
          className="request-new-btn"
          style={{
            background: "var(--blue)",
            color: "white",
            padding: "0.6rem 1rem",
            borderRadius: 8,
            textDecoration: "none",
            fontWeight: 600,
            fontSize: "0.95rem",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            minHeight: "44px",
            whiteSpace: "nowrap",
          }}
        >
          + Request New
        </Link>
      </div>

      {displayed.length === 0 ? (
        <section className="card" style={{ textAlign: "center", padding: "2rem" }}>
          <p className="muted">
            {tab === "upcoming"
              ? "You have no upcoming appointments or pending requests."
              : "No past appointment history."}
          </p>
          {tab === "upcoming" && (
            <Link
              to="/patient/book"
              className="button primary"
              style={{ display: "inline-block", marginTop: "1rem", textDecoration: "none" }}
            >
              Book an appointment
            </Link>
          )}
        </section>
      ) : (
        <div className="stack">
          {displayed.map((a) => {
            const isProposed = a.status === "reschedule_proposed";
            const isApproved = a.status === "approved";
            const isRequested = a.status === "requested";

            // Check if eligible for check-in (e.g. today, within 60 minutes or late)
            const minLeft = minutesUntil(a.start_at);
            const canCheckIn =
              isApproved &&
              minLeft <= 60 &&
              !data.queueEntries.some((q) => q.appointment_id === a.id);

            return (
              <section
                key={a.id}
                className="card stack"
                style={{
                  borderLeft: isProposed
                    ? "5px solid #d97706"
                    : isApproved
                      ? "5px solid var(--blue)"
                      : undefined,
                }}
              >
                <div className="card-header" style={{ flexWrap: "wrap", alignItems: "flex-start", gap: "0.5rem" }}>
                  <div style={{ flex: "1 1 180px", minWidth: 0 }}>
                    <h3 style={{ margin: 0, fontSize: "1.1rem", wordBreak: "break-word" }}>{a.doctor_name}</h3>
                    <p className="small muted" style={{ margin: "0.2rem 0 0" }}>
                      {a.department_name} · {a.hospital_name}
                    </p>
                  </div>
                  <StatusBadge status={a.status} />
                </div>

                <div>
                  <p>
                    <strong>Scheduled Time:</strong> {formatDateTime(a.start_at, a.hospital_timezone)}
                  </p>
                  {a.reason_for_visit && (
                    <p className="muted small">Reason for visit: {a.reason_for_visit}</p>
                  )}
                </div>

                {/* Reschedule Proposal alert */}
                {isProposed && (
                  <div
                    className="banner warning"
                    style={{ background: "#fffbeb", border: "1px solid #fde68a", padding: "0.75rem", borderRadius: 8 }}
                  >
                    <p style={{ margin: "0 0 0.5rem", fontWeight: 700, color: "#92400e" }}>
                      Hospital proposed a new time:
                    </p>
                    <p style={{ margin: "0 0 0.25rem", fontWeight: 600 }}>
                      {formatDateTime(a.proposed_start_at ?? "", a.hospital_timezone)}
                    </p>
                    {a.status_reason && (
                      <p className="small muted" style={{ margin: "0 0 0.75rem" }}>
                        Reason: {a.status_reason}
                      </p>
                    )}
                    <div className="button-row" style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
                      <button
                        type="button"
                        className="primary"
                        style={{ flex: "1 1 140px" }}
                        disabled={busy}
                        onClick={() => acceptReschedule(a)}
                      >
                        Accept new time
                      </button>
                      <button
                        type="button"
                        className="danger"
                        style={{ flex: "1 1 140px" }}
                        disabled={busy}
                        onClick={() => setDeclining(a)}
                      >
                        Decline
                      </button>
                    </div>
                  </div>
                )}

                {/* Requested disclaimer */}
                {isRequested && (
                  <div
                    className="notice"
                    style={{ background: "#f8fafc", borderLeft: "4px solid #64748b", padding: "0.6rem 0.75rem" }}
                  >
                    <p className="small muted" style={{ margin: 0 }}>
                      This request has been submitted and is awaiting approval by the hospital receptionist.
                    </p>
                  </div>
                )}

                {/* Check in / cancel buttons */}
                <div className="button-row" style={{ marginTop: "0.5rem", display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
                  {canCheckIn && (
                    <button
                      type="button"
                      className="primary"
                      style={{ flex: "1 1 180px" }}
                      disabled={busy}
                      onClick={() => checkIn(a.id)}
                    >
                      Check in for appointment
                    </button>
                  )}
                  {isApproved && !canCheckIn && minLeft > 60 && (
                    <span className="small muted" style={{ alignSelf: "center", flex: "1 1 100%" }}>
                      Check-in opens 60 minutes before appointment.
                    </span>
                  )}
                  {(isRequested || isApproved) && (
                    <button
                      type="button"
                      style={{ flex: "1 1 140px" }}
                      disabled={busy}
                      onClick={() => setCancelling(a)}
                    >
                      Cancel appointment
                    </button>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {cancelling && (
        <ConfirmDialog
          title="Cancel appointment?"
          confirmLabel="Cancel appointment"
          danger
          askReason="optional"
          message={`Are you sure you want to cancel your appointment with ${cancelling.doctor_name}?`}
          onCancel={() => setCancelling(null)}
          onConfirm={(reason) => {
            const appt = cancelling;
            setCancelling(null);
            void cancelAppointment(appt, reason);
          }}
        />
      )}

      {declining && (
        <ConfirmDialog
          title="Decline reschedule proposal?"
          confirmLabel="Decline and cancel"
          danger
          askReason="optional"
          message="Declining the proposed time will cancel this appointment request. You can submit a new request afterwards."
          onCancel={() => setDeclining(null)}
          onConfirm={(reason) => {
            const appt = declining;
            setDeclining(null);
            void declineReschedule(appt, reason);
          }}
        />
      )}
    </div>
  );
}

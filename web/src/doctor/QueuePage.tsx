import { useState } from "react";

import { doctorApi } from "../api/doctor";
import type { QueueEntry } from "../api/types";
import { ConfirmDialog, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { formatTime, minutesSince } from "../format";
import { StatusCard } from "./StatusCard";
import type { DoctorData } from "./useDoctorData";

interface Props {
  hospitalId: string;
  data: DoctorData;
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
}

type Pending =
  | { kind: "complete" | "missed"; entry: QueueEntry }
  | { kind: "pause" }
  | null;

export function QueuePage({ hospitalId, data, run, busy }: Props) {
  const [pending, setPending] = useState<Pending>(null);
  const { status, queue, entries, timezone } = data;

  if (data.loading && !status) return <Spinner />;
  if (data.error && !status) return <ErrorBox error={data.error} onRetry={() => void data.reload()} />;
  if (!status) return null;

  const withDoctor = entries.find((e) => e.status === "called" || e.status === "in_consultation") ?? null;
  const waiting = entries.filter((e) => e.status === "waiting").sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  const missed = entries.filter((e) => e.status === "missed");

  const callBlocker =
    status.status !== "available" ? "Set yourself as available to call patients." :
    queue?.is_paused ? "Your queue is paused." :
    withDoctor ? "Finish with the current patient first." :
    waiting.length === 0 ? "No one is waiting." : null;

  const act = (entry: QueueEntry, action: string, message: string, body: Record<string, unknown> = {}) =>
    run(() => doctorApi.entryAction(entry.id, action, { expected_version: entry.version, ...body }), message);

  return (
    <div className="stack">
      {data.error != null && <ErrorBox error={data.error} onRetry={() => void data.reload()} />}
      <StatusCard hospitalId={hospitalId} timezone={timezone} status={status} withPatient={withDoctor !== null}
        run={run} />

      {queue?.is_paused && (
        <div className="banner warning" role="status">
          Your queue is paused{queue.paused_reason ? `: ${queue.paused_reason}` : ""}. Patients are told their wait is
          unknown.
          <button type="button" disabled={busy} onClick={() => void run(() => doctorApi.resume(queue.id), "Queue resumed.")}>
            Resume queue
          </button>
        </div>
      )}

      <section className="card" aria-labelledby="current-heading">
        <h2 id="current-heading">With you now</h2>
        {withDoctor ? (
          <div className="current-patient">
            <p className="patient-name">{withDoctor.patient?.full_name}</p>
            <p className="muted">
              Ticket {withDoctor.ticket_number} · {withDoctor.patient?.hospital_patient_number ?? "no hospital number"} ·{" "}
              {withDoctor.kind === "walk_in" ? "Walk-in" : "Appointment"}
            </p>
            {withDoctor.reason_for_visit && <p>Reason for visit: {withDoctor.reason_for_visit}</p>}
            <p>
              <StatusBadge status={withDoctor.status} />{" "}
              {withDoctor.status === "called"
                ? `Called at ${formatTime(withDoctor.called_at, timezone)}${(withDoctor.call_count ?? 1) > 1 ? ` (${withDoctor.call_count} calls)` : ""}`
                : `Started at ${formatTime(withDoctor.started_at, timezone)}`}
              {withDoctor.status === "called" && (
                <span
                  style={{
                    marginLeft: "0.5rem",
                    background: "#e8f4fd",
                    color: "#0969da",
                    border: "1px solid #c8e1ff",
                    fontSize: "0.8rem",
                    padding: "0.15rem 0.45rem",
                    borderRadius: 4,
                  }}
                >
                  SMS & Push Sent
                </span>
              )}
            </p>
            <div className="button-row">
              {withDoctor.allowed_actions.includes("start") && (
                <button type="button" className="primary" disabled={busy}
                  onClick={() => void act(withDoctor, "start", "Consultation started.")}>Start consultation</button>
              )}
              {withDoctor.allowed_actions.includes("recall") && (
                <button type="button" disabled={busy}
                  onClick={() => void act(withDoctor, "recall", "Patient called again.")}>Call again</button>
              )}
              {withDoctor.allowed_actions.includes("mark_missed") && (
                <button type="button" disabled={busy}
                  onClick={() => setPending({ kind: "missed", entry: withDoctor })}>Didn't come</button>
              )}
              {withDoctor.allowed_actions.includes("complete") && (
                <button type="button" className="primary" disabled={busy}
                  onClick={() => setPending({ kind: "complete", entry: withDoctor })}>Complete consultation</button>
              )}
            </div>
          </div>
        ) : (
          <p className="muted">No patient is with you.</p>
        )}
      </section>

      <section className="card" aria-labelledby="next-heading">
        <h2 id="next-heading">Next patient</h2>
        {waiting[0] ? (
          <p>
            <strong>{waiting[0].patient?.full_name}</strong> · ticket {waiting[0].ticket_number}
            {(waiting[0].priority_level ?? 0) > 0 && <span className="badge badge-priority"> Priority {waiting[0].priority_level}</span>}
          </p>
        ) : (
          <p className="muted">No one is waiting.</p>
        )}
        <button type="button" className="primary big" disabled={busy || callBlocker !== null}
          aria-describedby={callBlocker ? "call-blocker" : undefined}
          onClick={() => queue && void run(() => doctorApi.callNext(queue.id), "Next patient called.")}>
          Call next patient
        </button>
        {callBlocker && <p id="call-blocker" className="muted">{callBlocker}</p>}
      </section>

      <section className="card" aria-labelledby="waiting-heading">
        <div className="card-header">
          <h2 id="waiting-heading">Waiting ({waiting.length})</h2>
          {queue && !queue.is_paused && (
            <button type="button" disabled={busy} onClick={() => setPending({ kind: "pause" })}>Pause queue</button>
          )}
        </div>
        {waiting.length === 0 ? (
          <p className="muted">{queue ? "No one is waiting." : "No patients have checked in to your queue today."}</p>
        ) : (
          <ol className="queue-list">
            {waiting.map((e) => (
              <li key={e.id}>
                <span className="ticket" aria-label={`Ticket ${e.ticket_number}`}>{e.ticket_number}</span>
                <span className="who">
                  <span className="name">{e.patient?.full_name}</span>
                  <span className="muted">
                    {e.kind === "walk_in" ? "Walk-in" : `Appt ${formatTime(e.appointment_start_at, timezone)}`}
                    {" · "}waiting {minutesSince(e.checked_in_at)} min
                    {e.check_in_method === "self" && !e.arrival_confirmed_at && " · arrival not confirmed"}
                  </span>
                </span>
                {(e.priority_level ?? 0) > 0 && (
                  <span className="badge badge-priority" title={e.priority_reason ?? undefined}>P{e.priority_level}</span>
                )}
              </li>
            ))}
          </ol>
        )}
        {missed.length > 0 && (
          <p className="muted">{missed.length} patient(s) missed their call. Reception can return them to the queue.</p>
        )}
      </section>

      {pending?.kind === "complete" && (
        <ConfirmDialog title="Complete consultation?" confirmLabel="Complete"
          message={`Mark the consultation with ${pending.entry.patient?.full_name} as completed.`}
          onCancel={() => setPending(null)}
          onConfirm={() => { setPending(null); void act(pending.entry, "complete", "Consultation completed."); }} />
      )}
      {pending?.kind === "missed" && (
        <ConfirmDialog title="Patient didn't come?" confirmLabel="Mark as missed" danger askReason="optional"
          message={`${pending.entry.patient?.full_name} will be marked as having missed their call. Reception can return them to the queue.`}
          onCancel={() => setPending(null)}
          onConfirm={(reason) => { setPending(null); void act(pending.entry, "mark-missed", "Marked as missed.", { reason }); }} />
      )}
      {pending?.kind === "pause" && queue && (
        <ConfirmDialog title="Pause your queue?" confirmLabel="Pause queue" askReason="optional"
          message="No patients can be called while paused, and waiting patients are told their wait is unknown."
          onCancel={() => setPending(null)}
          onConfirm={(reason) => { setPending(null); void run(() => doctorApi.pause(queue.id, reason), "Queue paused."); }} />
      )}
    </div>
  );
}

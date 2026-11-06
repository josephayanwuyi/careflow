import { useId, useState } from "react";

import { doctorApi } from "../api/doctor";
import type { DoctorAvailability } from "../api/types";
import { ConfirmDialog, Modal, StatusBadge } from "../components/ui";
import { formatTime } from "../format";

// Mirrors the server's rules so only sensible buttons are shown; the server still validates every change.
export const NEXT_STATUSES: Record<string, string[]> = {
  off_duty: ["checked_in"],
  checked_out: ["checked_in"],
  checked_in: ["available", "on_break", "temporarily_unavailable", "checked_out"],
  available: ["on_break", "temporarily_unavailable", "checked_out"],
  on_break: ["available", "temporarily_unavailable", "checked_out"],
  temporarily_unavailable: ["available", "on_break", "checked_out"],
};

const BUTTON_LABELS: Record<string, string> = {
  checked_in: "I've arrived",
  available: "I'm available",
  on_break: "Take a break",
  temporarily_unavailable: "Step away",
  checked_out: "Leave for the day",
};

const SOURCE_LABELS: Record<string, string> = {
  self_reported: "set by you",
  staff_verified: "confirmed by reception",
};

const RETURN_OPTIONS = [10, 15, 30, 45, 60, 90];

interface Props {
  hospitalId: string;
  timezone: string;
  status: DoctorAvailability;
  withPatient: boolean;
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
}

export function StatusCard({ hospitalId, timezone, status, withPatient, run }: Props) {
  const [asking, setAsking] = useState<string | null>(null);
  const options = NEXT_STATUSES[status.status] ?? [];

  const choose = (next: string) => {
    if (next === "on_break" || next === "temporarily_unavailable" || next === "checked_out") setAsking(next);
    else void run(() => doctorApi.setStatus(hospitalId, next), `Status: ${BUTTON_LABELS[next]}`);
  };

  return (
    <section className="card" aria-labelledby="status-heading">
      <h2 id="status-heading">Your status</h2>
      <p className="status-line">
        <StatusBadge status={status.status === "checked_in" ? "checked_in_doctor" : status.status}
          label={status.status === "off_duty" ? "Not checked in" : undefined} />
        {status.status_source && (
          <span className="muted">
            {" "}{SOURCE_LABELS[status.status_source] ?? ""}
            {status.status_updated_at && ` at ${formatTime(status.status_updated_at, timezone)}`}
          </span>
        )}
      </p>
      {status.expected_return_at && (
        <p className="muted">Expected back at {formatTime(status.expected_return_at, timezone)}</p>
      )}
      <p className="muted">Patients see: {status.public_status.replace(/_/g, " ")}</p>
      <div className="button-row">
        {options.map((next) => (
          <button key={next} type="button" className={next === "available" ? "primary" : undefined}
            disabled={next === "checked_out" && withPatient}
            title={next === "checked_out" && withPatient ? "Finish the current consultation first" : undefined}
            onClick={() => choose(next)}>
            {BUTTON_LABELS[next]}
          </button>
        ))}
      </div>
      {asking === "checked_out" && (
        <ConfirmDialog title="Leave for the day?" confirmLabel="Leave for the day"
          message="Patients still waiting will see that you are unavailable. Reception can transfer them."
          onCancel={() => setAsking(null)}
          onConfirm={() => {
            setAsking(null);
            void run(() => doctorApi.setStatus(hospitalId, "checked_out"), "You have left for the day.");
          }} />
      )}
      {(asking === "on_break" || asking === "temporarily_unavailable") && (
        <AwayDialog kind={asking} onCancel={() => setAsking(null)}
          onConfirm={(minutes, note) => {
            const target = asking;
            setAsking(null);
            const expected = minutes ? new Date(Date.now() + minutes * 60_000).toISOString() : null;
            void run(() => doctorApi.setStatus(hospitalId, target, { expected_return_at: expected, note }),
              `Status: ${BUTTON_LABELS[target]}`);
          }} />
      )}
    </section>
  );
}

function AwayDialog(props: { kind: string; onCancel: () => void; onConfirm: (minutes: number | null, note: string | null) => void }) {
  const [minutes, setMinutes] = useState<string>("15");
  const [note, setNote] = useState("");
  const selectId = useId();
  const noteId = useId();
  return (
    <Modal title={props.kind === "on_break" ? "Take a break" : "Step away"} onClose={props.onCancel}>
      <p>An expected return time lets patients see a realistic wait. Without it they are told the wait is unknown.</p>
      <div className="field">
        <label htmlFor={selectId}>Back in</label>
        <select id={selectId} value={minutes} onChange={(e) => setMinutes(e.target.value)}>
          {RETURN_OPTIONS.map((m) => <option key={m} value={m}>{m} minutes</option>)}
          <option value="">Not sure</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor={noteId}>Note for reception (optional)</label>
        <input id={noteId} value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="actions">
        <button type="button" onClick={props.onCancel}>Cancel</button>
        <button type="button" className="primary"
          onClick={() => props.onConfirm(minutes ? Number(minutes) : null, note.trim() || null)}>
          Confirm
        </button>
      </div>
    </Modal>
  );
}

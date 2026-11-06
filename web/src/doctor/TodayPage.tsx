import { useCallback, useEffect, useState } from "react";

import { doctorApi } from "../api/doctor";
import type { Appointment, QueueEntry } from "../api/types";
import { ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { formatTime, todayIn } from "../format";

export function TodayPage({ hospitalId, timezone, queueId, refreshKey }: {
  hospitalId: string; timezone: string; queueId: string | null; refreshKey: number;
}) {
  const [appointments, setAppointments] = useState<Appointment[] | null>(null);
  const [finished, setFinished] = useState<QueueEntry[]>([]);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const day = todayIn(timezone);
      const [page, entries] = await Promise.all([
        doctorApi.todaysAppointments(hospitalId, day),
        queueId ? doctorApi.entries(queueId, true) : Promise.resolve([] as QueueEntry[]),
      ]);
      setAppointments(page.items);
      setFinished(entries.filter((e) => e.status === "completed"));
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, [hospitalId, timezone, queueId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  if (error) return <ErrorBox error={error} onRetry={() => void load()} />;
  if (!appointments) return <Spinner />;

  const active = appointments.filter((a) => !["cancelled", "rejected", "expired"].includes(a.status));
  return (
    <div className="stack">
      <section className="card" aria-labelledby="appts-heading">
        <h2 id="appts-heading">Today's appointments ({active.length})</h2>
        {active.length === 0 ? (
          <p className="muted">You have no appointments today.</p>
        ) : (
          <ul className="plain-list">
            {active.map((a) => (
              <li key={a.id}>
                <span className="time">{formatTime(a.start_at, timezone)}</span>
                <span className="who">
                  <span className="name">{a.patient?.full_name}</span>
                  {a.reason_for_visit && <span className="muted">{a.reason_for_visit}</span>}
                </span>
                <StatusBadge status={a.status} />
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="card" aria-labelledby="done-heading">
        <h2 id="done-heading">Completed consultations ({finished.length})</h2>
        {finished.length === 0 ? (
          <p className="muted">None yet today.</p>
        ) : (
          <ul className="plain-list">
            {finished.map((e) => (
              <li key={e.id}>
                <span className="time">{formatTime(e.completed_at, timezone)}</span>
                <span className="who"><span className="name">{e.patient?.full_name}</span>
                  <span className="muted">Ticket {e.ticket_number}</span></span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

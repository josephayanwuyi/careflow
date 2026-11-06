import { useCallback, useEffect, useState } from "react";

import { doctorApi } from "../api/doctor";
import type { StatusEvent } from "../api/types";
import { ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { formatDateTime } from "../format";

export function HistoryPage({ doctorId, timezone, refreshKey }: { doctorId: string; timezone: string; refreshKey: number }) {
  const [events, setEvents] = useState<StatusEvent[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setEvents(await doctorApi.statusHistory(doctorId));
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, [doctorId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  if (error) return <ErrorBox error={error} onRetry={() => void load()} />;
  if (!events) return <Spinner />;
  return (
    <section className="card" aria-labelledby="history-heading">
      <h2 id="history-heading">Your availability history</h2>
      {events.length === 0 ? (
        <p className="muted">No status changes recorded yet.</p>
      ) : (
        <ul className="plain-list">
          {events.map((e, i) => (
            <li key={`${e.occurred_at}-${i}`}>
              <span className="time">{formatDateTime(e.occurred_at, timezone)}</span>
              <StatusBadge status={e.status === "checked_in" ? "checked_in_doctor" : e.status} />
              <span className="muted">{e.source === "staff_verified" ? "confirmed by reception" : "set by you"}
                {e.note ? ` · ${e.note}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

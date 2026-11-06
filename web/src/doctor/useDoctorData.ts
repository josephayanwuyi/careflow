import { useCallback, useEffect, useRef, useState } from "react";

import { doctorApi } from "../api/doctor";
import { subscribe, type StreamState } from "../api/events";
import type { DoctorAvailability, LiveEvent, QueueEntry, QueueSummary } from "../api/types";

export interface DoctorData {
  timezone: string;
  status: DoctorAvailability | null;
  queue: QueueSummary | null;
  entries: QueueEntry[];
  loading: boolean;
  error: unknown;
  stream: StreamState;
  reload: () => Promise<void>;
}

/**
 * Loads the doctor's own status and today's queue, and keeps them current:
 * live updates trigger a (debounced) reload; a periodic reload covers gaps,
 * every 60 s while live or every 15 s while the live connection is down.
 */
export function useDoctorData(hospitalId: string, doctorId: string): DoctorData {
  const [timezone, setTimezone] = useState("Africa/Lagos");
  const [status, setStatus] = useState<DoctorAvailability | null>(null);
  const [queue, setQueue] = useState<QueueSummary | null>(null);
  const [entries, setEntries] = useState<QueueEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [stream, setStream] = useState<StreamState>("connecting");
  const queueIdRef = useRef<string | null>(null);
  const generation = useRef(0);

  const reload = useCallback(async () => {
    const gen = ++generation.current;
    try {
      const [own, queues] = await Promise.all([doctorApi.myStatus(hospitalId), doctorApi.myQueues(hospitalId)]);
      const today = queues.find((q) => q.doctor.id === doctorId) ?? null;
      const list = today ? await doctorApi.entries(today.id) : [];
      if (gen !== generation.current) return; // a newer reload finished first
      queueIdRef.current = today?.id ?? null;
      setStatus(own);
      setQueue(today);
      setEntries(list);
      setError(null);
    } catch (err) {
      if (gen === generation.current) setError(err);
    } finally {
      if (gen === generation.current) setLoading(false);
    }
  }, [hospitalId, doctorId]);

  useEffect(() => {
    let cancelled = false;
    doctorApi.hospital(hospitalId).then((h) => !cancelled && setTimezone(h.timezone)).catch(() => undefined);
    setLoading(true);
    void reload();
    return () => {
      cancelled = true;
    };
  }, [hospitalId, reload]);

  // Live updates.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const relevant = (e: LiveEvent) =>
      e.doctor_id === doctorId || (e.queue_id != null && e.queue_id === queueIdRef.current);
    const stop = subscribe(
      (e) => {
        if (!relevant(e)) return;
        clearTimeout(timer);
        timer = setTimeout(() => void reload(), 300);
      },
      (s) => {
        setStream(s);
        if (s === "live") void reload(); // catch up on anything missed while disconnected
      },
    );
    return () => {
      clearTimeout(timer);
      stop();
    };
  }, [doctorId, reload]);

  // Fallback polling.
  useEffect(() => {
    const id = setInterval(() => void reload(), stream === "live" ? 60_000 : 15_000);
    return () => clearInterval(id);
  }, [stream, reload]);

  return { timezone, status, queue, entries, loading, error, stream, reload };
}

import { useCallback, useEffect, useRef, useState } from "react";

import { subscribe, type StreamState } from "../api/events";
import { patientApi } from "../api/patient";
import type { Appointment, LiveEvent, NotificationItem, QueueEntry } from "../api/types";

export interface PatientData {
  queueEntries: QueueEntry[];
  appointments: Appointment[];
  notifications: NotificationItem[];
  unreadCount: number;
  loading: boolean;
  error: unknown;
  stream: StreamState;
  reload: () => Promise<void>;
}

export function usePatientData(patientId: string | null): PatientData {
  const [queueEntries, setQueueEntries] = useState<QueueEntry[]>([]);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [stream, setStream] = useState<StreamState>("connecting");
  const generation = useRef(0);

  const reload = useCallback(async () => {
    if (!patientId) return;
    const gen = ++generation.current;
    try {
      const [queueRes, apptRes, notifRes] = await Promise.all([
        patientApi.myQueue(),
        patientApi.myAppointments(undefined, 50),
        patientApi.notifications(false, 20),
      ]);
      if (gen !== generation.current) return;
      setQueueEntries(queueRes);
      setAppointments(apptRes.items);
      setNotifications(notifRes.items);
      setUnreadCount(notifRes.unread_count);
      setError(null);
    } catch (err) {
      if (gen === generation.current) setError(err);
    } finally {
      if (gen === generation.current) setLoading(false);
    }
  }, [patientId]);

  useEffect(() => {
    setLoading(true);
    void reload();
  }, [reload]);

  // Real-time updates via SSE stream
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = subscribe(
      (_event: LiveEvent) => {
        clearTimeout(timer);
        timer = setTimeout(() => void reload(), 300);
      },
      (s) => {
        setStream(s);
        if (s === "live") void reload();
      },
    );
    return () => {
      clearTimeout(timer);
      stop();
    };
  }, [reload]);

  // Fallback periodic refresh
  useEffect(() => {
    const id = setInterval(() => void reload(), stream === "live" ? 60_000 : 15_000);
    return () => clearInterval(id);
  }, [stream, reload]);

  return { queueEntries, appointments, notifications, unreadCount, loading, error, stream, reload };
}

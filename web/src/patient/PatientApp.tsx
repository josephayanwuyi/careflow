import { useCallback, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { errorMessage } from "../components/ui";
import { AppointmentsPage } from "./AppointmentsPage";
import { BookAppointmentPage } from "./BookAppointmentPage";
import { LiveQueuePage } from "./LiveQueuePage";
import { NotificationsPage } from "./NotificationsPage";
import { ProfilePage } from "./ProfilePage";
import { usePatientData } from "./usePatientData";

const STREAM_TEXT = { live: "Live", connecting: "Connecting…", offline: "Offline – refreshing every 15 s" } as const;

export function PatientApp() {
  const { user, signOut } = useAuth();
  const data = usePatientData(user?.patient_id ?? null);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (action: () => Promise<unknown>, success: string) => {
      setBusy(true);
      setMessage(null);
      try {
        await action();
        setMessage({ text: success, error: false });
      } catch (err) {
        const stale = err instanceof ApiError && ["stale_version", "invalid_transition"].includes(err.code);
        setMessage({ text: errorMessage(err) + (stale ? " The screen has been updated." : ""), error: true });
      } finally {
        setBusy(false);
        await data.reload();
      }
    },
    [data],
  );

  if (!user?.patient_id) {
    return (
      <main className="page narrow">
        <h1>CareFlow</h1>
        <p>This area is for patients. Your account does not have a linked patient profile.</p>
        <button type="button" onClick={() => void signOut()}>
          Sign out
        </button>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="brand">CareFlow</p>
          <p className="muted small">{user?.full_name}</p>
        </div>
        <div className="topbar-right">
          <span className={`stream stream-${data.stream}`} role="status" aria-live="polite">
            {STREAM_TEXT[data.stream]}
          </span>
          <button type="button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>

      <nav className="tabs" aria-label="Patient sections">
        <NavLink to="/patient" end>
          Queue
        </NavLink>
        <NavLink to="/patient/appointments">Appointments</NavLink>
        <NavLink to="/patient/book">Book</NavLink>
        <NavLink to="/patient/notifications">
          Notifications {data.unreadCount > 0 ? `(${data.unreadCount})` : ""}
        </NavLink>
        <NavLink to="/patient/profile">Profile</NavLink>
      </nav>

      <div
        className={`toast ${message?.error ? "toast-error" : ""}`}
        role={message?.error ? "alert" : "status"}
        aria-live="polite"
      >
        {message?.text}
      </div>

      <main className="page">
        <Routes>
          <Route index element={<LiveQueuePage data={data} run={run} busy={busy} />} />
          <Route path="appointments" element={<AppointmentsPage data={data} run={run} busy={busy} />} />
          <Route path="book" element={<BookAppointmentPage run={run} busy={busy} />} />
          <Route path="notifications" element={<NotificationsPage data={data} run={run} busy={busy} />} />
          <Route path="profile" element={<ProfilePage run={run} busy={busy} />} />
          <Route path="*" element={<Navigate to="/patient" replace />} />
        </Routes>
      </main>
    </div>
  );
}

import { useCallback, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { ApiError } from "../api/client";
import { doctorMemberships, useAuth } from "../auth/AuthContext";
import { errorMessage } from "../components/ui";
import { HistoryPage } from "./HistoryPage";
import { QueuePage } from "./QueuePage";
import { TodayPage } from "./TodayPage";
import { useDoctorData } from "./useDoctorData";

const STREAM_TEXT = { live: "Live", connecting: "Connecting…", offline: "Offline – refreshing every 15 s" } as const;

export function DoctorApp() {
  const { user, signOut } = useAuth();
  const memberships = doctorMemberships(user);
  const [hospitalId, setHospitalId] = useState(memberships[0]?.hospital_id ?? "");
  const membership = memberships.find((m) => m.hospital_id === hospitalId) ?? memberships[0];

  if (!membership) {
    return (
      <main className="page narrow">
        <h1>CareFlow</h1>
        <p>This area is for doctors. Your account has no doctor role at any hospital.</p>
        <button type="button" onClick={() => void signOut()}>Sign out</button>
      </main>
    );
  }
  return (
    <DoctorWorkspace key={membership.hospital_id} hospitalId={membership.hospital_id}
      doctorId={membership.doctor_id as string} hospitalName={membership.hospital_name}
      hospitals={memberships.map((m) => ({ id: m.hospital_id, name: m.hospital_name }))}
      onHospitalChange={setHospitalId} />
  );
}

function DoctorWorkspace(props: {
  hospitalId: string; doctorId: string; hospitalName: string;
  hospitals: Array<{ id: string; name: string }>; onHospitalChange: (id: string) => void;
}) {
  const { user, signOut } = useAuth();
  const data = useDoctorData(props.hospitalId, props.doctorId);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  /** Run one action at a time (no double submissions), report the result, then reload. */
  const run = useCallback(async (action: () => Promise<unknown>, success: string) => {
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
      setRefreshKey((k) => k + 1);
    }
  }, [data]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="brand">CareFlow</p>
          <p className="muted small">{user?.full_name} · {props.hospitalName}</p>
        </div>
        <div className="topbar-right">
          <span className={`stream stream-${data.stream}`} role="status" aria-live="polite">{STREAM_TEXT[data.stream]}</span>
          <button type="button" onClick={() => void signOut()}>Sign out</button>
        </div>
      </header>
      {props.hospitals.length > 1 && (
        <label className="hospital-select">
          Hospital{" "}
          <select value={props.hospitalId} onChange={(e) => props.onHospitalChange(e.target.value)}>
            {props.hospitals.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        </label>
      )}
      <nav className="tabs" aria-label="Doctor sections">
        <NavLink to="/doctor" end>Queue</NavLink>
        <NavLink to="/doctor/today">Today</NavLink>
        <NavLink to="/doctor/history">History</NavLink>
      </nav>
      <div className={`toast ${message?.error ? "toast-error" : ""}`} role={message?.error ? "alert" : "status"}
        aria-live="polite">
        {message?.text}
      </div>
      <main className="page">
        <Routes>
          <Route index element={<QueuePage hospitalId={props.hospitalId} data={data} run={run} busy={busy} />} />
          <Route path="today" element={<TodayPage hospitalId={props.hospitalId} timezone={data.timezone}
            queueId={data.queue?.id ?? null} refreshKey={refreshKey} />} />
          <Route path="history" element={<HistoryPage doctorId={props.doctorId} timezone={data.timezone}
            refreshKey={refreshKey} />} />
          <Route path="*" element={<Navigate to="/doctor" replace />} />
        </Routes>
      </main>
    </div>
  );
}

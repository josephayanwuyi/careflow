import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { doctorMemberships, useAuth } from "./auth/AuthContext";
import { Spinner } from "./components/ui";
import { DoctorApp } from "./doctor/DoctorApp";
import { LoginPage } from "./LoginPage";
import { PatientApp } from "./patient/PatientApp";

/** Sends each signed-in user to the area for their role. */
function Home() {
  const { user, signOut } = useAuth();
  if (doctorMemberships(user).length) return <Navigate to="/doctor" replace />;
  if (user?.patient_id) return <Navigate to="/patient" replace />;
  return (
    <main className="page narrow">
      <h1>CareFlow</h1>
      <p>Reception and administration staff use the CareFlow desktop application.</p>
      <button type="button" onClick={() => void signOut()}>
        Sign out
      </button>
    </main>
  );
}

export function App() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading")
    return (
      <main className="page narrow">
        <Spinner label="Starting CareFlow…" />
      </main>
    );
  if (status === "signedOut") {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname }} />} />
      </Routes>
    );
  }
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="/doctor/*" element={<DoctorApp />} />
      <Route path="/patient/*" element={<PatientApp />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

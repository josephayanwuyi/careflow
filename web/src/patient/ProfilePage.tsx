import { useEffect, useState } from "react";

import { patientApi } from "../api/patient";
import type { PatientProfile } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ErrorBox, Spinner } from "../components/ui";

interface Props {
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
}

export function ProfilePage({ run, busy }: Props) {
  const { user, signOut } = useAuth();
  const [profile, setProfile] = useState<PatientProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  // Edit fields
  const [phone, setPhone] = useState("");
  const [dob, setDob] = useState("");
  const [sex, setSex] = useState("");

  const load = () => {
    setLoading(true);
    patientApi
      .myProfile()
      .then((p) => {
        setProfile(p);
        setPhone(p.phone || "");
        setDob(p.date_of_birth || "");
        setSex(p.sex || "");
        setError(null);
      })
      .catch((err) => setError(err))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const saveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    await run(
      () =>
        patientApi.updateProfile({
          phone: phone.trim() || null,
          date_of_birth: dob || null,
          sex: sex || null,
        }),
      "Profile updated successfully.",
    );
    load();
  };

  if (loading && !profile) return <Spinner label="Loading your profile…" />;

  return (
    <div className="stack">
      {error != null && <ErrorBox error={error} onRetry={load} />}

      <section className="card stack" aria-labelledby="profile-heading">
        <h2 id="profile-heading">Your Patient Profile</h2>

        <form onSubmit={saveProfile} className="stack">
          <div className="field">
            <label>Full Name</label>
            <input type="text" value={profile?.full_name ?? user?.full_name ?? ""} disabled />
            <span className="small muted">Contact reception if you need to officially update your legal name.</span>
          </div>

          <div className="field">
            <label>Email Address</label>
            <input type="email" value={user?.email ?? ""} disabled />
          </div>

          <div className="field">
            <label htmlFor="profile-phone">Phone Number</label>
            <input
              id="profile-phone"
              type="tel"
              value={phone}
              placeholder="e.g. 0803 123 4567"
              onChange={(e) => setPhone(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="profile-dob">Date of Birth</label>
            <input
              id="profile-dob"
              type="date"
              value={dob}
              onChange={(e) => setDob(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="profile-sex">Gender / Sex</label>
            <select id="profile-sex" value={sex} onChange={(e) => setSex(e.target.value)}>
              <option value="">Select (optional)</option>
              <option value="female">Female</option>
              <option value="male">Male</option>
              <option value="other">Other</option>
              <option value="undisclosed">Prefer not to say</option>
            </select>
          </div>

          <button type="submit" className="primary big" disabled={busy}>
            {busy ? "Saving…" : "Save Changes"}
          </button>
        </form>
      </section>

      <section className="card stack" aria-labelledby="account-heading">
        <h2 id="account-heading">Account</h2>
        <p className="muted small">Sign out of your CareFlow patient account on this browser.</p>
        <button type="button" className="danger big" onClick={() => void signOut()}>
          Sign out
        </button>
      </section>
    </div>
  );
}

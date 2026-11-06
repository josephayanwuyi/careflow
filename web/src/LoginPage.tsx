import { useId, useState, type FormEvent } from "react";

import { useAuth } from "./auth/AuthContext";
import { errorMessage } from "./components/ui";

export function LoginPage() {
  const { signIn, signUp, notice } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const emailId = useId();
  const passwordId = useId();
  const nameId = useId();
  const phoneId = useId();
  const dobId = useId();

  const toggleMode = () => {
    setMode((m) => (m === "login" ? "register" : "login"));
    setError(null);
    setPassword("");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (mode === "login") {
      if (!email || !password) {
        setError("Enter your email and password.");
        return;
      }
      setBusy(true);
      setError(null);
      try {
        await signIn(email.trim(), password);
      } catch (err) {
        setError(errorMessage(err));
        setPassword("");
      } finally {
        setBusy(false);
      }
    } else {
      if (!fullName.trim() || !email.trim() || !password) {
        setError("Please enter your name, email, and a password.");
        return;
      }
      if (password.length < 10) {
        setError("Password must be at least 10 characters long.");
        return;
      }
      setBusy(true);
      setError(null);
      try {
        await signUp({
          full_name: fullName.trim(),
          email: email.trim(),
          password,
          phone: phone.trim() || null,
          date_of_birth: dateOfBirth || null,
        });
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setBusy(false);
      }
    }
  };

  return (
    <main className="page narrow">
      <h1 className="brand-title">CareFlow</h1>
      <p className="muted">{mode === "login" ? "Sign in to continue." : "Create your patient account."}</p>
      {notice && !error && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <form onSubmit={submit} noValidate className="card stack">
        {mode === "register" && (
          <div className="field">
            <label htmlFor={nameId}>Full Name</label>
            <input
              id={nameId}
              type="text"
              autoComplete="name"
              value={fullName}
              required
              placeholder="e.g. Amina Bello"
              onChange={(e) => setFullName(e.target.value)}
            />
          </div>
        )}
        <div className="field">
          <label htmlFor={emailId}>Email</label>
          <input
            id={emailId}
            type="email"
            autoComplete="username"
            value={email}
            required
            placeholder="you@example.com"
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={passwordId}>Password</label>
          <input
            id={passwordId}
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            required
            minLength={10}
            onChange={(e) => setPassword(e.target.value)}
          />
          {mode === "register" && <span className="small muted">At least 10 characters</span>}
        </div>
        {mode === "register" && (
          <>
            <div className="field">
              <label htmlFor={phoneId}>
                Phone <span className="small muted">(optional)</span>
              </label>
              <input
                id={phoneId}
                type="tel"
                autoComplete="tel"
                value={phone}
                placeholder="e.g. 0803 123 4567"
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor={dobId}>
                Date of Birth <span className="small muted">(optional)</span>
              </label>
              <input
                id={dobId}
                type="date"
                value={dateOfBirth}
                onChange={(e) => setDateOfBirth(e.target.value)}
              />
            </div>
          </>
        )}
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="primary big" disabled={busy}>
          {busy
            ? mode === "login"
              ? "Signing in…"
              : "Creating account…"
            : mode === "login"
            ? "Sign in"
            : "Register & Sign in"}
        </button>
        <button type="button" onClick={toggleMode} disabled={busy}>
          {mode === "login" ? "Need an account? Register" : "Already have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}

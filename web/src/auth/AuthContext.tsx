import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import * as client from "../api/client";
import type { Me } from "../api/types";

type Status = "loading" | "signedOut" | "signedIn";

interface AuthValue {
  status: Status;
  user: Me | null;
  notice: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (input: client.RegisterPatientInput) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<Me | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    client.setSessionExpiredHandler(() => {
      setUser(null);
      setStatus("signedOut");
      setNotice("Your session has ended. Please sign in again.");
    });
    let cancelled = false;
    client
      .restoreSession()
      .then((me) => {
        if (cancelled) return;
        setUser(me);
        setStatus(me ? "signedIn" : "signedOut");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus("signedOut");
        setNotice(err instanceof client.ApiError ? err.message : "Could not restore your session.");
      });
    return () => {
      cancelled = true;
      client.setSessionExpiredHandler(null);
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const me = await client.login(email, password);
    setNotice(null);
    setUser(me);
    setStatus("signedIn");
  }, []);

  const signUp = useCallback(async (input: client.RegisterPatientInput) => {
    const me = await client.registerPatient(input);
    setNotice(null);
    setUser(me);
    setStatus("signedIn");
  }, []);

  const signOut = useCallback(async () => {
    await client.logout();
    setUser(null);
    setStatus("signedOut");
    setNotice("You have signed out.");
  }, []);

  const value = useMemo(
    () => ({ status, user, notice, signIn, signUp, signOut }),
    [status, user, notice, signIn, signUp, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export function doctorMemberships(user: Me | null) {
  return (user?.memberships ?? []).filter((m) => m.roles.includes("doctor") && m.doctor_id);
}

import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiError, json, mockApi } from "../test/fakeApi";
import { doctorUser } from "../test/fixtures";
import { _setAccessTokenForTests, api, ApiError, login, restoreSession, setSessionExpiredHandler } from "./client";

beforeEach(() => {
  _setAccessTokenForTests(null);
  setSessionExpiredHandler(null);
});

describe("api client", () => {
  it("signs in as a web client and keeps the access token in memory only", async () => {
    const fake = mockApi({
      "POST /auth/login": () => ({ access_token: "a1", refresh_token: null, user: doctorUser }),
      "GET /auth/me": (c) => (c.headers.authorization === "Bearer a1" ? doctorUser : apiError(401, "token_invalid")),
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    await login("doc@example.test", "pw");
    expect(fake.calls[0].body).toMatchObject({ client_type: "web" });
    await expect(api("GET", "/auth/me")).resolves.toEqual(doctorUser);
    expect(setItem).not.toHaveBeenCalled();
  });

  it("refreshes an expired token once, sending the CSRF header, and retries", async () => {
    document.cookie = "careflow_csrf=csrf-123; path=/";
    let valid = "a1";
    const fake = mockApi({
      "POST /auth/login": () => ({ access_token: "a1", user: doctorUser }),
      "POST /auth/refresh": (c) => {
        expect(c.headers["x-csrf-token"]).toBe("csrf-123");
        valid = "a2";
        return { access_token: "a2", user: doctorUser };
      },
      "GET /auth/me": (c) => (c.headers.authorization === `Bearer ${valid}` ? doctorUser : apiError(401, "token_expired")),
    });
    await login("doc@example.test", "pw");
    valid = "expired";
    await Promise.all([api("GET", "/auth/me"), api("GET", "/auth/me"), api("GET", "/auth/me")]);
    expect(fake.find("POST", "/auth/refresh")).toHaveLength(1);
  });

  it("reports an ended session once and clears the token", async () => {
    mockApi({
      "POST /auth/login": () => ({ access_token: "a1", user: doctorUser }),
      "GET /auth/me": () => apiError(401, "session_ended"),
    });
    const expired = vi.fn();
    setSessionExpiredHandler(expired);
    await login("doc@example.test", "pw");
    await expect(api("GET", "/auth/me")).rejects.toMatchObject({ code: "session_ended" });
    await expect(api("GET", "/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("turns network failures into a readable error", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(login("a@example.test", "pw")).rejects.toMatchObject({
      code: "network_error", message: expect.stringContaining("Cannot reach CareFlow"),
    });
  });

  it("surfaces server error messages and codes", async () => {
    mockApi({ "POST /auth/login": () => apiError(401, "invalid_credentials", "The email or password is incorrect.") });
    await expect(login("a@example.test", "bad")).rejects.toMatchObject({
      status: 401, code: "invalid_credentials", message: "The email or password is incorrect.",
    });
  });

  it("does not try to restore a session without the CSRF cookie", async () => {
    const fake = mockApi({});
    await expect(restoreSession()).resolves.toBeNull();
    expect(fake.calls).toHaveLength(0);
  });

  it("restores a session from the refresh cookie", async () => {
    document.cookie = "careflow_csrf=c; path=/";
    mockApi({ "POST /auth/refresh": () => json(200, { access_token: "a9", user: doctorUser }) });
    await expect(restoreSession()).resolves.toEqual(doctorUser);
  });
});

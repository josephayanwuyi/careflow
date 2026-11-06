import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { _setAccessTokenForTests } from "../api/client";
import { App } from "../App";
import { AuthProvider } from "../auth/AuthContext";
import { apiError, mockApi } from "../test/fakeApi";
import { availability, deskUser, doctorUser, entry, HID, queueSummary } from "../test/fixtures";

const hospital = { id: HID, code: "TEST", name: "Test Hospital", slug: "t", timezone: "Africa/Lagos", address: null };

function renderSignedIn(path = "/") {
  document.cookie = "careflow_csrf=c; path=/";
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function doctorRoutes(state: { status: string; entries: ReturnType<typeof entry>[]; queue?: typeof queueSummary | null },
  extra: Record<string, (c: never) => unknown> = {}) {
  return mockApi({
    "POST /auth/refresh": () => ({ access_token: "a1", user: doctorUser }),
    [`GET /hospitals/${HID}`]: () => hospital,
    "GET /doctors/me/status": () => availability(state.status),
    [`GET /hospitals/${HID}/queues`]: () => (state.queue === null ? [] : [state.queue ?? queueSummary]),
    "GET /queues/q-1/entries": () => state.entries,
    ...extra,
  });
}

beforeEach(() => _setAccessTokenForTests(null));

describe("doctor area", () => {
  it("restores the session and opens the doctor's queue", async () => {
    doctorRoutes({ status: "off_duty", entries: [], queue: null });
    renderSignedIn();
    expect(await screen.findByRole("heading", { name: "Your status" })).toBeInTheDocument();
    expect(screen.getByText("Not checked in")).toBeInTheDocument();
    expect(screen.getByText("No patients have checked in to your queue today.")).toBeInTheDocument();
  });

  it("explains why the doctor cannot call the next patient", async () => {
    doctorRoutes({ status: "checked_in", entries: [entry()] });
    renderSignedIn("/doctor");
    const button = await screen.findByRole("button", { name: "Call next patient" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Set yourself as available to call patients.");
  });

  it("checks in and becomes available", async () => {
    const state = { status: "off_duty", entries: [] as ReturnType<typeof entry>[] };
    const fake = doctorRoutes(state, {
      "POST /doctors/me/status": ((c: { body: { status: string } }) => {
        state.status = c.body.status;
        return availability(c.body.status);
      }) as never,
    });
    renderSignedIn("/doctor");
    await userEvent.click(await screen.findByRole("button", { name: "I've arrived" }));
    await userEvent.click(await screen.findByRole("button", { name: "I'm available" }));
    expect(fake.find("POST", "/doctors/me/status").map((c) => (c.body as { status: string }).status))
      .toEqual(["checked_in", "available"]);
    expect(await screen.findByText("Status: I'm available")).toBeInTheDocument();
  });

  it("calls the next patient and starts and completes the consultation", async () => {
    const state = { status: "available", entries: [entry(), entry({ id: "e-2", ticket_number: 2, position: 2,
      patient: { id: "p-2", full_name: "Bayo Patient", hospital_patient_number: null } })] };
    const fake = doctorRoutes(state, {
      "POST /queues/q-1/call-next": (() => {
        state.entries[0] = entry({ status: "called", position: null, allowed_actions: ["recall", "start", "mark_missed"],
          called_at: "2026-11-02T08:01:00+00:00" });
        return state.entries[0];
      }) as never,
      "POST /queue-entries/e-1/start": (() => {
        state.entries[0] = entry({ status: "in_consultation", position: null, allowed_actions: ["complete"],
          started_at: "2026-11-02T08:02:00+00:00" });
        return state.entries[0];
      }) as never,
      "POST /queue-entries/e-1/complete": (() => {
        state.entries.shift();
        return entry({ status: "completed" });
      }) as never,
    });
    renderSignedIn("/doctor");
    const waiting = await screen.findByRole("list");
    expect(within(waiting).getAllByRole("listitem")).toHaveLength(2);

    await userEvent.click(screen.getByRole("button", { name: "Call next patient" }));
    const current = await screen.findByRole("button", { name: "Start consultation" });
    expect(screen.getByRole("button", { name: "Call next patient" })).toBeDisabled(); // someone is with the doctor
    await userEvent.click(current);
    await userEvent.click(await screen.findByRole("button", { name: "Complete consultation" }));
    // Completing asks for confirmation first.
    const dialog = await screen.findByRole("dialog", { name: "Complete consultation?" });
    expect(fake.find("POST", "/queue-entries/e-1/complete")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Complete" }));
    await waitFor(() => expect(fake.find("POST", "/queue-entries/e-1/complete")).toHaveLength(1));
    expect(fake.find("POST", "/queue-entries/e-1/start")[0].body).toEqual({ expected_version: 1 });
    expect(await screen.findByText("No patient is with you.")).toBeInTheDocument();
  });

  it("shows the server's reason when an action is refused", async () => {
    doctorRoutes({ status: "available", entries: [entry()] }, {
      "POST /queues/q-1/call-next": (() => apiError(409, "patient_already_called",
        "Another patient is already with the doctor.")) as never,
    });
    renderSignedIn("/doctor");
    await userEvent.click(await screen.findByRole("button", { name: "Call next patient" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Another patient is already with the doctor.");
  });

  it("asks for a return time when taking a break", async () => {
    const fake = doctorRoutes({ status: "available", entries: [] }, {
      "POST /doctors/me/status": ((c: { body: { status: string } }) => availability(c.body.status)) as never,
    });
    renderSignedIn("/doctor");
    await userEvent.click(await screen.findByRole("button", { name: "Take a break" }));
    const dialog = await screen.findByRole("dialog", { name: "Take a break" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Back in"), "30");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(fake.find("POST", "/doctors/me/status")).toHaveLength(1));
    const body = fake.find("POST", "/doctors/me/status")[0].body as { status: string; expected_return_at: string };
    expect(body.status).toBe("on_break");
    const minutes = (new Date(body.expected_return_at).getTime() - Date.now()) / 60_000;
    expect(minutes).toBeGreaterThan(29);
    expect(minutes).toBeLessThan(31);
  });

  it("closes dialogs with Escape without acting", async () => {
    const fake = doctorRoutes({ status: "available", entries: [] });
    renderSignedIn("/doctor");
    await userEvent.click(await screen.findByRole("button", { name: "Leave for the day" }));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fake.find("POST", "/doctors/me/status")).toHaveLength(0);
  });
});

describe("routing and sign-in", () => {
  it("sends reception staff to the desktop app", async () => {
    mockApi({ "POST /auth/refresh": () => ({ access_token: "a1", user: deskUser }) });
    renderSignedIn();
    expect(await screen.findByText(/use the CareFlow desktop application/)).toBeInTheDocument();
  });

  it("shows sign-in errors and clears the password", async () => {
    mockApi({ "POST /auth/login": () => apiError(401, "invalid_credentials", "The email or password is incorrect.") });
    render(<MemoryRouter initialEntries={["/login"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
    await userEvent.type(await screen.findByLabelText("Email"), "doc@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("incorrect");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });
});

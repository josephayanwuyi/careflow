import { expect, request, test, type APIRequestContext } from "@playwright/test";

const PASSWORD = process.env.CAREFLOW_E2E_PASSWORD ?? "";
const API = (process.env.CAREFLOW_API_URL ?? "http://127.0.0.1:8000") + "/api/v1";
const DOCTOR = "dr.okafor@example.test";

test.skip(!PASSWORD, "set CAREFLOW_E2E_PASSWORD to the seed password");

async function staffApi(): Promise<{ api: APIRequestContext; hid: string; doctorId: string }> {
  const api = await request.newContext();
  const login = await api.post(`${API}/auth/login`, { data: { email: "reception1@example.test", password: PASSWORD } });
  const { access_token: token } = await login.json();
  const authed = await request.newContext({ extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
  const [hospital] = await (await authed.get(`${API}/hospitals`)).json();
  const doctors = await (await authed.get(`${API}/hospitals/${hospital.id}/doctors`)).json();
  const doctor = doctors.find((d: { display_name: string }) => d.display_name.includes("Okafor"));
  return { api: authed, hid: hospital.id, doctorId: doctor.id };
}

test("doctor works through a live queue on a phone", async ({ page }) => {
  const staff = await staffApi();

  // Sign in.
  await page.goto("/");
  await page.getByLabel("Email").fill(DOCTOR);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/doctor$/);
  await expect(page.getByRole("heading", { name: "Your status" })).toBeVisible();
  await expect(page.getByText("Live", { exact: true })).toBeVisible();

  // The refresh token is never readable by page scripts.
  expect(await page.evaluate(() => document.cookie)).not.toContain("careflow_refresh");

  // Get to "available" from whatever state the seed doctor is in today.
  const arrived = page.getByRole("button", { name: "I've arrived" });
  if (await arrived.isVisible()) {
    await arrived.click();
  }
  if (!await page.getByText("Patients see: available").isVisible()) {
    const available = page.getByRole("button", { name: "I'm available" });
    await available.click();
    await expect(page.getByText("Patients see: available")).toBeVisible();
  }

  // If the queue was left paused from an earlier run, resume it so patients can be called.
  const resume = page.getByRole("button", { name: "Resume queue" });
  if (await resume.isVisible()) {
    await resume.click();
    await expect(resume).not.toBeVisible();
  }

  // Reception adds a walk-in: it appears on the doctor's phone without a reload (live update).
  const name = `E2E Walkin ${Date.now()}`;
  const patient = await (await staff.api.post(`${API}/hospitals/${staff.hid}/patients`, {
    data: { full_name: name },
  })).json();
  const added = await staff.api.post(`${API}/hospitals/${staff.hid}/queue/walk-ins`, {
    data: { patient_id: patient.id, doctor_id: staff.doctorId },
  });
  expect(added.status()).toBe(201);
  await expect(page.getByRole("list").getByText(name)).toBeVisible({ timeout: 10_000 });

  // Work through the queue until our walk-in has been seen (earlier seed patients may be ahead).
  for (let i = 0; i < 10; i++) {
    await page.getByRole("button", { name: "Call next patient" }).click();
    await page.getByRole("button", { name: "Start consultation" }).click();
    const current = await page.locator(".current-patient .patient-name").textContent();
    await page.getByRole("button", { name: "Complete consultation" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Complete" }).click();
    await expect(page.getByText("Consultation completed.")).toBeVisible();
    if (current === name) break;
  }
  await expect(page.getByText("No patient is with you.")).toBeVisible();

  // Today tab lists the completed consultation.
  await page.getByRole("link", { name: "Today" }).click();
  await expect(page.getByRole("region", { name: /Completed consultations/ }).getByText(name)).toBeVisible();

  // A reload keeps the doctor signed in (HttpOnly refresh cookie), without asking for the password.
  await page.reload();
  await expect(page.getByRole("heading", { name: /Completed consultations/ })).toBeVisible();

  // Leave for the day (keeps the seed data tidy), then sign out.
  await page.getByRole("link", { name: "Queue" }).click();
  await page.getByRole("button", { name: "Leave for the day" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Leave for the day" }).click();
  await expect(page.getByText("You have left for the day.")).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible(); // the session really ended
});

test("reception accounts are pointed to the desktop app", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill("reception1@example.test");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(/use the CareFlow desktop application/)).toBeVisible();
});

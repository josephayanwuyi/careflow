import { expect, request, test, type APIRequestContext } from "@playwright/test";

const PASSWORD = process.env.CAREFLOW_E2E_PASSWORD ?? "";
const API = (process.env.CAREFLOW_API_URL ?? "http://127.0.0.1:8000") + "/api/v1";

test.skip(!PASSWORD, "set CAREFLOW_E2E_PASSWORD to the seed password");

async function staffApi(): Promise<{ api: APIRequestContext; hid: string; doctorId: string; today: string; startHour: number }> {
  const api = await request.newContext();
  const login = await api.post(`${API}/auth/login`, { data: { email: "admin@example.test", password: PASSWORD } });
  const { access_token: token } = await login.json();
  const authed = await request.newContext({ extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
  const [hospital] = await (await authed.get(`${API}/hospitals`)).json();
  const doctors = await (await authed.get(`${API}/hospitals/${hospital.id}/doctors`)).json();
  const doctor = doctors.find((d: { display_name: string }) => d.display_name.includes("Okafor"));

  // Create an extra session for right now so check-in window (start - 60 min) is active
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const startHour = (now.getHours() + 1) % 24;
  const endHour = (startHour + 1) % 24;

  const res = await authed.post(`${API}/hospitals/${hospital.id}/doctors/${doctor.id}/schedule-exceptions`, {
    data: {
      on_date: today,
      kind: "extra_session",
      start_time: `${String(startHour).padStart(2, "0")}:00:00`,
      end_time: `${String(endHour).padStart(2, "0")}:00:00`,
      slot_minutes: 15,
      reason: "E2E Extra Session",
    },
  });
  expect(res.status()).toBe(201);

  return { api: authed, hid: hospital.id, doctorId: doctor.id, today, startHour };
}

test("patient registers, books an appointment, checks in, and tracks live queue", async ({ page }) => {
  const staff = await staffApi();
  const unique = Date.now();
  const patientEmail = `patient.e2e.${unique}@example.test`;
  const patientName = `E2E Patient ${unique}`;
  const patientPhone = `080${String(unique).slice(-8)}`;

  // 1. Register a new patient account
  await page.goto("/");
  await page.getByRole("button", { name: /Register/i }).click();
  await page.getByLabel("Full Name").fill(patientName);
  await page.getByLabel("Email").fill(patientEmail);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel(/Phone/i).fill(patientPhone);
  await page.getByRole("button", { name: "Register & Sign in" }).click();

  // Redirected to patient area
  await expect(page).toHaveURL(/\/patient$/);
  await expect(page.getByRole("heading", { name: "Live Queue" })).toBeVisible();
  await expect(page.getByText(patientName)).toBeVisible();
  await expect(page.getByText("Live", { exact: true })).toBeVisible();

  // The refresh token is in an HttpOnly cookie, never in document.cookie
  expect(await page.evaluate(() => document.cookie)).not.toContain("careflow_refresh");

  // 2. Discover doctors and book an appointment
  await page.getByRole("link", { name: "Book", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Book an Appointment" })).toBeVisible();

  // Click Dr. Okafor
  const doctorBtn = page.getByRole("button", { name: /Okafor/ });
  await expect(doctorBtn).toBeVisible();
  await doctorBtn.click();

  // Pick an available slot
  const slotBtn = page.locator("button[role='radio']:not([disabled])").first();
  await expect(slotBtn).toBeVisible({ timeout: 10_000 });
  await slotBtn.scrollIntoViewIfNeeded();
  await slotBtn.click({ force: true });

  // Enter reason for visit
  await page.getByLabel(/Reason for visit/i).fill("Routine general checkup");

  // Submit request
  await page.getByRole("button", { name: "Submit Appointment Request" }).click();

  // Navigated to appointments page
  await expect(page).toHaveURL(/\/patient\/appointments$/);
  await expect(page.getByText("Appointment request submitted!")).toBeVisible();
  await expect(page.getByText("Pending approval")).toBeVisible();

  // 3. Receptionist approves the appointment via API
  const apptsRes = await staff.api.get(`${API}/hospitals/${staff.hid}/appointments?status=requested`);
  const apptsData = await apptsRes.json();
  const pendingAppt = apptsData.items.find((a: { patient: { full_name: string } }) => a.patient.full_name === patientName);
  expect(pendingAppt).toBeTruthy();

  const approveRes = await staff.api.post(`${API}/appointments/${pendingAppt.id}/approve`, {
    data: { expected_version: pendingAppt.version },
  });
  expect(approveRes.status()).toBe(200);

  // 4. Live update pushes approval to patient's browser
  await expect(page.getByText("Approved")).toBeVisible({ timeout: 10_000 });

  // 5. Patient checks in for the appointment
  const checkInBtn = page.getByRole("button", { name: /Check in for appointment/i });
  await expect(checkInBtn).toBeVisible({ timeout: 10_000 });
  await checkInBtn.click();
  await expect(page.getByText("Checked in successfully!")).toBeVisible();

  // 6. Live Queue tab displays active ticket
  await page.getByRole("link", { name: "Queue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your Queue Status" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Your Ticket/i)).toBeVisible();
  await expect(page.getByText(/Waiting/i)).toBeVisible();

  // 7. Patient leaves the queue
  await page.getByRole("button", { name: "Leave queue" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Leave queue" }).click();

  await expect(page.getByText("You have left the queue.")).toBeVisible();
  await expect(page.getByText("You are not currently waiting in any hospital queue.")).toBeVisible();

  // 8. Sign out
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

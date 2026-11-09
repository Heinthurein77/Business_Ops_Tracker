// Shared helpers for the BOT frontend smoke specs. Defaults are the
// local dev seed accounts (see e2e/README.md); override via env vars.
//
// NOTE: the dev seed marks every account require_password_change, so the
// FIRST login for an account lands on /force-password-change. loginAs()
// completes that rotation automatically (to E2E_ROTATED_PASSWORD) and
// waits until the user reaches their role home. Run against a fresh
// local_dev.db (README) so every run behaves the same way.
export const accounts = {
  chairman: {
    username: process.env.E2E_CHAIRMAN_USERNAME || "chairman",
    password: process.env.E2E_CHAIRMAN_PASSWORD || "Chairman123!",
  },
  ho: {
    username: process.env.E2E_HO_USERNAME || "ho_staff",
    password: process.env.E2E_HO_PASSWORD || "HOStaff123!",
  },
};

// Password every rotated account is set to during the smoke run.
// Must satisfy the server policy (12-72 chars, not the username, not
// a common password, >= 5 distinct characters).
export const rotatedPassword = process.env.E2E_ROTATED_PASSWORD || "E2eRotated123!";

// Usernames whose password this run has already rotated. loginAs()
// uses the rotated password for those accounts instead of the seed
// password — retrying the seed password on every test would burn
// through the login throttle (5 failed attempts per user per 15
// minutes) and lock the account out mid-run.
const rotatedInRun = new Set();

async function attemptLogin(page, username, password) {
  await page.goto("/login");
  await page.getByPlaceholder("Your username").fill(username);
  await page.getByPlaceholder("••••••••").fill(password);
  await page.getByRole("button", { name: "Sign In Securely" }).click();
  try {
    await page.waitForFunction(() => {
      const text = document.body.innerText || "";
      return text.includes("Password Rotation Required") || !window.location.pathname.startsWith("/login");
    }, { timeout: 8 * 1000 });
    return true;
  } catch {
    return false;
  }
}

export async function loginAs(page, account) {
  // One attempt only, with the password this run knows: the seed password
  // for a fresh DB, the rotated password once this run has rotated the
  // account. Deliberately NO fallback guess: a wrong-password retry
  // burns the login throttle (5 failed attempts per user per 15 min)
  // and locks the account out mid-run. Reruns must use a fresh
  // local_dev.db (see e2e/README.md).
  const password = rotatedInRun.has(account.username) ? rotatedPassword : account.password;
  const ok = await attemptLogin(page, account.username, password);
  if (!ok) {
    throw new Error(`login failed for ${account.username} — use a fresh local_dev.db (see e2e/README.md)`);
  }
  const heading = page.getByRole("heading", { name: "Password Rotation Required" });
  if (await heading.isVisible().catch(() => false)) {
    await completeForcedRotation(page, password, rotatedPassword);
    rotatedInRun.add(account.username);
  }
  // Rotation lands on the role home; a plain login lands there directly.
  await page.waitForURL(
    (url) => !url.pathname.startsWith("/login") && !url.pathname.startsWith("/force-password-change"),
    { timeout: 15 * 1000 });
}

export async function completeForcedRotation(page, currentPassword, newPassword) {
  await page.getByPlaceholder("Your current password").fill(currentPassword);
  await page.getByPlaceholder("At least 12 characters").fill(newPassword);
  await page.getByPlaceholder("Re-enter password").fill(newPassword);
  await page.getByRole("button", { name: "Update Password & Continue" }).click();
}

export async function signOut(page) {
  await page.getByRole("button", { name: "Sign Out" }).first().click();
  await page.waitForURL(/\/login/, { timeout: 10 * 1000 });
}

// Creates a BU accountant through the API as the currently logged-in
// Chairman (page.request shares the browser context's session cookie).
// Returns { username, temporary_password }. The username is unique per
// run so reruns against the same dev DB never collide.
export async function createAccountant(page, businessUnitId = 1) {
  const username = `e2e_acct_${Date.now()}`;
  const res = await page.request.post("/api/admin/users", {
    data: {
      username,
      full_name: "E2E Smoke Accountant",
      role: "Accountant",
      business_unit_id: businessUnitId,
    },
  });
  if (!res.ok()) {
    throw new Error(`create accountant failed: ${res.status()} ${await res.text()}`);
  }
  const body = await res.json();
  return { username, temporary_password: body.temporary_password };
}

// Shared helpers for the BOT frontend smoke specs.
//
// The backend must be running against a FRESH scratch database (see
// e2e/README.md) with ALLOW_DEV_SEED=true, so the dev seed accounts
// exist. Since 2026-11-17 the seed passwords are RANDOM per boot —
// they are written once to `.dev_seed_passwords` next to the uploads
// directory and must be passed in as E2E_*_PASSWORD env vars. There are
// no hardcoded seed passwords anywhere in this file: the old defaults
// (Chairman123! / HOStaff123!) stopped existing when the M-02 seed
// hardening landed, and a wrong-password guess burns the login throttle
// (5 failed attempts per user per 15 minutes).
//
// NOTE: the dev seed marks every account require_password_change, so the
// FIRST login for an account lands on /force-password-change. loginAs()
// completes that rotation automatically (to E2E_ROTATED_PASSWORD) and
// waits until the user reaches their role home. Run against a fresh
// scratch DB (README) so every run behaves the same way.
function requiredEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `missing ${name} — start the backend with ALLOW_DEV_SEED=true against a fresh scratch DB, ` +
      `then export the password from the .dev_seed_passwords file it writes on boot (see e2e/README.md)`,
    );
  }
  return value;
}

export const accounts = {
  chairman: {
    username: process.env.E2E_CHAIRMAN_USERNAME || "chairman",
    get password() {
      return requiredEnv("E2E_CHAIRMAN_PASSWORD");
    },
  },
  ho: {
    username: process.env.E2E_HO_USERNAME || "ho_staff",
    get password() {
      return requiredEnv("E2E_HO_PASSWORD");
    },
  },
  // Seeded CEO for BU1 (the dev seed creates one active CEO per BU, so
  // creating a fresh CEO via the API would 400 — "already has an active
  // CEO account"). Its profile is incomplete at seed time, so the first
  // login goes rotation -> profile setup; completeCeoProfile() below
  // finishes that step over the API.
  ceo: {
    username: process.env.E2E_CEO_USERNAME || "ceo_bu1",
    get password() {
      return requiredEnv("E2E_CEO_PASSWORD");
    },
  },
};

// Password every rotated account is set to during the smoke run.
// Must satisfy the server policy (12-72 chars, not the username, not
// a common password, >= 5 distinct characters).
export const rotatedPassword = process.env.E2E_ROTATED_PASSWORD || "E2eRotated123!";

// RFC 6238 TOTP (SHA-1, 30 s step, 6 digits), dependency-free via
// node:crypto. The Chairman's enrollment secret is shown on the login
// page, so the specs compute the code themselves instead of needing an
// authenticator app. The server verifies the current 30 s step only,
// so callers compute the code immediately before filling it, and retry
// once with a fresh code if a step boundary fell in between.
import { createHmac } from "node:crypto";

const _BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

export function totpCode(secret) {
  const clean = secret.replace(/[\s=]/g, "").toUpperCase();
  let bits = 0;
  let value = 0;
  const bytes = [];
  for (const char of clean) {
    const idx = _BASE32.indexOf(char);
    if (idx === -1) throw new Error(`bad base32 character in TOTP secret: ${char}`);
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((value >>> bits) & 0xff);
    }
  }
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30)));
  const hmac = createHmac("sha1", Buffer.from(bytes)).update(counter).digest();
  const offset = hmac[hmac.length - 1] & 0x0f;
  const code = ((hmac[offset] & 0x7f) << 24) | (hmac[offset + 1] << 16)
    | (hmac[offset + 2] << 8) | hmac[offset + 3];
  return String(code % 1_000_000).padStart(6, "0");
}

// The Chairman's TOTP secret, captured at enrollment (the secret is
// only ever shown then). Later logins in the same run need it to
// produce the login code; a fresh scratch DB always enrolls first,
// so this is never stale within a run.
let chairmanTotpSecret = process.env.E2E_CHAIRMAN_TOTP_SECRET || null;

// Usernames whose password this run has already rotated. loginAs()
// uses the rotated password for those accounts instead of the seed
// password — retrying the seed password on every test would burn
// through the login throttle (5 failed attempts per user per 15
// minutes) and lock the account out mid-run.
const rotatedInRun = new Set();

// Waits for the login attempt to settle into one of its outcomes:
// "rotate" (forced password change), "enroll" (Chairman TOTP setup),
// "totp" (TOTP code requested), "ok" (landed past /login), "failed".
async function loginOutcome(page) {
  try {
    await page.waitForFunction(() => {
      const text = document.body.innerText || "";
      return text.includes("Password Rotation Required")
        || text.includes("Two-factor authentication is required")
        || text.includes("Enter the 6-digit code")
        || !window.location.pathname.startsWith("/login");
    }, { timeout: 8 * 1000 });
  } catch {
    return "failed";
  }
  if (await page.getByRole("button", { name: "Confirm and Enable" }).isVisible().catch(() => false)) {
    return "enroll";
  }
  if (await page.getByRole("heading", { name: "Password Rotation Required" }).isVisible().catch(() => false)) {
    return "rotate";
  }
  if (await page.getByPlaceholder("6-digit code").isVisible().catch(() => false)) {
    return "totp";
  }
  return new URL(page.url()).pathname.startsWith("/login") ? "failed" : "ok";
}

async function attemptLogin(page, username, password) {
  await page.goto("/login");
  await page.getByPlaceholder("Your username").fill(username);
  await page.getByPlaceholder("••••••••").fill(password);
  await page.getByRole("button", { name: "Sign In Securely" }).click();
  return loginOutcome(page);
}

export async function loginAs(page, account) {
  // One attempt only, with the password this run knows: the seed password
  // for a fresh DB, the rotated password once this run has rotated the
  // account. Deliberately NO fallback guess: a wrong-password retry
  // burns the login throttle (5 failed attempts per user per 15 min)
  // and locks the account out mid-run. Reruns must use a fresh
  // scratch DB (see e2e/README.md).
  const password = rotatedInRun.has(account.username) ? rotatedPassword : account.password;
  let state = await attemptLogin(page, account.username, password);
  if (state === "failed") {
    throw new Error(`login failed for ${account.username} — use a fresh scratch DB (see e2e/README.md)`);
  }
  if (state === "enroll") {
    // Chairman, first login on a fresh DB: TOTP enrollment is mandatory
    // (M-01) before any session is issued. The setup panel shows the
    // secret; confirm with a code computed from it, then sign in again.
    const secret = (await page.locator("code").first().innerText()).trim();
    for (let attempt = 0; attempt < 2; attempt++) {
      await page.getByPlaceholder("6-digit code").fill(totpCode(secret));
      await page.getByRole("button", { name: "Confirm and Enable" }).click();
      try {
        await page.waitForFunction(
          () => (document.body.innerText || "").includes("sign in again with your password"),
          { timeout: 5 * 1000 });
        break;
      } catch {
        if (attempt === 1) throw new Error("chairman TOTP enrollment failed twice — aborting");
      }
    }
    chairmanTotpSecret = secret;
    state = await attemptLogin(page, account.username, password);
    if (state === "failed") {
      throw new Error(`login failed for ${account.username} after TOTP enrollment — use a fresh scratch DB (see e2e/README.md)`);
    }
  }
  if (state === "totp") {
    // Chairman, already enrolled: the password was right, a current
    // code is the second factor.
    if (!chairmanTotpSecret) {
      throw new Error("chairman TOTP code requested but this run never enrolled — use a fresh scratch DB (see e2e/README.md)");
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      await page.getByPlaceholder("6-digit code").fill(totpCode(chairmanTotpSecret));
      await page.getByRole("button", { name: "Sign In Securely" }).click();
      state = await loginOutcome(page);
      if (state !== "totp") break;
    }
    if (state === "failed" || state === "totp") {
      throw new Error(`login failed for ${account.username} (TOTP code rejected) — use a fresh scratch DB (see e2e/README.md)`);
    }
  }
  if (state === "rotate") {
    await completeForcedRotation(page, password, rotatedPassword);
    rotatedInRun.add(account.username);
  }
  // Rotation lands on the role home (or profile setup for a CEO whose
  // profile is still incomplete); a plain login lands there directly.
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

// Completes the seeded CEO's mandatory profile over the API as the
// currently logged-in CEO (page.request shares the browser context's
// session cookie), so the CEO can reach their role home (/ceo). Done
// over the API rather than through the Profile Setup form so this spec
// only depends on the login page and the role-bounce routes.
export async function completeCeoProfile(page) {
  const res = await page.request.post("/api/ceo/profile", {
    multipart: {
      full_name: "E2E Smoke CEO",
      phone_number: "+959123456789",
      date_of_birth: "1990-05-01",
      address: "E2E Test Address, Yangon",
    },
  });
  if (!res.ok()) {
    throw new Error(`complete CEO profile failed: ${res.status()} ${await res.text()}`);
  }
  const body = await res.json();
  if (!body.ok) {
    throw new Error(`complete CEO profile failed: ${JSON.stringify(body)}`);
  }
}

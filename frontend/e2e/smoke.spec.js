// T-m2 smoke layer: the frontend previously had zero tests of any kind.
// These specs cover the three checks the audit asked for — Chairman
// login + admin dashboard load, role visibility (a BU accountant must
// not reach Chairman-only pages), and the failed-load error state
// (an error card, not a permanent spinner).
import { expect, test } from "@playwright/test";
import { accounts, completeForcedRotation, createAccountant, loginAs, rotatedPassword, signOut } from "./helpers.js";

test("chairman login lands on the admin dashboard", async ({ page }) => {
  await loginAs(page, accounts.chairman);
  await expect(page).toHaveURL(/\/admin$/);
  // Inside the app shell: the admin sidebar is visible and the login
  // form is gone.
  await expect(page.getByText("Business Units", { exact: true }).first()).toBeVisible();
  await expect(page.getByPlaceholder("Your username")).toHaveCount(0);
  await expect(page.getByText("Couldn't load the dashboard")).toHaveCount(0);
});

test("wrong password shows an error and stays on login", async ({ page }) => {
  await page.goto("/login");
  await page.getByPlaceholder("Your username").fill(accounts.chairman.username);
  await page.getByPlaceholder("••••••••").fill("WrongPass123!");
  await page.getByRole("button", { name: "Sign In Securely" }).click();
  await expect(page).toHaveURL(/\/login/);
  // The error banner renders above the form, not inside it.
  const banner = page.locator("div.bg-red-50").first();
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(/invalid|incorrect|failed|wrong/i);
});

test("BU accountant cannot reach Chairman-only pages", async ({ page }) => {
  // Create the accountant as Chairman, then switch users.
  await loginAs(page, accounts.chairman);
  const acct = await createAccountant(page, 1);
  await signOut(page);

  // Fresh account: forced rotation first, then the accountant home.
  await page.goto("/login");
  await page.getByPlaceholder("Your username").fill(acct.username);
  await page.getByPlaceholder("••••••••").fill(acct.temporary_password);
  await page.getByRole("button", { name: "Sign In Securely" }).click();
  await completeForcedRotation(page, acct.temporary_password, rotatedPassword);
  await expect(page).toHaveURL(/\/accounting/);

  // Chairman-only route: bounced back to the accountant's own home.
  await page.goto("/admin/users");
  await expect(page).toHaveURL(/\/accounting/);
  await expect(page.getByText("Manage Users")).toHaveCount(0);

  // The admin dashboard itself is Chairman/HO-only too.
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/accounting/);
});

test("HO user cannot reach Chairman-only pages", async ({ page }) => {
  await loginAs(page, accounts.ho);
  await expect(page).toHaveURL(/\/admin$/);
  await page.goto("/admin/users");
  // HO's role home is /admin, so the bounce lands back on the dashboard.
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText("Manage Users")).toHaveCount(0);
});

test("failed dashboard load shows an error, not a permanent spinner", async ({ page }) => {
  await loginAs(page, accounts.chairman);
  await expect(page).toHaveURL(/\/admin$/);
  // Kill the overview call, then reload: the page must surface the
  // failure instead of spinning forever.
  await page.route("**/api/admin/overview", (route) => route.abort());
  await page.reload();
  await expect(page.getByText("Couldn't load the dashboard")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.locator(".animate-spin")).toHaveCount(0);
});

// T-M1 smoke layer: the frontend previously had zero tests of any kind.
// These specs cover the three checks the audit asked for — Chairman
// login + admin dashboard load, role visibility (a CEO must not reach
// Chairman-only pages), and the failed-load error state (an error card,
// not a permanent spinner).
//
// Roles that exist post-ERP-removal are Chairman, HO and CEO only
// (src/schemas.py CreateUserRequest.validate_role rejects anything
// else); there is no Accountant role and no /accounting route. The
// role-bounce spec therefore uses the seeded CEO (ceo_bu1), whose home
// is /ceo. Specs stick to the login page and role bounces so the
// ongoing page refactor cannot break them.
import { expect, test } from "@playwright/test";
import { accounts, completeCeoProfile, loginAs } from "./helpers.js";

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

test("CEO cannot reach Chairman-only pages", async ({ page }) => {
  // Seeded CEO: forced rotation first, then the mandatory profile
  // (completed over the API), then the CEO home.
  await loginAs(page, accounts.ceo);
  await completeCeoProfile(page);
  await page.goto("/ceo");
  await expect(page).toHaveURL(/\/ceo$/);

  // Chairman-only route: bounced back to the CEO's own home.
  await page.goto("/admin/users");
  await expect(page).toHaveURL(/\/ceo$/);
  await expect(page.getByText("Manage Users")).toHaveCount(0);

  // The admin dashboard itself is Chairman/HO-only too.
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/ceo$/);
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

// Committed smoke suite for the Business Ops Tracker frontend (Playwright).
//
// Run from frontend/ with the dev backend running:
//
//   cd ~/workspace/business-ops-tracker
//   rm -f local_dev.db*            # fresh seed: deterministic first logins
//   ENVIRONMENT=development DATABASE_URL=sqlite:///./local_dev.db \
//     .venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8123 &
//   cd frontend && npm run dev &  # port 5173 (proxies /api to :8123)
//   npm run test:e2e
//
// The specs log in with the local dev seed accounts (chairman /
// Chairman123!, ho_staff / HOStaff123!); override with E2E_* env vars
// (see e2e/helpers.js). The seed marks every account
// require_password_change, so the first login rotates the password to
// E2E_ROTATED_PASSWORD (default "E2eRotated123!") — that is why the run
// needs a fresh local_dev.db. The specs create one throwaway BU
// accountant per run (username e2e_acct_<timestamp>) and leave it in the
// dev DB; harmless, and reruns never collide with it.
//
// The config starts the vite dev server itself (and reuses it if it is
// already running); the backend must already be up on :8123.
//
// Browser: Playwright's default full Chromium when installed
// (`npx playwright install chromium`); otherwise the headless shell
// already present on this machine is used automatically. Override
// with PLAYWRIGHT_CHROMIUM_PATH.
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

function chromiumExecutable() {
  if (process.env.PLAYWRIGHT_CHROMIUM_PATH) return process.env.PLAYWRIGHT_CHROMIUM_PATH;
  const cache = join(homedir(), ".cache", "ms-playwright");
  try {
    for (const dir of readdirSync(cache)) {
      if (dir.startsWith("chromium-") && !dir.includes("headless")) {
        const exe = join(cache, dir, "chrome-linux", "chrome");
        if (existsSync(exe)) return undefined; // default full build
      }
    }
    for (const dir of readdirSync(cache)) {
      if (dir.startsWith("chromium_headless_shell-")) {
        const exe = join(cache, dir, "chrome-headless-shell-linux64", "chrome-headless-shell");
        if (existsSync(exe)) return exe;
      }
    }
  } catch {
    // No cache dir — let Playwright report the missing browser itself.
  }
  return undefined;
}

const executablePath = chromiumExecutable();

export default defineConfig({
  testDir: "./e2e",
  timeout: 30 * 1000,
  expect: { timeout: 10 * 1000 },
  fullyParallel: false,
  workers: 1, // specs share one dev database; keep them serial.
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:5173",
    trace: "retain-on-failure",
    ...(executablePath ? { launchOptions: { executablePath } } : {}),
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60 * 1000,
  },
});

# Frontend smoke tests (T-M1)

Minimal committed Playwright layer — the frontend previously had zero
tests. Run from `frontend/` against a **scratch database in /tmp**
(never the real `local_dev.db` — the fresh-DB step below would destroy
it):

```sh
cd ~/workspace/business-ops-tracker
E2E_DB=/tmp/bot-e2e.db
rm -f "$E2E_DB" .dev_seed_passwords
ENVIRONMENT=development ALLOW_DEV_SEED=true \
  ALLOWED_ORIGINS=http://localhost:5173 \
  DATABASE_URL="sqlite:///$E2E_DB" \
  .venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8123 &
# The backend writes the random dev-seed passwords to .dev_seed_passwords
# on first boot (0600, next to the uploads dir). Export them for the run:
export E2E_CHAIRMAN_PASSWORD=$(grep '^chairman: ' .dev_seed_passwords | cut -d' ' -f2)
export E2E_HO_PASSWORD=$(grep '^ho_staff: ' .dev_seed_passwords | cut -d' ' -f2)
export E2E_CEO_PASSWORD=$(grep '^ceo_bu1: ' .dev_seed_passwords | cut -d' ' -f2)
cd frontend && npm run dev &  # port 5173 (proxies /api to :8123)
npm run test:e2e
# When done: kill the backend + vite, then rm -f "$E2E_DB" .dev_seed_passwords
```

The backend must already be up on `:8123`; the config starts (or reuses)
the vite dev server itself. A full Chromium is used when installed
(`npx playwright install chromium`); otherwise the headless shell already
present on this machine is used automatically.

CI runs the same flow (`.github/workflows/tests.yml`, `e2e` job) with
its own scratch DB.

## What the specs cover

- Chairman login lands on the admin dashboard (incl. the seed accounts'
  first-login forced password rotation).
- Wrong password shows an error and stays on `/login`.
- A CEO (seeded `ceo_bu1`, profile completed over the API) cannot reach
  Chairman-only pages (`/admin/users`, `/admin`) — it is bounced to its
  own home, `/ceo`.
- An HO user likewise cannot reach `/admin/users`.
- A failed dashboard load (the `/api/admin/overview` call aborted)
  shows the "Couldn't load the dashboard" error card with a Retry
  button, not a permanent spinner.

Roles that exist are Chairman, HO and CEO only — there is no Accountant
role and no `/accounting` route since the ERP removal (2026-11-17).

## Notes

- The dev seed marks every account `require_password_change`, so the
  first login rotates the password to `E2E_ROTATED_PASSWORD` (default
  `E2eRotated123!`). That is why the run needs a fresh scratch DB.
- `ALLOWED_ORIGINS=http://localhost:5173` is required: the Vite proxy
  rewrites `Host` to the backend (`127.0.0.1:8123`) while the browser's
  `Origin` stays `localhost:5173`, so without it the backend's CSRF
  check rejects every login POST with "Cross-origin request blocked".
- The Chairman's first login also completes mandatory TOTP enrollment
  (M-01): the specs read the secret off the login page, compute the
  6-digit code in-test (`totpCode` in `helpers.js`), confirm, then sign
  in again with password + code. Later Chairman logins in the same run
  reuse the captured secret.
- **Reruns must also use a fresh scratch DB.** A rerun against the
  same DB would log in with the seed password, which a previous run
  already rotated — and the app's login throttle (5 failed attempts per
  user per 15 minutes) locks the account out after a few such guesses,
  by design. If a run fails with "Too many sign-in attempts", delete
  the scratch DB + `.dev_seed_passwords` and restart the backend.
- The CEO spec completes the seeded CEO's mandatory profile over the
  API (`POST /api/ceo/profile`); it creates no accounts.
- These are dev-only seed credentials. Never point the specs at a
  production backend.

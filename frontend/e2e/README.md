# Frontend smoke tests (T-m2)

Minimal committed Playwright layer — the frontend previously had zero
tests. Run from `frontend/`:

```sh
cd ~/workspace/business-ops-tracker
rm -f local_dev.db*            # fresh seed: deterministic first logins
ENVIRONMENT=development DATABASE_URL=sqlite:///./local_dev.db \
  .venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8123 &
cd frontend && npm run dev &  # port 5173 (proxies /api to :8123)
npm run test:e2e
```

The backend must already be up on `:8123`; the config starts (or reuses)
the vite dev server itself. A full Chromium is used when installed
(`npx playwright install chromium`); otherwise the headless shell already
present on this machine is used automatically.

## What the specs cover

- Chairman login lands on the admin dashboard (incl. the seed accounts'
  first-login forced password rotation).
- Wrong password shows an error and stays on `/login`.
- A BU accountant (created fresh per run as `e2e_acct_<timestamp>`)
  cannot reach Chairman-only pages (`/admin/users`, `/admin`) — it is
  bounced to its own home, `/accounting`.
- An HO user likewise cannot reach `/admin/users`.
- A failed dashboard load (the `/api/admin/overview` call aborted)
  shows the "Couldn't load the dashboard" error card with a Retry
  button, not a permanent spinner.

## Notes

- The dev seed marks every account `require_password_change`, so the
  first login rotates the password to `E2E_ROTATED_PASSWORD` (default
  `E2eRotated123!`). That is why the run needs a fresh `local_dev.db`.
- **Reruns must also use a fresh `local_dev.db`.** A rerun against the
  same DB would log in with the seed password, which a previous run
  already rotated — and the app's login throttle (5 failed attempts per
  user per 15 minutes) locks the account out after a few such guesses,
  by design. If a run fails with "Too many sign-in attempts", delete
  `local_dev.db*` and restart the backend.
- The specs create one throwaway BU accountant per run and leave it in
  the dev DB; reruns use a timestamped username and never collide.
- These are dev-only seed credentials. Never point the specs at a
  production backend.

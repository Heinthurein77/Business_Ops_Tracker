# Session policy — Business Ops Tracker

Decided policy (R-5, BOT security review 2026-11-09). Implemented by
`src/config.py` (`JWT_EXPIRE_MINUTES=480`) and `src/auth.py`
(`bump_token_version`); there is no code change in this fix — this doc
records the decision and its accepted risk.

## The policy

- **Absolute maximum session lifetime: 8 hours.** One JWT is minted at
  login (`exp` = login + 480 minutes); there is no sliding refresh, so
  a session ends 8 hours after login at the latest and the user signs in
  again. The 8-hour value is the default in `JWT_EXPIRE_MINUTES` and in
  the compose file.
- **No idle timeout.** A session stays valid for the full 8 hours even
  if the user walks away from the browser. There is no "log out after
  30 minutes idle" check.
- **Logout revokes ALL sessions for that user.** Logging out (or any
  password change — self-service, Chairman-issued, or the recovery
  script) bumps `token_version`, which invalidates every token minted
  before it, on every device. There is no per-device session list and no
  way to revoke a single device: logging out on a phone logs the same
  user out on their laptop too.
- **Cookies:** `HttpOnly`, `Secure` + `__Host-` prefix in production,
  `SameSite=lax`. The `__Host-` prefix means the cookie only works over
  HTTPS — this is why the compose path needs the TLS profile (Caddy)
  before it can be called production-ready.

## Accepted risk (recorded deliberately)

A stolen session cookie is usable for up to 8 hours, with no idle
cut-off and no way to revoke just that session — the only revocation
is "log out / change password", which ends the legitimate user's
sessions too. This is accepted because:

- The system has ~25 known users on managed devices, not a public
  audience; the realistic theft path is a shared/unlocked machine, not
  a network attack (cookies are `Secure` + `HttpOnly`, same-origin SPA,
  no CORS, Origin/Referer CSRF checks).
- A stolen password is the same blast radius, and password theft is
  already mitigated by the login throttle (5/user/15 min, IP breaker)
  and lockout alerts.
- The alternative — a session table with per-device tokens and an
  "active sessions" view — is a design change (see review D-m5), not a
  quick fix, and was deferred.

If the user base grows beyond the current group, or a session is ever
known-stolen, revisit: cut `JWT_EXPIRE_MINUTES`, add an idle timeout,
or build the per-device session table.

## What to do when a session is suspected stolen

1. The user changes their password (or the Chairman resets it) — this
   bumps `token_version` and kills every session, including the stolen
   one.
2. If the Chairman's session is suspected, also rotate `JWT_SECRET_KEY`
   — see the incident runbook ([BOT_Incident_Runbook_2026-11-09.md](BOT_Incident_Runbook_2026-11-09.md),
   "Leaked JWT_SECRET_KEY"): rotating the secret invalidates every
   session for every user at once.
3. Check `audit_logs` for actions from that user in the window
   (see the incident runbook's evidence-preservation step first — [BOT_Incident_Runbook_2026-11-09.md](BOT_Incident_Runbook_2026-11-09.md) §2).

> **မှတ်ချက် — 2026-11-17 ပြင်ဆင်ချက်:** ဒီစာရွက်ကို tracker-only
> system အတွက် ပြန်ရေးထားပါတယ်။ ERP (accounting / journals / CoA /
> inventory / payroll / leave) ကို 2026-11-17 မှာ ဖျက်ပြီးပါပြီ —
> အောက် ERP အဆင့်တွေ (BU nature, CoA provisioning, opening balance,
> reconcile job) အားလုံး မရှိတော့ပါ။

# Business Ops Tracker Go-Live Runbook

ပြင်ဆင်သည့်ရက်: 2026-10-21 · Code HEAD / test အရေအတွက်ကို ဒီစာရွက်ထဲက
နံပါတ်နဲ့ မယုံနဲ့ (အရင် HEAD/test နံပါတ်တွေ မှားနေခဲ့ဖူးတယ် — R-m3) —
deploy လုပ်ခါနီး `git log -1 --oneline` နဲ့ စစ်, suite က
`ENVIRONMENT=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/ -q`
နဲ့ run (အောင်မှ deploy).
အခြေအနေ: code လည်း complete, security လည်း စစ်/ပြင်ပြီးသား — ကျန်တာ go-live အဆင့်တွေပဲ။

---

## အဆင့် ၁ — App ထဲက clicks (server မလို, အခုလုပ်လို့ရ)

### 1A. BU နာမည်တွေ အတည်ပြု

Sidebar → **Manage → Business Units**. BU 23 ခု နာမည်မှန်လား စစ် —
BU18–BU23 ကို နာမည်အမှန် ပေးပြီး rename လုပ်။ (BU nature / CoA
provisioning ဆိုတာ ERP နဲ့အတူ ဖျက်ပြီးပါပြီ — nature ရွေးစရာ မရှိတော့ဘူး။)

| BU | နာမည် |
|---|---|
| BU1 | ABC Convenience Store |
| BU2 | ABC Logistics (Logistics) |
| BU3 | ABC Logistics (Flour) |
| BU4 | ABC Logistics (Project) |
| BU5 | ABC Telecom |
| BU6 | ABC Content Solution |
| BU7 | ABC Beauty (Principal) |
| BU8 | ABC Beauty (Distribution) |
| BU9 | ABC Medical |
| BU10 | Hope Land Real Estate |
| BU11 | Myanmar Herbal Medicine |
| BU12 | MIB Coffee |
| BU13 | MIB Production |
| BU14 | MIB FMCG Distribution |
| BU15 | MIB FMCG Principal (PM) |
| BU16 | MIB FMCG Principal (HM) |
| BU17 | MIB FMCG Principal (Shiny) |
| BU18–BU23 | နာမည်မရှိသေး — rename လုပ် |


### 1B. User accounts (data အမှန် မဝင်ခင်)

Manage Users → CEO accounts ဖန်တီး (one-time temporary password အလိုအလျောက် ထုတ်).
Chairman account ပဲ အရင် ရှိတယ်.

---

## အဆင့် ၂ — Server deploy (Neon + VPS)

### 2A. Neon

1. Neon dashboard → **project အသစ်** ဖွင့် (KPI ရဲ့ project နဲ့ မရောနဲ့ — tracker က သပ်သပ် project)
2. Connection string ကို ကူး → အောက် `DATABASE_URL` မှာ သုံး
3. **Neon PITR (point-in-time recovery) ဖွင့် + retention window အတည်ပြု** — dashboard
   မှာ လုပ်ရမယ် (repo ကနေ လုပ်လို့ မရ)။ PITR + weekly `pg_dump` + quarterly restore
   drill အပြည့်အစုံ: `docs/backup-recovery.md`။ **RPO 1 နာရီ, RTO 4 နာရီ** —
   go-live မတိုင်ခင် restore drill တစ်ခါ လုပ်+မှတ်ထားရမယ် (gate)။

### 2B. Secrets (တစ်ခါတည်း ထုတ်)

```bash
openssl rand -hex 32   # → JWT_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # → DATA_ENCRYPTION_KEY
```

**⚠️ Secrets ကို command line မှာ `export` နဲ့ လုံးဝ မရိုက် (D-m10) — shell
history ထဲ ကျန်မယ်။** တန်ဖိုးတွေကို repo folder ထဲက `.env` file ထဲ ထည့်
(`chmod 600 .env` — Compose က အလိုအလျောက် ဖတ်တယ်, export လုပ်စရာ မလို)။
တန်ဖိုးထည့်တာ `read -s` နဲ့ လုပ် — terminal ပေါ် မပေါ်ဘူး, history ထဲ
မကျန်ဘူး:
```bash
read -s -p "JWT_SECRET_KEY: " V; printf 'JWT_SECRET_KEY=%s\n' "$V" >> .env
read -s -p "DATA_ENCRYPTION_KEY: " V; printf 'DATA_ENCRYPTION_KEY=%s\n' "$V" >> .env
chmod 600 .env
```
Secret တစ်ခု command line မှာ မှားပါသွားရင် ချက်ချင်း `history -d <နံပါတ်>`
နဲ့ ဖျက် (သို့မဟုတ် `HISTCONTROL=ignorespace` ထား + command အရှေ့ space
တစ်လုံး ခံ)။ `.env` ကို git ထဲ ဘယ်တော့မှ မထည့် (`.gitignore` မှာ ရှိ
ပြီးသား)။

**⚠️ DATA_ENCRYPTION_KEY ကို server ပြင်ပ (password manager) မှာ သိမ်းထား** — DB ထဲက
ပုဂ္ဂိုလ်ရေးရာ data (CEO ဖုန်း/email/လိပ်စာ/မွေးနေ့, health notes) ကို ဒီ key နဲ့ encrypt
ထားတယ်; key ပျောက်ရင် အဲ့ data တွေ ပြန်ဖတ်လို့ မရတော့ဘူး. App က production မှာ ဒီ key
မရှိရင် စတောင် မစဘူး (`src/config.py`) — compose file မှာ required ဖြစ်တယ်.

### DATA_ENCRYPTION_KEY — off-platform copy check and rotation

Why: the key encrypts CEO phone/email/address/date-of-birth and health
notes (`src/crypto.py`, `ENCRYPTED_COLUMNS`). Losing every copy makes those
fields unrecoverable — there is no reset path. A leaked copy decrypts all of
them. The key lives in the same env plane as `DATABASE_URL` and
`JWT_SECRET_KEY`, so keep its off-platform copy somewhere else (password
manager / sealed envelope — never in the repo, never in the same env dump).

Go-live gate — verify the off-platform copy (before first real data, and
again after any restore or server move):
1. Copy one stored ciphertext from the database, e.g. in the Neon SQL
   editor: `SELECT phone_number FROM ceo_profiles LIMIT 1;`
   (value starts with `enc:v1:`).
2. On a machine that has ONLY the off-platform copy (not the server env):
   `python -c "from cryptography.fernet import Fernet; print(Fernet(b'<COPY>').decrypt(b'<TOKEN>').decode())"`
   where `<TOKEN>` is the value after `enc:v1:`.
   It must print a phone number. If it raises `InvalidToken`, the copy is
   wrong — STOP, do not go live, and recover the correct key before any
   more encrypted data is written.
3. Record the check (date + who) in the go-live log.

Rotation schedule:
- Annually (calendar reminder, same time as the restore drill), AND
- immediately when anyone who could see the key leaves the team, AND
- immediately on any suspected leak (follow the incident runbook; a leak
  means re-encrypting under a new key and revoking the old one).
- Procedure (already documented in `src/scripts/rotate_data_key.py`):
  1. generate a new key, 2. set
  `DATA_ENCRYPTION_KEY="<new>,<old>"` (newest first) and redeploy,
  3. run `python -m src.scripts.rotate_data_key`, 4. remove the old key
  and redeploy. Update the off-platform copy at step 2 and re-run the
  verification above at step 4.

Graceful failure: a row encrypted under a lost/rotated-out key no longer
500s the CEO profile page — `GET /api/ceo/profile` returns `"unavailable"`
for the encrypted fields (and `profile_fields_unavailable: true`); the
dashboard shows no profile. That is a signal to restore the key, not a
state to leave in place.

**⚠️ R2 uploads — go-live အတွက် လိုအပ်တယ် (optional မဟုတ်):** production မှာ R2
မချိတ်ထားရင် upload တွေ အသံထွက် ပျက်တယ် (`src/r2_storage.py` → `upload_fileobj()`
က `None` ပြန်တယ်) — local volume ကို fallback သုံးတာ **dev-only** ဖြစ်တယ်. R2 မထည့်ရင်
ပျက်မယ့် flows: CEO/HO daily-report attachments, CEO profile-photo
uploads, license-document uploads, တခြား attachment uploads အားလုံး. ဒါကြောင့် အောက် R2 တန်ဖိုးတွေကိုလည်း အပေါ် `.env` file ထဲပဲ
ထည့် (`export` မလုပ်):

```
R2_ACCOUNT_ID='<Cloudflare R2 account id>'
R2_ACCESS_KEY_ID='<R2 access key>'
R2_SECRET_ACCESS_KEY='<R2 secret key>'
R2_BUCKET_NAME='business-ops-tracker'
R2_ENDPOINT_URL='https://<account-id>.r2.cloudflarestorage.com'
R2_PUBLIC_BASE_URL='<public base URL — ရှိရင်>'
```

**⚠️ အရေးကြီး — chairman password:** production first boot မှာ chairman account ကို
`BOOTSTRAP_CHAIRMAN_PASSWORD` env ရှိရင် အဲ့ဒါနဲ့ seed လုပ်တယ် — env က အားနည်းရင် app က
စတောင် မစဘူး (password policy စစ်တယ်, `src/config.py`). **မရှိရင် random password ထုတ်ပြီး
`/app/uploads/.bootstrap_chairman_password` file ထဲ 0600 နဲ့ သိမ်းတယ်** (log ထဲမှာ အစပိုင်း
4 လုံးပဲ hint အနေနဲ့ ပြတယ်, အပြည့်အစုံ မပြဘူး) — server မှာ
`docker compose exec web cat /app/uploads/.bootstrap_chairman_password` နဲ့ ယူ, first login
ပြီးရင် file ကို ဖျက်. မလုပ်မိရင် login ဝင်မရဘူး.
ဒါကြောင့် `BOOTSTRAP_CHAIRMAN_PASSWORD` ကိုလည်း အပေါ် `.env` file ထဲ ထည့်
(`export` မလုပ်) — အားနည်းတဲ့ password ထားရင် app က စတောင် မစဘူး.

### 2C. Server (ဖုန်း SSH)

```bash
cd ~/business-ops-tracker        # repo clone လုပ်ထားတဲ့ folder (path မတူရင် ညှိ)
git pull
# Deploy လုပ်ခါနီး head ကို အရင် စစ် (runbook ထဲက နံပါတ်/HEAD ကို မယုံနဲ့ —
# `alembic heads` နဲ့ `git log` ပဲ အမှန်):
alembic heads                    # → single head a720b6b7c616
git log -1 --oneline
# Secrets တွေက §2B မှာ `.env` file (0600) ထဲ ရောက်ပြီးသား — Compose က
# အလိုအလျောက် ဖတ်တယ်, ဒီမှာ `export` လုပ်စရာ မလို (D-m10: command line
# မှာ secret ရိုက်ရင် shell history ထဲ ကျန်မယ်)။ `DATABASE_URL` ကိုလည်း
# §2A က Neon connection string နဲ့ `.env` ထဲ ထည့်
# (`DATABASE_URL='postgresql://...'`)။
# TLS is now the DEFAULT path (SM-2): Caddy always starts with `up` and
# DOMAIN is required — `up` fails loudly without it. No --profile tls
# anymore, and there is no HTTP-only production path.
# export DOMAIN=erp.example.com   # DOMAIN က secret မဟုတ် — export လုပ်လို့ရ
# Deploy with no old-code/new-schema window (PM-7): run
# scripts/deploy.sh — it stops the app AND Caddy, runs the migration,
# then starts both and waits for /readyz. Plain `up -d --build`
# recreates the container while the old code is still serving, so a
# deploy is a hard restart either way. Do NOT deploy with a bare
# `up -d app`: Caddy would never start on a first deploy (F-1) and
# would serve 502s through the window on later ones (F-10).
./scripts/deploy.sh
# Manual equivalent of what the script does (only if the script itself
# cannot run):
#   docker compose -f docker-compose.prod.yml stop app caddy
#   docker compose -f docker-compose.prod.yml run --rm migrate
#   docker compose -f docker-compose.prod.yml up -d --build app caddy
```

ပြီးရင် စစ်:

```bash
docker compose -f docker-compose.prod.yml logs migrate --tail=20
# → `alembic upgrade head` အောင်မြင်ကြောင်း ပေါ်ရမယ် (migrate service က
#    app မစခင် အရင် run တယ်; migration ပျက်ရင် app က လုံးဝ မစ — အရင်လို
#    crash-loop မဖြစ်ဘူး)
docker compose -f docker-compose.prod.yml logs app --tail=40
# → chairman seeded လို့ ပေါ်ရမယ်
curl -s http://127.0.0.1:8123/healthz    # → 200
```

လကုန်ရက် သတိ (PM-5/SM-11): CEO ၂၅ ယောက်လောက် တစ်ပြိုင်တည်း သုံးရင် DB pool
(5+5) ပြည့်ပြီး 503 "Database unavailable" ဖြစ်နိုင်တယ် — report တွေကို SQL
aggregation နဲ့ ပြင်ပြီးပြီ, ဒါပေမယ့် လကုန်မနက် traffic ကို ခွဲသုံးတာ အကောင်းဆုံး
(CEO တွေ အလှည့်ကျ submit လုပ်)။ 503 တွေ့ရင် log_scan (09:00) က pool signal
တွေ ပြန်စစ်ပေးတယ်။

မှတ်ချက်များ:
- Migrations ကို one-shot **`migrate` service** က app မစခင် run တယ်
  (`RUN_MIGRATIONS_ON_STARTUP=false`) — လက်နဲ့ alembic run စရာ မလို။
  Rollback: code revert + rebuild — ⚠️ head revision `a720b6b7c616` က
  one-way ဖြစ်တယ် (downgrade က ငြင်းတယ်) — ပြန်ယူချင်ရင် ERP ဖျက်ခြင်း
  မတိုင်ခင် backup ကနေ restore လုပ်; downgrade ဟာ `e4f8a2b6d0c5` ကို
  ကျော်ရင် scrub လုပ်ပြီးသား audit data ပြန်မရဘူး, forward-fix ကို ဦးစားပေး။
- **Health check — `/readyz` က DB လည်း စစ်တယ်:** `curl http://127.0.0.1:8123/readyz` →
  200 ဆို DB လည်း OK (Docker/DO health check က အဲ့ကို ချိန်ထားတယ်); `/healthz` က
  process ပဲ စစ်တယ်. Startup မှာ DB ပျက်ရင် 5 ကြိမ် retry (~60 စက္ကန့်) လုပ်တယ် —
  log မှာ "database unreachable" vs "migration failed" ခွဲထားတယ်. Run-time မှာ DB ခန
  ပျက်ရင် 503 "Database unavailable" ပြန်တယ် (500 မဟုတ်)။
- **HTTPS — go-live gate (နောက်မှ မဟုတ်):** TLS is the DEFAULT
  production path (SM-2) — Caddy always starts with `up` and DOMAIN is
  required: `up` fails loudly without it, and there is no `--profile tls`
  anymore and no HTTP-only production path. Set `DOMAIN` + deploy with
  `./scripts/deploy.sh` and Caddy does HTTPS automatically (Let's
  Encrypt) — Caddyfile + compose ထဲက `caddy` service ကြည့်။ Domain
  မရှိသေးရင် app ကို တိုက်ရိုက် public မဖော်နဲ့: production cookie တွေက
  Secure + `__Host-` ဖြစ်လို့ HTTP ပေါ်မှာ browser က လက်မခံဘူး → login ပါ
  ပျက်တယ်။ `ALLOWED_HOSTS` / `ALLOWED_ORIGINS` က DOMAIN ကနေ အလိုအလျောက်
  ဖြစ်, `TRUSTED_PROXY_HOPS=1` (Caddy တစ်ဆင့်တည်း) — compose file ထဲ
  မှတ်ထားတယ်။
- **Least-privilege DB roles — data အမှန် မဝင်ခင် gate (နောက်မှ မဟုတ်):**
  `deploy/db_roles.sql` ကို Neon owner အနေနဲ့ တစ်ခါ run → `DATABASE_URL` ကို
  bot_app URL, `MIGRATION_DATABASE_URL` ကို bot_migrator URL ထား →
  `RUN_MIGRATIONS_ON_STARTUP=false` (compose မှာ အသင့် false ဖြစ်ပြီးသား) →
  file ထဲက verify checks (bot_app အနေနဲ့ `DELETE FROM audit_logs` /
  `DROP TABLE users` တို့ fail ဖြစ်ရမယ်) run။ မလုပ်ရသေးရင် default က
  owner URL နဲ့ run နေတယ် — `DATABASE_URL` leak ဖြစ်ရင် table အကုန် ဖျက်လို့ရ။
- **Scheduled jobs:** `crontab deploy/crontab` install → `crontab -l` နဲ့
  အတည်ပြု (output ကို ဒီမှာ မှတ်) — 4 ခု:
  scheduled_alerts (နေ့စဉ် 08:30),
  audit_retention (Sunday 03:00, MIGRATION_DATABASE_URL လိုတယ်),
  log_scan (နေ့စဉ် 09:00), backup (Sunday 03:30)။ Log တွေက `~/bot-*.log`။
- **Session policy:** 8 နာရီ absolute max, idle timeout မရှိ, logout ဆို
  session အားလုံး revoke — accepted risk အဖြစ် မှတ်ထားတယ်
  (`docs/session-policy.md`)။ Logout က ဖုန်းတစ်လုံးတည်းက မဟုတ် —
  device တိုင်းက session အကုန် ပျက်တယ်; တစ်လုံးတည်း revoke လုပ်တဲ့
  per-device view မရှိ (session table လိုတယ် — out of scope,
  `docs/session-policy.md` မှာ မှတ်ထားတယ်) (D-m5)။
- **Proxy / client IP (D-m2):** Caddy က app ရှေ့မှာ **တစ်ဆင့်တည်း** —
  `X-Forwarded-For` ကို ညာဘက်က 1 ခု ရေတွက် (`TRUSTED_PROXY_HOPS=1`,
  compose မှာ ရှိ); `CLIENT_IP_HEADER` ကို တမင် မထား (X-Forwarded-For က
  list ဖြစ်လို့ `src/client_ip.py` က လက်မခံ)။
- **Proxy / Host (P3-8):** Caddy က `Host` header ကို ပြောင်းမပေးဘူး —
  လက်ခံရရှိတဲ့အတိုင်း ဖြတ်ပေးတယ်။ ဒါကြောင့် Host စစ်တာက app ရဲ့
  `ALLOWED_HOSTS` allowlist (DOMAIN ကနေ အလိုအလျောက်) — public domain
  မဟုတ်တဲ့ Host ဆို 400 နဲ့ ပယ် (Origin check မရောက်ခင်)။
  `ALLOWED_HOSTS` ကို `*` လုံးဝ မထား။
- **DB TLS (D-m8):** compose မှာ `DATABASE_SSLMODE=verify-full` +
  `DATABASE_CA_CERT` (Neon ရဲ့ ISRG Root X1 PEM) — CA cert မရှိရင် app က
  စတောင် မစဘူး (fail-loud — go-live gate)။ Cert မရသေးရင် interim အနေနဲ့
  `require` ထားလို့ရ (encrypt ဖြစ်တယ်, verify မဖြစ်ဘူး) — ဒါပေမဲ့ data
  အမှန် မထည့်ခင် verify-full ဖြစ်အောင် လုပ်။
- **Logs — retention (A-m1):** VPS မှာ container log တစ်ခုကို 10 MB × 3
  files ပဲ ထား (compose `logging`, D-m9 — ~30 MB ကျော်ရင် အဟောင်း ပျက်)။
  DO App Platform မှာ runtime log က ရက်ပိုင်းပဲ ရှိပြီး redeploy လုပ်ရင်
  ပျက် — လိုတာကို redeploy မလုပ်ခင် export လုပ်။ `reset_chairman.py` က
  temporary chairman password ကို stdout မှာ ပြတယ် (by design,
  တစ်ခါသုံး + first-login change) — အဲ့ exposure ကို လက်ခံထားတယ်,
  retention bound က အပေါ်ကဟာပဲ။
- **Data retention / erasure (A-m3):** `audit_retention` က Sunday 03:00
  run — login rows 90 ရက် ကျော်ရင် ဖျက်, IP 30 ရက် ကျော်ရင် clear,
  report/note rows ကို ထား (tamper evidence)။ Owner role
  (`MIGRATION_DATABASE_URL`) လိုတယ် — runtime role (`bot_app`) က
  `audit_logs` ကို DELETE လုပ်လို့ မရ; `~/bot-audit-retention.log` မှာ
  "permission denied" တွေ့ရင် `MIGRATION_DATABASE_URL` မပါဘဲ `up` လုပ်မိတာ
  — `up -d` ပြန်လုပ်။ ထွက်သွားတဲ့ CEO/user ဆို **deactivate လုပ်, hard
  delete မလုပ်**; ကိုယ်ရေးအချက်အလက် (6 encrypted columns) ကို
  `"[erased]"` / DOB `1900-01-01` နဲ့ app layer ကနေ ဖျက် (script
  မရှိသေးဘူး — ပထမအကြိမ် လိုအပ်မှ ရေးရမယ်); `audit_logs.username` +
  reports ကို evidence အဖြစ် ထား။ အပြည့်အစုံ:
  `docs/security-decisions.md` (A-m3)။
- **Capacity — ပထမ bottleneck (A-m4):** DB pool (4+4, 10 စက္ကန့်) က CPU
  ထက် အရင် ပြည့်တယ် — exports တွေက in-request sync ဖြစ်လို့ connection
  ကိုင်ထားတယ်။ Pool-timeout 500 တွေက log မှာ `ERROR` line အနေနဲ့ ပေါ်တယ်
  — နေ့စဉ် 09:00 log scan က ရေတွက်ပြီး Chairman ကို notify လုပ်တယ်
  (သီးသန့် pool counter မရှိ)။ Disk-full က ဒုတိယ failure mode —
  log rotation (D-m9) နဲ့ ကန့်ထားပြီးသား။ Single uvicorn worker ပဲ
  (`--workers` မရှိ — export budget ကို process memory ကနေ ရွှေ့ပြီးမှ
  ထည့်ရမယ်)။
- **Secret rotation (D-m10):** JWT rotate → redeploy → user အားလုံး
  ပြန်ဝင် (incident runbook §3A, rehearsal §6); R2 keys → အဟောင်း revoke
  အရင်, ပြီးမှ အသစ် (§3C); DATA_ENCRYPTION_KEY → ဒီစာရွက် §2B အောက်
  rotation အပိုင်း + incident runbook §3D။ ဒီမှာ ထပ်မရေးဘူး —
  incident runbook ကိုပဲ ကိုး။
- **တခြား ဆုံးဖြတ်ချက်တွေ:** ဘယ် field တွေ encrypt ထားတယ် /
  `DATABASE_URL` leak ဖြစ်ရင် ဘာဖြစ်မယ် (R-m1), password policy က
  length-only ဖြစ်တာ တမင် ဆုံးဖြတ်ထားတယ် (R-m2) —
  `docs/security-decisions.md` မှာ ဖတ်။
- **Incident runbook:** `BOT_Incident_Runbook_2026-11-09.md` (ဒီ file နား) —
  secret leak ဖြစ်ရင် အဲ့ အတိုင်း လုပ်; JWT rotation ကို go-live မတိုင်ခင်
  တစ်ခါ rehearsal လုပ်။
- Dev login `Chairman123!` က **local dev DB အတွက်ပဲ** — production က `BOOTSTRAP_CHAIRMAN_PASSWORD` နဲ့ ဝင်.

### 2D. Deploy အတည်ပြု (push ≠ live — KPI သင်ခန်းစာ)

1. Browser မှာ public URL ဖွင့် → chairman login ဝင်
2. CEO dashboard ပေါ်လား, Business Units page မှာ BU 23 ခု မှန်လား
3. CEO test account တစ်ခုနဲ့ daily report submit လုပ်ပြီး dashboard မှာ ပေါ်လား,
   attachment တစ်ခု upload/resolve လုပ်လား

---

## အဆင့် ၃ — နောက်မှ လုပ်လို့ရတာတွေ (go-live day မှာ မလို)

- **Backup/restore drill:** `docs/backup-recovery.md` အတိုင်း quarterly drill
  (scratch DB မှာ restore + migration-chain check) — ပထမဆုံး drill က go-live gate။
  R2 bucket versioning ကို Cloudflare dashboard မှာ ဖွင့်ထား (repo ကနေ လုပ်လို့ မရ)။
- **Incident runbook rehearsal:** JWT rotation ကို တစ်ခါ ကျင့်ထား —
  `BOT_Incident_Runbook_2026-11-09.md` §6။
- **Telegram notify (option 10)** — အရင် list မှာ မရွေးခဲ့တာ, backlog မှာ ကျန်နေသေးတယ်. လိုချင် ပြော.
- **R2 uploads** — §2B မှာ လုပ်ရမယ် (go-live အတွက် required) — မထည့်ရင် production မှာ
  CEO/HO daily-report attachments, CEO profile-photo uploads, တခြား attachment
  uploads အားလုံး ပျက်တယ်
  (local volume fallback က dev-only).

---

## အကျဉ်း — မင်း လုပ်ရမှာ

- [ ] Business Units page: BU 23 ခု နာမည်မှန်လား အတည်ပြု (BU18–23 rename)
- [ ] CEO accounts ဖန်တီး
- [ ] "deploy လုပ်မယ်" ဆုံးဖြတ် → အဆင့် ၂ commands run
- [ ] အဆင့် 2D verify

ပြီးရင် tracker က live data နဲ့ စသုံးလို့ရပြီ. 🎉

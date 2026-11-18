> **မှတ်ချက် — 2026-11-17 ပြင်ဆင်ချက်:** ဒီစာရွက်ကို tracker-only
> system အတွက် ပြန်ရေးထားပါတယ်။ ERP (accounting / journals / CoA)
> ဖျက်ပြီးပါပြီ — journal-failure response နဲ့ trial-balance စစ်ဆေးမှု
> တွေ မရှိတော့ပါ; verify အဆင့်တွေက CEO dashboard + attachment ဖြစ်သွားပါပြီ။

# Incident Runbook — Business Ops Tracker

ရက်: 2026-11-09 · Security review (A-2, D-7) အရ ရေး — go-live runbook
(`BOT_GoLive_Runbook_2026-10-21.md`) ရဲ့ အဖော် စာရွက်။ Incident ဖြစ်ရင်
ဒီအတိုင်း လုပ်; မသေချာရင် **evidence အရင် သိမ်း, ပြီးမှ ပြင်**။

## 1. Detection — ဘယ်လို သိမလဲ

| Signal | ဘယ်မှာကြည့်လဲ |
|---|---|
| Login lockout တွေ ရုတ်တရက် များလာတာ | `~/bot-log-scan.log` (`lockout` lines) / `GET /api/admin/security-log` (Chairman only) |
| မမျှော်လင့်တဲ့ audit actions (ဥပမာ — Chairman account က ညသန်းခေါင် daily report submit) | `audit_logs` — export လုပ် (အောက် §2) ပြီးမှ စစ် |
| R2 / Neon ကို IP အသစ် / နိုင်ငံအသစ်က access | Cloudflare / Neon dashboard access logs |
| User က "ငါ မလုပ်တဲ့ action တွေ ငါ့ account နဲ့ ဖြစ်နေတယ်" လို့ ပြောတာ | အဲ့ user ရဲ့ `audit_logs` + `token_version` စစ် |
| App က 500 တွေ အများကြီး / DB down | `~/bot-log-scan.log` (`ERROR` lines) |

ဘယ် signal မဆို — အရင် **§2 evidence သိမ်း**, ပြီးမှ §3 containment.

## 2. Evidence preservation — ပြင်ဆင်တာ မလုပ်ခင် အရင် လုပ်

**`audit_retention` ကို လုံးဝ မ run ပါနဲ့** (90-day login rows ဖျက်တာ + IP
clear လုပ်တာ — evidence ပျက်မယ်)။ Cron ထဲက Sunday 03:00 run ကို
incident ပြီးတဲ့အထိ ခေတ္တ ရပ် (`crontab -e` မှာ comment ထုတ်)။

```bash
cd ~/business-ops-tracker
# audit log အပြည့်အစုံ export (DB ဖတ်လို့ ရသေးရင်) — evidence copy
pg_dump "$DATABASE_URL" -t audit_logs -Fc -f ~/audit-logs-evidence-$(date +%Y%m%d).dump
# ဒါမှမဟုတ် docker မရှိရင်: docker run --rm -e PGURL="$DATABASE_URL" \
#   -v ~:/backup postgres:16-alpine sh -c 'pg_dump "$PGURL" -t audit_logs -Fc -f /backup/audit-logs-evidence.dump'
# app logs သိမ်း
docker compose -f docker-compose.prod.yml logs app --since 72h > ~/app-logs-evidence-$(date +%Y%m%d).log 2>&1
```

မှတ်ချက်: `audit_logs` က **append-only ဖြစ်တာ `deploy/db_roles.sql`
apply ပြီးမှသာ** (bot_app က UPDATE/DELETE လုပ်လို့ မရ)။ Roles မလုပ်ရသေးရင်
attacker က audit rows ကို ပြင်/ဖျက်နိုင်တယ် — ဒါကို သိထား၊ export ထဲက
rows တွေကို အပြည့်အဝ မယုံနဲ့။

## 3. Containment — secret အမျိုးအစားအလိုက် (exact commands)

မူလ: secret အသစ် ထုတ် → env update → redeploy → အကောင့် ပြန်ဝင်။
**ဘယ် secret ကို leak ဖြစ်မှန်း မသေချာရင် အားလုံး rotate လုပ်။**

### 3A. Leaked `JWT_SECRET_KEY` (အဆိုးဆုံး — Chairman အပါအဝင် ဘယ်အကောင့်မဆို forge လုပ်လို့ရ)

HS256 symmetric signing, single key, payload `{"sub","tv","exp"}` —
key ရှိသူက `tv` (0 ကနေ စတဲ့ integer လေး) ခန့်မှန်းပြီး Chairman token
ထုတ်လို့ရ။ Rotation rehearsal ကို go-live မတိုင်ခင် တစ်ခါ လုပ်ထား (အောက် §6)။

```bash
# 1. Secret အသစ် ထုတ် (server မှာ — shell history ထဲ မကျန်အောင်
#    `export` နဲ့ command line မှာ တိုက်ရိုက် မရိုက်နဲ့; .env file 0600 နဲ့ သုံး)
python -c "import secrets; print(secrets.token_urlsafe(48))"
# 2. JWT_SECRET_KEY ကို အသစ်နဲ့ အစားထိုး → redeploy
docker compose -f docker-compose.prod.yml up -d --build
# 3. ပြီးပြီ: secret အဟောင်းနဲ့ ထုတ်ထားတဲ့ token အားလုံး ချက်ချင်း ပျက် —
#    user တိုင်း ပြန်ဝင် (re-login) ရမယ်။ ဒါကို user တွေကို ကြိုပြော (§5)။
```

DO path ဆို: DO dashboard → app env vars → `JWT_SECRET_KEY` အသစ် →
redeploy (deploy_on_push ဆို push လုပ်)။

### 3B. Leaked `DATABASE_URL`

```bash
# 1. Neon dashboard → database password rotate (သို့) database အသစ် ဖန်တီး
# 2. DATABASE_URL အသစ် + (roles သုံးရင်) MIGRATION_DATABASE_URL အသစ် ထား
#    — roles သုံးနေရင် bot_app + bot_migrator password နှစ်ခုလုံး rotate:
#    Neon dashboard မှာ role တစ်ခုချင်း password ပြောင်း, deploy/db_roles.sql
#    ကို ပြန် run စရာ မလို (role တွေက ရှိနေပြီးသား)
# 3. Redeploy
docker compose -f docker-compose.prod.yml up -d --build
# 4. အဟောင်း URL နဲ့ ချိတ်လို့ မရတော့ဘူး စစ်: psql "<old URL>" -c 'select 1'
#    → connection/auth failed ဖြစ်ရမယ်
```

### 3C. Leaked R2 keys (`R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`)

```bash
# 1. Cloudflare dashboard → R2 → API Tokens → အဟောင်း key ကို revoke/delete
#    (rotate မလုပ်ခင် revoke အရင် — key အဟောင်း ဆက်သုံးလို့ မရအောင်)
# 2. Key အသစ် ထုတ် → R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY update
# 3. Redeploy
docker compose -f docker-compose.prod.yml up -d --build
# 4. Upload စမ်း (CEO daily-report attachment တစ်ခု) — R2 ပြန်တက်ကြောင်း အတည်ပြု
```

Bucket က public ဖြစ်နေလား (`R2_PUBLIC_BASE_URL` သုံးနေရင်) — အဲ့
setting ကို incident ပြီးတဲ့အထိ ပြန်စစ်; public base URL ရှိရင်
upload link တိုင်း bearer URL (ရတိုင်း ဖတ်လို့ရ) ဖြစ်တယ်။

### 3D. Leaked `DATA_ENCRYPTION_KEY`

အဆိုးဆုံး အချက်: **rotation က အနာဂတ် data ပဲ ကာကွယ်တယ်** — key
အဟောင်းနဲ့ encrypt ထားတဲ့ CEO ဖုန်း/email/လိပ်စာ/မွေးနေ့, health notes
တွေကို attacker က ဖတ်ပြီးသားဆို ပြန်ရုပ်သိမ်းလို့ မရဘူး။ ဒါကို
Chairman ကို တိတိကျကျ ပြော (§5)။

```bash
# 1. Key အသစ် ထုတ်
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 2. DATA_ENCRYPTION_KEY="<new key>,<old key>" (အသစ် အရှေ့) ထား → redeploy
#    — app က key နှစ်ခုလုံးနဲ့ ဖတ်လို့ရ, အသစ်နဲ့ပဲ ရေးတယ်
docker compose -f docker-compose.prod.yml up -d --build
# 3. Data အားလုံး key အသစ်အောက် ပြန် encrypt (container ထဲမှာ run)
docker compose -f docker-compose.prod.yml exec -T app python -m src.scripts.rotate_data_key
# 4. "nothing left to change" လို့ ပြရင် old key ကို DATA_ENCRYPTION_KEY က
#    ဖြုတ် → redeploy
docker compose -f docker-compose.prod.yml up -d --build
# 5. Off-platform copy (password manager) ကိုလည်း အသစ်နဲ့ update
```

### 3E. Suspected unauthorised access (ဘယ် secret leak မှန်း မသိရင်)

1. §2 evidence သိမ်း။
2. **Chairman password reset** — `src/scripts/reset_chairman.py` (session
   အားလုံး revoke + temporary password ထုတ်, first-login change 강제):
   ```bash
   docker compose -f docker-compose.prod.yml exec -T app python -m src.scripts.reset_chairman
   ```
3. သံသယရှိတဲ့ user တိုင်းကို password reset ခိုင်း (Chairman က
   Manage Users → reset-password) — password change တိုင်းက အဲ့ user ရဲ့
   session အားလုံး revoke လုပ်တယ်။
4. မသေချာရင် §3A–3D အားလုံး rotate လုပ် (JWT အရင် — အမြန်ဆုံး + အကျယ်ဆုံး)။

### 3F. Data destruction / ransomware / DB ပျက်စီးတာ

1. App ရပ်: `docker compose -f docker-compose.prod.yml down` (volume တွေ ကျန်တယ်)။
2. Restore — `docs/backup-recovery.md` အတိုင်း: Neon PITR အရင်,
   မရရင် weekly `pg_dump` ကနေ။ **Live DB ပေါ် တိုက်ရိုက် restore မလုပ် —
   scratch DB မှာ အရင် restore + စစ်, ပြီးမှ `DATABASE_URL` ပြောင်း။**
3. Restore ပြီးရင် go-live runbook §2D verify (login, CEO dashboard,
   one attachment resolves)。

## 4. Recovery — ပြန်ဖွင့်ဖို့ checklist

- [ ] Leak ဖြစ်တဲ့ secret အားလုံး rotate + redeploy ပြီးပြီ
- [ ] User တိုင်း ပြန်ဝင်နိုင်တယ် (JWT rotate ဆို အားလုံး re-login)
- [ ] CEO dashboard ပေါ်တယ်, daily report တစ်ခု submit လုပ်လို့ရတယ်
- [ ] R2 upload တစ်ခု စမ်းပြီးပြီ
- [ ] Cron jobs ပြန်ဖွင့် (incident အတွင်း ရပ်ထားရင်)
- [ ] Incident မှတ်တမ်း ရေး (အောက် §6) — ဘာဖြစ်တယ်, ဘာလုပ်တယ်, ဘယ်လောက် ကြာတယ်

## 5. User comms — ဘာပြောမလဲ

Chairman က ဆုံးဖြတ်ပြီး ပို့တဲ့ စာတိုပုံစံ (copy-paste, အခြေအနေ အလိုက် ဖြည့်):

> "Business Ops Tracker မှာ security ပြဿနာ တစ်ခု တွေ့လို့ [ရက်/အချိန်] မှာ
> password အားလုံး reset လုပ်ခဲ့ပါတယ်။ အားလုံး ပြန်ဝင်ပြီး password
> အသစ် ထားပေးပါ။ [ဒေတာ ပျက်စီးမှု ရှိ/မရှိ — ရှိရင် ဘာတွေ, ဘယ်အချိန်က
> backup ကနေ ပြန်ယူခဲ့တယ်]။ မေးစရာ ရှိရင် Chairman ကို ဆက်သွယ်ပါ။"

- JWT rotate လုပ်ရင်: **အားလုံး ပြန်ဝင်ရမယ်** ဆိုတာ ကြိုပြော (ရုတ်တရက်
  logout ဖြစ်တာ bug မဟုတ်)။
- DATA_ENCRYPTION_KEY leak ဆို: ဘယ် data အမျိုးအစား ထိခိုက်နိုင်တယ်
  (CEO ကိုယ်ရေးအချက်အလက်) ဆိုတာ ဖုံးကွယ်မထားနဲ့ — ဒါပေမဲ့ ဘယ်
  တစ်ဦးချင်းရဲ့ data ဖတ်ခံရတယ်ဆိုတာ မသိရင် မသိဘူးလို့ ပြော။
- ဘယ်တော့မှ မပြောနဲ့: secret အသစ်ရဲ့ တန်ဖိုး, ဘယ် IP/ဘယ်သူက attack
  လုပ်တယ်ဆိုတဲ့ ခန့်မှန်းချက် (မသေချာရင်)။

## 6. Rehearsal + record — JWT rotation ကို တစ်ခါ ကျင့်ထား (D-7)

Go-live မတိုင်ခင် **တစ်ခါ rehearsal လုပ်** (staging/test DB နဲ့, ဒါမှမဟုတ်
go-live မတိုင်ခင် production မှာ user မဝင်သေးခင်):

1. `JWT_SECRET_KEY` အသစ် ထုတ် → redeploy → chairman ပြန်ဝင် → CEO
   test account တစ်ခု ပြန်ဝင် → အဟောင်း token နဲ့ API ခေါ်တာ 401
   ဖြစ်ကြောင်း အတည်ပြု။
2. အချိန်မှတ်: rotation + redeploy + re-login စုစုပေါင်း ဘယ်လောက် ကြာလဲ။
3. ဒီမှာ မှတ်:

| ရက် | ဘာလုပ်ခဲ့လဲ | ကြာချိန် | ရလဒ် |
|---|---|---|---|
| (မလုပ်ရသေးပါ) | | | |

Incident တိုင်း ပြီးရင် ဒီဇယားအောက်မှာ တစ်ကြောင်း ထည့် (ရက်, အမျိုးအစား,
containment, recovery time) — နောက်တစ်ခါ ဒီစာရွက် ဖတ်တဲ့သူက အချိန်မှန်း
လို့ရအောင်။

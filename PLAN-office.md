# The office: one server next to Tally, a small control plane, and who may see what

Prepared 21 October 2026. Not started. Replaces the Google Drive parts of PLAN-keeper.md:
its sections 4, 8, 12 (hosting and opening), 13 (Drive rows) and 16 (Phases 4 and 7). The
Keeper's Tally reading, snapshot types, file format, sync engine and screens stand as
written there.

---

## 0. The short version

- **Who uses it.** A personal setup for one office:

  | Person | May use | Gets pushes for |
  |---|---|---|
  | **You** (super admin) | Everything, plus the admin console: people, devices, audit | Admin alerts |
  | **Boss** | The books, read-only (the Keeper viewer), on a phone and a laptop | Overdue bills, big transactions |
  | **Accountant** | The Bridge and the Assurance Console | - |
  | **Manager** | The Assurance Console | - |
  | **Purchase team** (one account each) | The books, read-only, all of them, on phones and laptops | Overdue bills |

  These are presets. Each person's access is a set of ticks you can change: `books`,
  `tib`, `console`, `admin`.

- **Three pieces:**
  1. **The office server:** the PC where Tally runs. It runs Ultimate Accountant for the
     whole office: the Bridge, the Console, the Keeper sync, the AI and the OCR. It keeps the one
     copy of their data next to Tally.
  2. **Office clients:** the accountant's and manager's PCs. They run the same app in
     *client mode*, which is just its window, signed in, onto the office server over the
     office network. Nothing runs or is stored on them.
  3. **The control plane:** small and hosted. It handles sign-in, the people and their
     access, every device, the encrypted Keeper snapshots and backups, push
     notifications, and the admin console. It also serves the viewer, the web app the
     boss and the purchase team open on any phone or laptop.

- **It is blind to the books.** The control plane stores the snapshots and backups only as
  ciphertext. The key to them lives on the office server and on the devices you have
  approved. Each approval is signed by your own admin device, and the office server
  checks that signature before giving any device the key. A leaked database or storage
  bucket gives away no figures. There is one limit to this, in section 9.

- **Tally is never exposed.** It is reached only by the office server, over localhost:
  the Bridge to post and the Keeper to read. The rules in PLAN-keeper.md section 2 (G1-G9) all
  stand. The new code, `app/office/`, follows the same rule: it never imports `app.bridge`
  or `app.backend`.

- **Offline is still fine inside the office.** If the internet drops, everyone already
  signed in carries on over the office network. Only new sign-ins, new devices, pushes
  and the Keeper's upload wait.

---

## 1. The whole picture

```
                       ┌──────────────────── control plane (hosted) ─────────────────────┐
                       │  FastAPI on Vercel (Mumbai, bom1)                                │
  Boss / purchase      │   /           viewer web app (PWA)       Google sign-in (OIDC)   │
  phone, laptop ──────►│   /admin      admin console              people, access, devices │
  (browser / PWA)      │   /api/…      the API                    audit log               │
                       │  Supabase Postgres: metadata  R2: ciphertext only  Web Push sender │
                       └───────▲───────────────────────────────────▲───────────────────────┘
                               │ HTTPS: roster, status, uploads,    │ HTTPS: sign-in,
                               │ wrapped keys, encrypted events     │ device registration
                       ┌───────┴────────── office network ──────────┴──────┐
                       │  OFFICE SERVER (the Tally PC)                      │
                       │   Ultimate Accountant, server mode, :8770 (TLS)    │
  Accountant PC ──────►│    sign-in check on every request (app/office)     │
  Manager PC   ──────► │    /bridge  Bridge  posts ──►  TallyPrime :9000    │
  (client mode,        │    /console Assurance Console                      │
   the app's window)   │    /keeper  Keeper sync ── reads (Export only) ──► │
                       │   holds: data key (DPAPI), Bridge and Console data  │
                       └─────────────────────────────────────────────────────┘
```

---

## 2. People, access and sign-in

### 2.1 Accounts
- **Everyone signs in with their Google account** (Gmail or Workspace). You add their
  email in the admin console; any other email is refused. There are no passwords of ours
  to store or reset.
- **Google OAuth client:** type *Web application*, in your own Cloud project. Scopes
  `openid email profile`, all non-sensitive, so no verification is needed. Consent
  screen set to *External, In production*. Redirect
  `https://<domain>/api/auth/google/callback`.
- **The control plane** runs the standard server-side authorization-code flow (with
  `state` and a `nonce`) and checks the ID token's signature, `aud`, `iss` and
  `email_verified`. This works the same in a phone's home-screen web app, which removes
  the old spike S2.

### 2.2 Sessions, per device
- **Each device has its own keypair,** made at install: ECDSA P-256 to sign and ECDH P-256
  to receive the data key.
  - On a PC it is DPAPI-held. In a browser it is a non-extractable WebCrypto key in
    IndexedDB. P-256 is used everywhere because every browser's WebCrypto has it.
- **Tokens:**
  - an *access token*: a JWT signed by the control plane with Ed25519, 15 minutes, with
    `sub`, `device`, `caps`
  - a *refresh token*: random, stored hashed, rotated on each use
- **A refresh must also be signed by the device's key,** over a challenge from the
  server. A stolen refresh token alone is useless.
- **In the browser** (viewer, admin console), these sit in `HttpOnly`, `Secure`,
  `SameSite=Lax` cookies on the control plane's own origin. Each API call also needs a
  CSRF token header.
- **On a PC** (office server, clients): the app opens the system browser to
  `/api/auth/google/start`, with its own PKCE challenge and a loopback return address.
  The control plane sends back a one-time code, and the app exchanges it with the PKCE
  verifier.

### 2.3 New devices
1. **The device registers** with its public keys, name, OS and app version. It is
   `pending`, and its screen shows a fingerprint: 12 digits, `1234 5678 9012`, from the SHA-256 of its two public keys (as built; six words were the first idea).
2. **You get a push** (*The boss's iPhone wants to be approved*). In the admin console you
   compare its fingerprint with the device's screen and approve or refuse.
3. **The approval is a signature by your own admin device:**
   `ECDSA(admin key, device id ‖ user ‖ caps ‖ public keys ‖ time)`. It is stored with the
   device.
4. **A `books` device then needs the data key.** The office server, on its next check
   (every minute while it runs), finds the approved device and verifies your signature
   against the admin keys it has pinned. It then wraps the data key to the device's ECDH
   key and uploads the wrapped copy (section 4).
5. **Revoking** a device or a person: their refresh tokens die at once, and the next
   roster tells the office server, which ends their office sessions within a minute
   (2.4). For `books`, the office server also makes a new data key, wraps it for the
   devices that remain, and uses it from the next sync on.

**Your admin devices are pinned on the office server** at its setup. The office server
shows its fingerprint, your phone shows its own, and you compare both ways. More admin
devices are added by a signature from one already pinned. The recovery sheet (section 4)
can pin a new one if every admin device is lost.

### 2.4 Signing in to the office server
- A client PC signs in with the control plane (2.2), then hands its access token to the
  office server *once*. The office server checks it against the control plane's public
  keys (cached) and starts its own office session: 12 hours, renewed while the person
  stays on the roster.
- **The roster** is everyone, their access ticks and their revoked devices, signed by
  the control plane. The office server fetches it every minute.
- **Internet down:** the last roster stands. People already signed in carry on, and new
  sign-ins wait. *Revoke* reaches the office within a minute while online. When offline,
  it reaches it as soon as the internet is back; or you can end a session on the office
  server itself.

---

## 3. The office server and its clients

### 3.1 Modes
- `launch.py` gets a mode, chosen in set-up and saved in the app's settings:

  | Mode | What it runs |
  |---|---|
  | `server` | Today's app, plus the sign-in check, served on the office network |
  | `client` | No server, AI or OCR: the app's window onto `https://<office server>:8770`, plus sign-in |
  | `alone` | Today's app, unchanged, for this PC only |

- The updater updates every mode, as now.

### 3.2 On the office network (spike N1)
- **In server mode it binds to `0.0.0.0:8770` over HTTPS.** At set-up the office server
  makes its own certificate for its name and its office IP addresses, using
  `cryptography`, which can already do X.509.
- **Clients pin that certificate's fingerprint when pairing.**
  1. On the client, type the office server's name or IP.
  2. Compare the fingerprint on both screens.
  3. The client keeps it.
- **The window:** WebView2's `ServerCertificateErrorDetected` event lets the app accept
  exactly that pinned certificate and refuse any other. Nothing is added to Windows'
  trusted roots. `launch.py` already reaches into pywebview's WebView2 internals, so this
  is one more hook there. **Spike N1** checks it works through pywebview 6.2.
  - **N1 passed, 21 Oct.** The test machine had the WebView2 SDK 1.0.3856 that pywebview
    6.2 bundles, and the WebView2 runtime 155. The test used two self-signed certificates for
    the same name, `localhost`: one pinned, one rogue.
    - Subscribing to `CoreWebView2.ServerCertificateErrorDetected` in `on_webview_ready`
      (the hook `launch.py` already patches) and comparing the SHA-256 of the certificate's
      DER let the window load the pinned server's page and refuse the rogue one.
    - The rogue one was refused **again after the pinned one had been allowed on the same
      host name.** So WebView2 remembers an *allow* per host *and* certificate, as its docs
      say, and a swapped certificate is asked about again.
    - A refused load leaves the window on the page it was showing, so the client shows
      its own *Wrong server certificate* page instead. Nothing was added to Windows' trusted
      roots, and the fallback isn't needed.
    - The event fires only for certificates Windows doesn't already trust. So clients reach
      the office server by its office IP or a name like `office-pc.lan`, which no public
      authority will certify for anyone.
  - *Fallback:* add the office server's own CA to the current user's trusted roots
    (`certutil -user -addstore Root`), which Windows asks the user to confirm.
- **Windows Firewall:** set-up adds a rule allowing 8770 on *Private* networks only.

### 3.3 The sign-in check: `app/office/gate.py`
- Middleware in `app/main.py`, before anything else, checks every request: a valid
  office session, and the right access tick for the path:

  | Path | Needs |
  |---|---|
  | `/` (choice of module) | Signed in; shows only the modules your ticks allow |
  | `/bridge/…` | `tib` |
  | `/console/…`, `/api/…` | `console` |
  | `/keeper/…` (the Keeper's status and set-up screen) | `admin` |
  | `/office/sign-in…` | Nothing (that's where you sign in) |

- **It applies to requests from the server PC itself too,** so whoever sits at it signs
  in as themselves.
- **The Bridge's phone upload** keeps its own one-time codes on its own port. Making a code
  needs `tib`, which this check now covers.
- **Who did what:** the signed-in person goes onto each request's state. The Bridge's posting
  log and the Console's runs can record it later. That is a small change in each
  module, and is optional.
- `alone` mode skips the check. It is today's app, unchanged.

---

## 4. Keys

Section 5 of PLAN-keeper.md stands, with one change: **the passphrase is gone. Approved
devices are the keys.**

- **The data key (DEK):** 32 random bytes, made by the office server. It is DPAPI-held
  there and never leaves the office server except wrapped.
- **Wrapped for each device:**
  - ECDH between a fresh P-256 key and the device's ECDH key
  - then HKDF-SHA256 with info `keeper-dek:<kid>:<device id>`
  - then AES-256-GCM over the DEK
  - The control plane stores `(device, kid, ephemeral public key, nonce, ciphertext)`.
- **In a browser,** WebCrypto `deriveKey` and `unwrapKey` turn that into a
  **non-extractable** AES key in IndexedDB. A script on the page can use it but can't
  read it out.
- **Key ids** (`kid`) are in every `.ukeep` header. After a revocation the office server
  makes `k2`, wraps it for the remaining devices, and re-encrypts `index` and each
  company's `current`. Older dated copies stay on `k1`, which is also re-wrapped for the
  remaining devices, so nobody loses the history.
- **The recovery sheet,** printed at the office server's set-up and kept in a safe:
  - the DEKs in base32, which decrypt any backup if the office server dies
  - a one-time code that pins a new admin device if all of yours are lost
  - Restoring onto a new office server starts from this sheet.

---

## 5. Snapshots and backups, now on the control plane

- **The `.ukeep` v1 files** (PLAN-keeper.md 5.2) and their layout (PLAN-keeper.md 8) move
  from Drive to **Cloudflare R2**. They are stored under
  `office/index.ukeep`, `office/current/<company id>.ukeep`, `office/fy/<company id>/fy-2025-26.ukeep`,
  `office/daily/<company id>/2026-10-21.ukeep` and `office/backups/<company id>/<file>.ukeep`.
  The kind of file comes first so R2's lifecycle rules, which match by prefix, can tell them
  apart.
- **Uploads,** from the office server:
  1. `POST /api/office/blobs` with the name, size and SHA-256. It needs the office
     server's own device token.
  2. The control plane returns a pre-signed R2 `PUT` URL, valid for 15 minutes. Files
     over 50 MB are uploaded in parts.
  3. `POST /api/office/blobs/{id}/commit` records it. R2 has its own checksum check.
  - The office server's outbox, retention and checks stand as written.
- **Downloads,** for the viewer: `GET /api/blobs/{id}` checks `books` and an approved
  device, writes an audit line, and redirects to a pre-signed `GET` URL valid for
  5 minutes.
- **Retention:** the office server asks the control plane to delete. R2's own lifecycle
  rule is a second net: anything under `daily/` or `backups/` older than 400 days goes.
- **Restore** is as in PLAN-keeper.md 10, downloading from R2 instead of Drive.

---

## 6. Push notifications

- **Standard Web Push** (VAPID, RFC 8291) from the control plane, to the viewer and the
  admin console. It reaches phones (Android Chrome; iPhone with iOS 16.4+ once the web
  app is on the home screen) and laptop browsers (Chrome, Edge, Firefox, Safari).
- **Subscriptions:** `POST /api/push/subscribe` from the service worker, tied to the
  device. Revoking a device drops them.
- **What is sent, and to whom:**

  | Event | Worked out by | To | Content |
  |---|---|---|---|
  | A device waiting for approval | Control plane | Admins | Plain: the device's name and owner |
  | The office server silent for 3 hours in office hours (09:00-20:00 IST, Mon-Sat; changeable) | Control plane | Admins | Plain |
  | A sync failing, or the newest Tally backup older than 7 days | Office server, sent as status | Admins | Plain: no figures |
  | **Overdue bills:** a bill that crossed its due date since the last sync | Office server, at each sync | People with `books`, by their choice | **Encrypted** |
  | **Big transactions:** a voucher at or above the amount you set (default ₹1,00,000) that is new since the last sync | Office server, at each sync | The boss (anyone who opts in) | **Encrypted** |

- **Encrypted events stay blind.**
  1. The office server encrypts each one with the current DEK, for example *Overdue:
     Dungarpur Fabricators, ₹2,45,000, 3 bills*.
  2. It hands the control plane only ciphertext and an audience (`books`).
  3. The control plane pushes the ciphertext.
  4. On the device, the service worker decrypts it with the IndexedDB key and shows the
     notification.
  - **Spike N2** checks that a service worker on an iOS home-screen app can do that
    decrypt before showing the notification.
- **No repeats:** the office server remembers which bills and vouchers it has already
  announced, in its Keeper data. At most one *overdue* push per party per day, gathered
  into one push when there are several.
- **The threshold** for big transactions is set in the admin console. It isn't secret, so
  it lives on the control plane, and the office server reads it with the roster.

---

## 7. The admin console (`/admin`, needs `admin`)

| Page | What it shows and does |
|---|---|
| **People** | Add by email; access ticks from a preset; disable; per person: devices, last seen, what they last did |
| **Devices** | Every install, viewer and office client: owner, kind, name, OS, app version, first and last seen, last IP. *Approve* (with the fingerprint check), *Revoke*, *Rename*. The office server's line shows Tally reachable or not, the last sync, the backup age and the outbox |
| **Audit** | Every sign-in, refused sign-in, device approval and revocation, access change, snapshot download, restore and sync, with who, which device, when and from which IP. Kept 2 years |
| **Settings** | Office hours, the big-transaction amount, who gets which pushes, retention |

The admin console is the same web app as the viewer, on the same origin, so a phone that
is an admin device can approve devices from anywhere.

---

## 8. The control plane: stack and data

| Part | Choice | Why |
|---|---|---|
| API and pages | Python 3.14, FastAPI, pydantic v2: the same as the app | One language; `.ukeep` types shared with `app/keeper/schema.py` |
| Host | **Vercel**, Python (FastAPI) functions, region `bom1` (Mumbai), next to the database. At `<project>.vercel.app` or a domain of your own (section 12) | HTTPS included. Request bodies are capped at 4.5 MB, which is fine: files go straight to R2 through pre-signed URLs, never through a function. A cold start is a second or two |
| Database | **Supabase** Postgres (free tier), a project of its own, region South Asia (Mumbai, `ap-south-1`). SQLAlchemy 2.1 (already pinned) with `psycopg` through Supabase's transaction pooler (port 6543), `NullPool`, and prepared statements off, which that pooler can't keep | Small; `pg_cron` and `pg_net` built in (timed checks, below) |
| Locking down Supabase | Our tables live in a schema of their own, `office`, which is **not** in the Data API's exposed schemas, and they have row-level security on with no policies. Supabase's REST API and its `anon` and `service_role` keys reach none of it; only the control plane's own database role does. Supabase Auth and Storage aren't used | Supabase exposes `public` over REST by default, and nothing of ours may be reachable that way |
| Ciphertext store | Cloudflare R2 (10 GB free, no download fees), with pre-signed URLs | Backups are the big files |
| Web Push | `pywebpush` (one new dependency, on the control plane only) | VAPID and RFC 8291 done right |
| JWT | `cryptography` Ed25519 and a few lines of encoding; or `PyJWT` | Few claims; no framework |
| Domain | One small domain or subdomain, for example `office.<yours>.in` | OAuth redirects, Web Push and cookies want a stable origin |
| Deploy | Vercel's Git integration on your copy of this repository, root `control-plane/`. GitHub Actions runs the tests on every push; production deploys only from `main` | Every change reviewed and versioned |
| Secrets | Vercel environment variables, marked *Sensitive*, production only: Google client ID and secret, Ed25519 signing key, VAPID key, R2 keys, the database URL | Never in the repo, never in chat |
| Timed checks | **`pg_cron` in Supabase**, every 5 minutes. It runs `net.http_post` (`pg_net`) to `POST /api/cron/watch`, with a secret kept in Supabase Vault, and the control plane sends the *office server silent* alert. The check runs in Python, beside the rest of the push code, and the database only keeps time | Vercel's Hobby plan allows only daily cron jobs, up to 59 minutes late. No GitHub Actions timer needed. A free Supabase project pauses after a week without use; the office server's every-minute calls keep it awake |

**Tables** (Supabase, schema `office`):
```
users(id, email unique, name, caps text[], status active|disabled, created_at, created_by)
devices(id uuid, user_id, kind office_server|office_client|viewer, name, os, app_version,
        sign_pub, wrap_pub, fingerprint, status pending|approved|revoked,
        approval_sig, approved_by_device, approved_at, revoked_at,
        first_seen, last_seen, last_ip)
refresh_tokens(id, device_id, token_hash, created_at, expires_at, replaced_by, revoked_at)
admin_pins(device_id, pinned_at)                      -- mirrors what the office server pinned
wrapped_keys(device_id, kid, epk, nonce, ciphertext, created_at)
blobs(id, path, kid, bytes, sha256, taken_at, uploaded_at, deleted_at)
push_subscriptions(id, device_id, endpoint, p256dh, auth, created_at)
events(id, audience, ciphertext | text, created_at, sent_at)
office_status(at, tally_reachable, last_sync, backup_age_days, outbox, app_version)
settings(key, value jsonb)
audit(id, at, user_id, device_id, action, detail jsonb, ip)
```

**The API** (`/api/…`):

| Area | Endpoints |
|---|---|
| Sign-in | `GET auth/google/start`, `GET auth/google/callback`, `POST auth/token` (PC, PKCE), `POST auth/refresh` (signed by the device), `POST auth/sign-out` |
| Devices | `POST devices` (register), `GET devices/me`, `POST devices/me/heartbeat` |
| Admin | `GET/POST/PATCH admin/users`, `GET admin/devices`, `POST admin/devices/{id}/approve` `{signature}`, `POST admin/devices/{id}/revoke`, `GET admin/audit`, `GET/PUT admin/settings` |
| Keys | `GET keys/me` (my wrapped keys); office server: `GET office/devices-needing-keys`, `PUT office/wrapped-keys/{device}` |
| Books | `GET blobs/index`, `GET blobs/{id}`; office server: `POST office/blobs`, `POST office/blobs/{id}/commit`, `DELETE office/blobs/{id}` |
| Office | `GET office/roster` (signed), `POST office/status`, `POST office/events` |
| Push | `GET push/vapid-key`, `POST push/subscribe`, `DELETE push/subscribe` |

- **Rate limits** on `auth/*` and `devices`.
- **Pages:** CSP with `default-src 'self'`, plus the HSTS, frame and referrer headers.
- **Every admin action** goes in the audit log.

---

## 9. What "blind" does and doesn't cover

| Someone gets | Can they read the books? |
|---|---|
| A copy of the database, or of the R2 bucket | No: only ciphertext, and keys wrapped to devices they don't have |
| Write access to the database (inserts a device, marks it approved) | No: the office server only wraps the key for devices whose approval carries a pinned admin's signature |
| A stolen refresh token | No: each refresh must be signed by the device's own key |
| A phone of the boss's, unlocked | Yes, what it shows. *Revoke* stops anything newer reaching it |
| **Control of the deploy itself** (your GitHub or Vercel) | **Yes, in the end.** They could ship a viewer that sends home what it decrypts. That is true of every web app that decrypts in the browser. The guard is your own accounts: two-factor on Google, GitHub, Vercel and Cloudflare, and deploys only through reviewed GitHub Actions |
| The office server | Yes. It is where the books are; that is Tally's own position today |

---

## 10. What this changes in code already planned

| Plan | Change |
|---|---|
| PLAN-keeper Phase 1 (kept apart; done) | None. `app/office/` joins the rule: no imports of `app.bridge` or `app.backend`, and `test_kept_apart.py` checks it |
| PLAN-keeper Phase 2 (keys, format) | `.ukeep` v1 stands. The passphrase and `keeper.json` go; device wrapping (section 4) comes in their place |
| PLAN-keeper Phase 3 (reading Tally) | None |
| PLAN-keeper Phase 4 (Drive) | **Gone.** It becomes Phases B, C and E below |
| PLAN-keeper Phase 5 (sync engine) | Uploads through section 5; events at each sync (section 6) |
| PLAN-keeper Phase 7 (viewer) | Served by the control plane; sign-in and keys as in sections 2 and 4; push |
| Spikes S1 and S2 | **Gone:** no Drive scopes, and the redirect flow works in a home-screen app |

**The new code:**
```
app/office/                 (in the desktop app, all modes)
  identity.py     device keys (DPAPI), fingerprints
  signin.py       loopback + PKCE to the control plane; tokens
  roster.py       fetch, verify, cache the signed roster
  gate.py         the sign-in check (3.3); office sessions (2.4)
  pairing.py      office server certificate; pinning on clients
  keys.py         wrap the DEK for approved devices; verify admin signatures
  tests/
control-plane/              (hosted; its own requirements.txt)
  app/main.py, auth.py, devices.py, admin.py, keys.py, blobs.py, push.py, roster.py, audit.py
  app/web/        viewer + admin console (static, served by the API)
  migrations/     SQL, applied by the deploy
  tests/
  Dockerfile
```

---

## 11. Phases

| Phase | What | Done when |
|---|---|---|
| **A. Spikes** | N1 WebView2 accepting a pinned certificate through pywebview (**passed**, section 3.2); N2 iOS home-screen web app: push, then decrypting in the service worker; N3 Vercel (bom1) + Supabase cold start and query time (**done**, below); S3 and S5 from PLAN-keeper (needs Tally open) | Each answered here, with its fallback chosen if needed |
| **B. Control plane core** | Cloud project (sign-in only), Vercel project, Supabase, R2; `auth`, `devices`, `admin` (people, devices, audit); deploy pipeline | You sign in on a phone, see that phone pending, approve it from a second device; **done 21 Oct**: signed in on a laptop and a phone, and approved the phone from the laptop. 51 checks in `control-plane/tests/test_api.py` |
| **C. Office server mode** | Modes, pairing and certificate pinning, `gate.py`, roster, office sessions, firewall rule | The accountant's PC signs in and sees only the Bridge and the Console; the manager's only the Console; revoking one ends their session within a minute; **built and tested 21 Oct**, in 1.1.0: needs setting up on the office PCs (below) |
| **D. Keys** (PLAN-keeper Phase 2, with section 4) | `.ukeep`, device wrapping, admin signatures, recovery sheet; the Python ↔ WebCrypto check | A tampered approval is refused; a revoked device gets no new key; **done 21 Oct** (`app/office/keys.py`, `app/keeper/ukeep.py`, `keeper-crypto.js`) |
| **E. Keeper on the control plane** (PLAN-keeper Phases 3 and 5) | Reading Tally, snapshots, sync, outbox, R2 uploads, retention | A year off the fake Tally lands in R2 as ciphertext; zero non-Export requests; **done 21 Oct**, live since 1.2.3. Real Tally needs periods as `TYPE="Date"` (without, it crashed) and sends undeclared `UDF:` prefixes; the sync runs on its own only after a clean Sync now |
| **F. Viewer** (PLAN-keeper's screens) | The PWA, its screens and offline use | The boss's phone and laptop show the books; the purchase team the same; **built 21 Oct** (`/books`). Not built: a service worker (the page needs the internet to load; the data opens offline) |
| **G. Push** | VAPID, subscriptions, admin alerts, encrypted overdue and big-transaction events, no repeats | Each row of the section 6 table arrives once, on Android, iPhone and a laptop; **built 22 Oct** (`cp/push.py`, `app/keeper/notices.py`, `public/sw.js`), push made with `cryptography` (no new package), the 3-hour silence checked by Supabase pg_cron every 20 minutes; checked in tests (a browser's decryption, the VAPID signature, the service worker opening a sealed notice), not yet on a real phone (spike N2) |
| **H. Backup and restore** (PLAN-keeper Phase 8) | As written, through R2 | A Tally backup round-trips and restores into a scratch data folder; **built 22 Oct** (`app/keeper/backups.py`), round trip tested with a 3 MB stand-in backup; needs the backup folder set on the Keeper page |
| **I. Docs and release** | `docs/office.md`, README (the "nothing leaves" line becomes accurate about what does), the version, the release | Live; **done 22 Oct** with 1.2.5 |

**Phase B as built (21 Oct).** Two changes from sections 2.2 and 2.3:
- **Browser sessions are rows, not JWTs.** A random cookie (HttpOnly, Secure, SameSite=Lax),
  only its SHA-256 stored, 30 days sliding. The control plane serves its own pages, so the
  same origin needs nothing more. The device proves its key once per session, by signing
  the session's challenge.
  - The signed Ed25519 access tokens of 2.2 come in Phase C, where the office server must
    check a person without asking the control plane each time.
  - Changes need an `X-UA-Request` header, which no cross-site page can send.
- **The first admin device approves itself,** once, while no approved admin device exists
  (`device.approved-first-admin` in the audit log). Every later device needs a signature
  from an approved admin device, checked on the server against exactly what is approved:
  the device, its owner's email and access, and its keys.
  - The pinning of admin keys on the office server (2.3) comes with Phase C.

Not done yet: rate limits on sign-in. Google's own sign-in screen stands in front of it for
now.

**N3, measured 21 Oct.** A temporary function connected to Supabase and ran `SELECT 1`
three times. Calls were from an office PC on broadband, not a phone on mobile data, which
will add its own delay.
- **Where it runs:** every call answered `bom1::bom1`, so both Vercel's edge and the
  function were in Mumbai.
- **Warm:** about 85-550 ms per call in total, median about 140 ms. Connecting to the
  Supabase pooler took 17-20 ms, and each `SELECT 1` took 1-3 ms.
- **Cold, after 15 minutes idle:** 1.08 s in total, with the connection at 86 ms and the
  first query at 13 ms. The next call was warm again, at 129 ms.
- **What it means:** a page needing a handful of queries is answered in well under a
  quarter of a second once warm, and the first tap after a quiet spell costs about one
  second. Nothing needs keeping warm. The function was removed after measuring.

- B and D can run side by side. C needs B.
- E needs B and D. F needs B and D. G needs E and F.
- Phase A's N1 decides C's shape, so it comes first.

---

### Phases C and D as built (21 October 2026)

**Tests.**
- `app/office/tests/test_office.py`, 63 checks: DPAPI, tokens, the gate, trust and
  rotation of keys, `.ukeep` tampering, and `keeper-crypto.js` under Node.
- `control-plane/tests/test_api.py`, 67 checks, including the real `app/office` code
  linking, approving, getting tokens, fetching the roster and handing out keys over HTTP.
- The real `app/main.py` in server mode: signed out, only `/api/health` answers.
  Signed in as a Console-only manager: home, Console and its API open; the Bridge and shutdown
  are refused.
- `launch._pin_certificate` in a real WebView2 window: the pinned server loads, a rogue
  one for the same host doesn't.
- Every earlier self-check passes.

**How it differs from sections 2 and 3:**
- **An office PC links through its person's browser**, at `/link`. The PC's keys and
  signature travel in the address's `#fragment`; the person confirms the 12 digits; the
  browser comes back to the PC's loopback address.
  - There's no refresh token. The PC signs a fresh time for each 15-minute token, and the
    control plane refuses a time it has already seen.
- **The office server's own window signs in as the person at that PC,** not as the
  server. The server has two identities: `server.json`, linked by an admin and used for the
  roster and keys, and `client.json`, linked by whoever uses that PC.
- **Admin pinning:** at set-up, the office server shows which device approved it and that
  device's 12 digits (shown on its first page). You confirm them, and its key is pinned.
  - From then on, a device gets the data key only through a chain of admin signatures
    starting at a pinned key. The control plane's word alone isn't enough.
- **The firewall rule is one command,** shown at the end of set-up, for you to run as
  administrator. The app doesn't change Windows' security settings itself.
- **Client PCs don't check for updates themselves yet** (`ponytail:` in `run_client`). Run
  one in `alone` mode now and then, or add the check if they drift behind.

**Setting up the office PCs, once 1.1.0 is installed:**
1. **On the office server PC** (the one with Tally), in a command prompt:
   `"%LOCALAPPDATA%\Programs\Ultimate Accountant\Ultimate Accountant.exe" --office server`
   Follow the dialogs:
   1. Your browser opens: sign in as admin and choose **Link this PC**.
   2. Approve it in the admin console, from a device whose 12 digits you can see.
   3. Confirm that device's digits.
   4. Say who uses that PC, and link and approve them too.
   5. Print the recovery sheet.
   6. Run the firewall command it shows, as administrator.
   7. Close and reopen the app.
2. **On each office PC:** `... --office client <office server's name or IP>`. Compare the
   12 digits with the office server's (`--office status` there). The person signs in and
   links, and you approve.
3. **Back to normal on any PC:** `--office alone`.

## 12. Before Phase A: what your deployment needs

| | What to set up |
|---|---|
| **Secrets** | The control plane's secrets live in `%USERPROFILE%\.ultimate-accountant\office.env` for local runs, outside the repo, and as the host's environment variables in production. `scripts/office_secrets.py` reports which are filled in, never their values, and makes the generated keys. The desktop app's own settings (`UA_CONTROL_PLANE` and the rest) go in `deploy.env` or the environment: see [docs/configuration.md](docs/configuration.md) |
| **Domain** | The control plane's address: `PUBLIC_ORIGIN` on the control plane, `UA_CONTROL_PLANE` in the app (for example `https://your-control-plane.example`). **Settle it before anyone but you is set up.** A web app's push subscriptions, its service worker and the device keys in IndexedDB all belong to its origin. Moving after the boss and the purchase team are on means every phone and browser signs in again, registers as a new device and is approved again. The PCs' sign-in only changes its address |
| **Google Cloud project** | Sign-in only, no billing. External audience, *In production*. A *Web application* client with two redirects (`<PUBLIC_ORIGIN>/…` and `http://localhost:8000/…` for local runs); its ID and secret go in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` |
| **Cloudflare R2** | A bucket that is not public, its name in `R2_BUCKET`. A token for objects only, in that bucket only (it can't read bucket settings, by design). CORS: GET and HEAD from the control plane's address. Lifecycle: `office/daily/` and `office/backups/` after 400 days, plus Cloudflare's 7-day abort for unfinished uploads |
| **Supabase** | Postgres 17 in a region near the office (Mumbai, `ap-south-1`, beside Vercel's `bom1`), transaction pooler, `pg_cron` and `pg_net` on; its pooler URL in `DATABASE_URL` |
| **Vercel** | A project linked to your GitHub repository with Root Directory `control-plane`; the CLI through `scripts/vercel.ps1`. Hobby is for non-commercial use: a commercial deployment needs Pro |
| **The office server** | Which PC, and a fixed IP for it from the router |

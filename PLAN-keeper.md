# The Keeper: plan for the whole pipeline

Prepared 21 October 2026. Replaces the outline written earlier the same day.

> **Changed later on 21 Oct: Google Drive is out. See [PLAN-office.md](PLAN-office.md).**
> The office now runs on one office server next to Tally, with a small hosted control
> plane for sign-in, people, devices, push and the encrypted snapshots (on R2). These
> parts of this plan no longer apply, and PLAN-office.md has their replacements:
> - §4 (signing in to Drive), and the passphrase and `keeper.json` in §5.1 (approved
>   devices are the keys now)
> - §8 (the layout in Drive)
> - §12's hosting and opening steps
> - §13's Drive rows
> - Phases 4 and 7 in §16
> - spikes S1 and S2
>
> Still standing: the rules keeping the Keeper and the Bridge apart (§2), `.ukeep` v1 (§5.2),
> reading Tally (§6), the types (§7), the sync engine (§9), backup and restore (§10) and
> the viewer's screens (§12).

The Keeper reads Tally on this PC, encrypts what it read, and puts it in the user's own
Google Drive or OneDrive. A viewer page opens it on a phone or any browser. **It is
read-only all the way through:** nothing in the Keeper can write to Tally.

---

## 0. The short version

| | |
|---|---|
| **What it does** | It gives the books on mobile and the web, syncs every company open in Tally automatically, shows outstanding payments with ageing, and keeps backups you can restore |
| **What it leaves out** | Reports beyond the screens in section 12, sharing on WhatsApp, payment reminders, and creating or editing vouchers |
| **Where it runs** | *Keeper sync* is a third module in Ultimate Accountant (`app/keeper/`), plus a sync-only mode the Windows Task Scheduler runs. *Keeper viewer* is a static web page (`keeper-viewer/`) on GitHub Pages |
| **Where the data goes** | The user's own Drive. Google: a folder the app creates, with the `drive.file` scope. OneDrive: the app folder, `Apps/Ultimate Accountant Keeper`, with `Files.ReadWrite.AppFolder` |
| **Who can read it** | Someone needs both the Drive sign-in and the Keeper passphrase. Encryption happens on the PC and decryption on the device. Google and Microsoft only ever hold ciphertext |
| **New dependencies** | None on the PC: `httpx`, `cryptography` and `pydantic` are already pinned. In the viewer, one vendored file (MSAL for OneDrive) and Google's sign-in script, which Google requires be loaded from its own site |
| **Kept apart from the Bridge** | It has its own Tally client that can only send *Export* requests, and it never imports `app.bridge`. The one thing shared is a named lock, so the two never talk over each other to Tally. Checked by tests (section 2) |

**Changed from the outline: the PC now uploads through the Drive's API instead of
writing into the folder the desktop Drive app syncs.**

The reason is Google. A viewer can read files from a synced folder only with
`drive.readonly`. Google lists that as a *restricted* scope, which needs a paid security
assessment before anyone outside a test list can sign in. With `drive.file`, a
*non-sensitive* scope, the viewer can read only the files our own app created, and the PC
can create them only by uploading them itself. OneDrive is done the same way so both
paths match.

This also means the user doesn't need the Google Drive or OneDrive desktop app at all.
The files still land in the user's own Drive, with its version history and trash.

---

## 1. Scope

**In:**
1. **Data on mobile and the web.** The screens are in section 12.
2. **Automatic sync of every company open in Tally.** It runs every 2 hours, including
   while the Ultimate Accountant window is closed.
3. **Outstanding payments with ageing:** receivables and payables, by party and then by
   bill, aged 0-30, 31-60, 61-90 and 90+ days.
4. **Backup and restore:** dated encrypted snapshots, plus Tally's own backup file. The
   restore hands that file back to Tally's *Restore*.

**Out:** the 35+ reports, WhatsApp, reminders, voucher entry, more than one user's keys,
and syncing companies that aren't open in Tally. (Tally's HTTP server only serves the
companies it has loaded.)

---

## 2. Keeper and the Bridge kept apart

The Bridge writes to Tally. The Keeper must never be able to, even through a bug. These are the
rules, each with the test that holds it:

| # | Rule | How it is enforced |
|---|---|---|
| G1 | No Keeper runtime code imports `app.bridge` or `app.backend` | `test_isolation.py` walks the AST of every `.py` file under `app/keeper/` except `tests/`, and fails on any import of `app.bridge*` or `app.backend*`. The only `app.*` it may import is `app.tally_line` |
| G2 | The Keeper's Tally client can only *read* | `app/keeper/tally_read.py` builds requests only from its own fixed templates. Its single `_send()` parses each payload before it goes and **refuses** anything whose `HEADER/TALLYREQUEST` isn't `Export`, whose `TYPE` isn't `Collection` or `Object`, or which contains `IMPORTDATA`, `<ACTION`, `<ADD ` or a TDL `FUNCTION`. Only the company name and dates are put into the templates, escaped. Tests send an Import and a TDL function through `_send()` and expect `ReadOnlyViolation` |
| G3 | Under every sync test, the fake Tally records every request it gets | Every Keeper test that syncs checks that the fake received **zero** non-Export requests. The fake is extended to log requests (section 15) |
| G4 | The Keeper keeps its own settings, data and Tally address | Settings live in `%APPDATA%\Ultimate Accountant\keeper\settings.json`, and its Tally host and port are its own. It never opens the Bridge's database, settings or folders. G1 covers the code side |
| G5 | The only thing shared with the Bridge is a lock | `app/tally_line.py` is a Windows named mutex, `Local\UltimateAccountant.TallyLine`, with `acquire(timeout)` and `release()` and no knowledge of Tally. In the Bridge, `tally.py` changes one line: `_line = threading.Lock()` becomes `_line = tally_line.LINE`. Inside one process it is still a `threading.Lock`, taken first, so it behaves exactly as before: not even the thread holding it can take it twice. The Bridge's `test_pipeline` relies on that, and it failed when the first version used the mutex alone. The named mutex, taken second, carries the turn to other processes, and a process that dies holding it doesn't keep it. **Done 21 Oct; the Bridge's four test files pass unchanged** |
| G6 | The viewer can't reach the PC or Tally | Its CSP sets `connect-src` to only `googleapis.com`, `graph.microsoft.com`, `login.microsoftonline.com`, `accounts.google.com` and `oauth2.googleapis.com`. It has no address for the PC to use |
| G7 | Restore never writes into Tally or Tally's data folder | It decrypts a backup into a folder the user picks, and that folder is refused if it is Tally's data path. The user runs Tally's own *Data > Restore*. There is no snapshot-to-Tally-XML path at all, because it would be a write path |
| G8 | The viewer itself never writes to the Drive | `drive.js` has one `get()` helper and no other HTTP methods. A test greps the viewer for `method:` and for `PUT`, `POST`, `PATCH` and `DELETE`. The allowed exceptions are the token exchange and listing calls, kept in a list |
| G9 | In the UI it is a separate module | It has its own card on `home.html`, its own colours and its own screen at `/keeper/`, with no links into the Bridge's posting screens |

**Why the lock matters:** Tally answers one connection at a time and drops a second one.
Without the shared lock, a Keeper read could arrive while the Bridge is posting a bill. The Bridge's
connection would then be dropped, and the Bridge would tell the user "check Day Book, it may
have taken the voucher" when it hadn't. With the lock they queue instead. The Keeper
asks for vouchers one month at a time, so the Bridge never waits behind it for long.

---

## 3. The pipeline, end to end

```
 PC (Windows user who set it up)                                   User's Drive                Phone / any browser
 ─────────────────────────────────────────────────────────────    ─────────────────           ──────────────────────
 Tally :9000 ──Export only──► tally_read.py
   (Local\…TallyLine mutex)        │ masters, monthly vouchers,
                                   │ bills, alter-ids
                                   ▼
                              snapshot.py  ── pydantic Snapshot, checks (Dr = Cr, counts)
                                   │ JSON (UTF-8) → gzip
                                   ▼
                              crypto.py  ── AES-256-GCM, chunked; DEK from DPAPI
                                   │ .ukeep file
                                   ▼
                              outbox/  (kept until the upload is confirmed)
                                   │
                              drive.py ──HTTPS, OAuth token (DPAPI)──► Keeper folder ──GET──► drive.js (signed-in token)
                                                                         keeper.json         │
                                                                         index.ukeep         ▼
                                                                         c-xxxx/*.ukeep    IndexedDB: encrypted copy
                                                                                             │ DEK (non-extractable
                                                                                             │ CryptoKey) from IndexedDB,
                                                                                             │ unwrapped once with passphrase
                                                                                             ▼
                                                                                          decrypt → gunzip → JSON
                                                                                             ▼
                                                                                          screens (in memory only)
```

---

## 4. Who is who, and how each side signs in

There are no Keeper accounts. Three things identify the user:

| What | Where it lives | What it gates |
|---|---|---|
| The Windows user on the PC | Windows | The DPAPI-held DEK and Drive token: syncing, *Sync now*, restore |
| The Google or Microsoft account | Google or Microsoft | Where the files are; who can download the ciphertext |
| The Keeper passphrase | The user's head and the recovery sheet | Turning the ciphertext into books, on a new device |

### 4.1 The PC: Google
- **OAuth client:** type *Desktop app*, in a Cloud project the user owns ("Ultimate
  Accountant Keeper"). Google doesn't treat a desktop client's secret as secret, so it is
  shipped in the build, as its docs allow.
- **Flow:** authorization code with PKCE (S256), redirecting to the loopback address
  `http://127.0.0.1:<random port>/`. It opens in the **system browser**, because Google
  blocks sign-in inside embedded webviews such as pywebview. A one-shot stdlib
  `http.server` catches the redirect, and the `state` value is checked.
- **Scope:** `https://www.googleapis.com/auth/drive.file` only.
- **Consent screen:** must be set to *In production*. In *Testing*, refresh tokens expire
  after 7 days. With only a non-sensitive scope, *In production* needs no verification.
- **Tokens:** the refresh token is kept DPAPI-encrypted in
  `keeper\google.token.dpapi`. Access tokens are refreshed as needed and kept only in
  memory.

### 4.2 The PC: Microsoft
- **App registration:** in Entra ("Ultimate Accountant Keeper"), for *personal Microsoft
  accounts and work or school accounts*. Platform *Mobile and desktop*, redirect
  `http://localhost`. It is a public client: no secret.
- **Flow:** authorization code with PKCE in the system browser, loopback, through
  `https://login.microsoftonline.com/common/oauth2/v2.0/`.
- **Scopes:** `Files.ReadWrite.AppFolder offline_access`. Graph creates the folder on the
  first `GET /me/drive/special/approot`. The code always uses `approot`, never a path,
  because the name of the `Apps` folder changes with the user's language.
- A work or school tenant may need an admin to consent; the setup screen says so if
  Microsoft returns `AADSTS65001`.
- **Tokens:** the refresh token is DPAPI-encrypted in `keeper\microsoft.token.dpapi`.

### 4.3 The viewer: Google
- **OAuth client:** type *Web application*, **in the same Cloud project**, with JavaScript
  origin `https://your-github-user.github.io`.
- **Flow:** the Google Identity Services *token client*
  (`google.accounts.oauth2.initTokenClient`), scope `drive.file`. It gives a 1-hour
  access token with no refresh token, as Google gives every browser app. On later opens
  it asks again with `prompt: ''`, which is silent or a single tap while the user is
  signed in to Google.
- **Spike S1:** that the web client can see files the desktop client created. `drive.file`
  access is believed to be per *project*, not per client, but Google's pages don't say so
  outright. If it doesn't hold, the fallbacks in order are `drive.appdata` (shared by the
  project, but hidden from the user in Drive) and then a one-time Google Picker of the
  Keeper folder.
- **Spike S2:** that the sign-in pop-up works in an iOS home-screen web app. If it
  doesn't, use the OAuth redirect flow (`response_type=token`) on that platform.

### 4.4 The viewer: Microsoft
- **Same app registration,** adding the *Single-page application* platform with redirect
  `https://your-github-user.github.io/ultimate-accountant-keeper/`. The same app
  means the same `approot` folder.
- **MSAL.js** (`@azure/msal-browser`, pinned, vendored into `vendor/`), authorization
  code with PKCE, redirect flow (better in PWAs than pop-ups). Scope
  `Files.ReadWrite.AppFolder`. There is no read-only app-folder scope, so G8 keeps the
  viewer's code to reading.
- **The refresh token** sits in MSAL's localStorage cache. For SPAs it lasts 24 hours and
  rolls, so a phone used daily opens silently. After a gap, one tap.

### 4.5 Signing out and lock-outs
- **PC, *Disconnect*:** revokes the refresh token (Google's `/revoke`; for Microsoft it is
  simply deleted, since public clients have no revoke call) and deletes the DPAPI files.
  The Drive files stay.
- **Viewer, *Forget this device*:** deletes the IndexedDB key, the cached files and the
  tokens.
- **PC, *Lock out every device*:** makes a new DEK (`k2`) and re-encrypts `index` and every
  `current` file. Phones then need the passphrase again, which re-unwraps.
  Older dated copies stay on `k1`, still wrapped by the passphrase. A stolen phone keeps
  whatever it had already downloaded; nothing can reach into it.

---

## 5. Keys and the file format

### 5.1 Keys
- **The data key (DEK):** 32 random bytes made at setup. Every `.ukeep` file is encrypted
  with it.
- **The passphrase key (KEK):** `PBKDF2-HMAC-SHA256(passphrase, salt, 600 000)`, giving 32
  bytes. Both `cryptography` and WebCrypto have it. On a mid-range phone it takes about
  a second, once per device.
- **`keeper.json`,** in the Drive folder, in plain text, holds no secrets:
  ```json
  { "format": 1, "app": "ultimate-accountant-keeper",
    "kdf": { "name": "PBKDF2-SHA256", "iterations": 600000, "salt": "<b64 16>" },
    "keys": [ { "kid": "k1", "created": "2026-10-21T10:00:00+05:30",
                "wrapped": "<b64: nonce 12 | AES-GCM(KEK, DEK, aad='keeper-dek:k1')>" } ],
    "current": "k1" }
  ```
- **Changing the passphrase** re-wraps the DEKs and rewrites `keeper.json`. No data file
  changes, and every old copy opens with the new passphrase.
- **On the PC,** the DEKs are held with DPAPI (`CryptProtectData`, current user, through
  `ctypes`: about 30 lines). The passphrase itself is never stored.
- **Passphrase rules:** at least 12 characters, typed twice, with a plain warning that a
  lost passphrase can't be recovered by us.
- **The recovery sheet,** printable from the PC, shows the passphrase hint (written by the
  user) and a **recovery key**: the DEK in base32, in groups of four. With the recovery
  key alone the viewer can open the books, through *Use a recovery key*.
- **Forgotten passphrase with the PC still working:** set a new one from the PC. The PC
  holds the DEK, so it can re-wrap without the old passphrase. Anyone at that PC can
  already open Tally, so this gives nothing away.

### 5.2 `.ukeep` v1, used for snapshots and Tally backups alike
```
"UKEEP1\n"                       7 bytes, magic
u32 BE  header length
header  UTF-8 JSON: { "v":1, "kid":"k1", "kind":"snapshot"|"index"|"tally-backup",
                      "company":"c-3f9a1b2c"|null, "taken_at":"…", "chunk":1048576,
                      "np":"<b64 8: nonce prefix, random per file>", "name":"<backup file name>"? }
chunks  repeated: AES-256-GCM(DEK, nonce = np ‖ u32 BE i, plaintext chunk i, aad = A_i) ‖ 16-byte tag
        A_i = sha256(header bytes) ‖ u32 BE i ‖ u8 (1 if last chunk else 0)
```
- The plaintext is `gzip(json)` for snapshots and the index, and the raw file for Tally
  backups.
- Chunks are 1 MiB, so a 500 MB backup goes through in constant memory, and the viewer
  decrypts snapshots chunk by chunk too.
- **Tamper-proofing:**
  - The header is bound into every chunk, so it can't be edited.
  - The chunk number stops chunks being reordered.
  - The last-chunk flag stops a file being cut short without notice.
  - A random nonce prefix per file, with a counter per chunk, means a nonce is never
    reused.
- **What the Drive provider can see:** file names, sizes, times, `kind`, the opaque
  company id, and `taken_at`. Company names, figures and parties are only ever inside
  the ciphertext.

---

## 6. Reading Tally: `app/keeper/tally_read.py`

- **Transport:** `httpx` POST to `http://<host>:<port>` (Keeper's own setting, default
  `localhost:9000`, with a *Test* button). The XML clean-up for Tally's `&` and control
  characters is copied in from the Bridge's `_clean`, not imported (G1): about 15 lines.
- **Timeouts:** connect 2 s, read 120 s. A dropped *read* is retried once after 1 s
  (reads are safe to repeat). Errors are reported in plain words, as the Bridge does.
- **The lock:** `tally_line.LINE` is held per request, not per sync, so the Bridge can get in
  between requests.
- **Requests,** all `TALLYREQUEST=Export`. Field names marked † get checked against a
  real TallyPrime in **spike S3**:

| Request | Collection / object | Fetch |
|---|---|---|
| Companies loaded | `Company` | `NAME, GUID, STARTINGFROM, BOOKSFROM, GSTREGISTRATIONNUMBER†, STATENAME, ALTMSTID†, ALTVCHID†` |
| Groups | `Group` | `NAME, PARENT, ISREVENUE, ISDEEMEDPOSITIVE, AFFECTSGROSSPROFIT, PRIMARYGROUP†` |
| Ledgers | `Ledger` | `NAME, PARENT, PARTYGSTIN, OPENINGBALANCE, CLOSINGBALANCE, ISBILLWISEON, BILLCREDITPERIOD†` |
| Stock items | `StockItem` | `NAME, PARENT, BASEUNITS, CLOSINGBALANCE, CLOSINGVALUE` |
| Voucher types | `VoucherType` | `NAME, PARENT` |
| Vouchers, **one month per request** | `Voucher` + `SVFROMDATE/SVTODATE` | `MASTERID, DATE, VOUCHERTYPENAME, VOUCHERNUMBER, PARTYLEDGERNAME, REFERENCE, NARRATION, ISOPTIONAL, ISCANCELLED, ALLLEDGERENTRIES.LIST (LEDGERNAME, AMOUNT, BILLALLOCATIONS.LIST: NAME, BILLTYPE, AMOUNT, BILLCREDITPERIOD), ALLINVENTORYENTRIES.LIST (STOCKITEMNAME, ACTUALQTY, RATE, AMOUNT)` |
| Pending bills | `Bills`† | `NAME, PARENT (ledger), BILLDATE†, BILLCREDITPERIOD† / due date†, OPENINGBALANCE, CLOSINGBALANCE` |

- **Signs:** Tally's XML puts debits as negative. The reader flips them once, so in the
  snapshot **debit > 0 and credit < 0**, and amounts are **integer paise**, parsed with
  `Decimal`, never `float`.
  Quantities and rates stay decimal strings, unit included (`"12.500 Kg"` becomes
  `"12.500"` plus `"Kg"`).
- **Skipping unchanged books:** if a company's `ALTMSTID` and `ALTVCHID` are the same as
  at the last good sync, nothing is pulled and the index only records the check. If S3
  shows these fields aren't there, every sync re-reads the current year.
- **Years:** the current financial year is read on every changed sync. Each earlier year
  since `BOOKSFROM` is read once into its own file, and again only on *Re-read every
  year*. A change dated in a closed year moves `ALTVCHID`; the Keeper then re-reads only
  the current year and says *Earlier years may have changed: re-read them*. (Finding
  which year changed would need a full scan.)
- **Tally's load:** months are read one after another, with 250 ms between requests. In
  every test, a full year of a large set of books goes through as 12 short requests, not
  one long one.

---

## 7. The types

On the PC these are pydantic models in `app/keeper/schema.py`, which validate on build. In
the viewer the same shapes are JSDoc `@typedef`s in `keeper-viewer/types.js`.
`schema: 1` is in every file; the viewer refuses a newer major version with *Update
the viewer*, which can't happen in practice because the viewer ships from the same repo.

```ts
type Paise   = number;   // integer; debit > 0, credit < 0
type Day     = string;   // "YYYY-MM-DD"
type Instant = string;   // ISO 8601 with offset

type Snapshot = {
  schema: 1;
  taken_at: Instant;
  app_version: string;
  company: {
    id: string;                 // "c-" + 8 hex, opaque, given at setup
    guid: string;               // Tally's company GUID
    name: string; gstin?: string; state?: string;
    books_from: Day;
    fy: { from: Day; to: Day };
  };
  period: { from: Day; to: Day };    // what this file's vouchers cover (one FY)
  alter_ids: { masters: number; vouchers: number };
  groups: Group[];
  ledgers: Ledger[];
  stock: StockItem[];
  voucher_types: { name: string; parent: string }[];
  vouchers: Voucher[];
  bills: Bill[];                     // current-FY file only
  checks: { trial_balance_diff: Paise; voucher_count: number };   // diff must be 0
};

type Group     = { name: string; parent: string | null;  // null under Primary
                   nature: "assets" | "liabilities" | "income" | "expenses";
                   affects_gross_profit: boolean };
type Ledger    = { name: string; group: string; gstin?: string;
                   opening: Paise; closing: Paise; bill_wise: boolean; credit_days?: number };
type StockItem = { name: string; group?: string; unit?: string;
                   closing_qty: string; closing_value: Paise };

type Voucher = {
  id: string;                   // Tally MASTERID, unique per company
  date: Day; type: string; base_type: string;   // base_type: the voucher type's root, e.g. "Sales"
  number?: string; party?: string; reference?: string; narration?: string;
  optional: boolean;            // shown, not counted, as Tally does
  lines: { ledger: string; amount: Paise;
           bills?: { name: string; kind: "New Ref" | "Agst Ref" | "Advance" | "On Account";
                     amount: Paise; due?: Day }[] }[];
  items?: { item: string; qty: string; unit?: string; rate?: string; amount: Paise }[];
};                              // cancelled vouchers are left out

type Bill = { ledger: string; name: string; date: Day; due?: Day;
              opening: Paise; pending: Paise };     // pending ≠ 0 only

type Index = {                  // index.ukeep, written last on every sync
  schema: 1; written_at: Instant;
  companies: {
    id: string; name: string; gstin?: string;
    last_good: Instant | null; last_try: Instant;
    status: "ok" | "unchanged" | "not_open_in_tally" | "error"; message?: string;
    files: { current: string; years: { fy: string; file: string }[];
             daily: string[]; backups: { file: string; taken_at: Instant; bytes: number }[] };
  }[];
};
```

**Before any upload, the PC checks** that:
- every voucher's lines sum to 0
- the ledger closings sum to 0 (`trial_balance_diff`)
- every ledger's group exists
- the voucher count isn't below 50% of the last good one, without a confirmation

If any check fails, nothing is uploaded and the last good copy stays. A half-read set of
books is never sent.

---

## 8. Layout in the Drive

```
Ultimate Accountant Keeper/          (Google: created by the app | OneDrive: Apps/…, approot)
  keeper.json                        key wrapping, plain (section 5.1)
  index.ukeep                        every company and its files, encrypted
  c-3f9a1b2c/
    current.ukeep                    current FY, replaced in place (new revision) each sync
    fy-2025-26.ukeep                 closed years, written once
    daily/2026-10-21.ukeep           first good sync of each day
    backups/2026-10-21-1.ukeep       Tally's own backup file, encrypted
```

- **Company folders use the opaque id, never the name.**
- **Retention,** applied after a good sync: `daily/` keeps the last 30 days plus the last
  copy of each month. `backups/` keeps the last 10 plus each month's last. Deleting
  uses Drive's trash and OneDrive's recycle bin, so a mistake can be undone for 30 days.
- **Uploads:**
  - *Google:* multipart for files under 5 MB, otherwise a resumable session.
  - *OneDrive:* a simple `PUT` under 4 MB, otherwise an upload session.
  - Chunks are 10 MiB, a multiple of both Google's 256 KiB and Graph's 320 KiB.
  - `current.ukeep` is updated in place, so the Drive keeps its revisions.
- **The outbox:** every file is written to `keeper\outbox\` first and deleted only after
  the Drive confirms the upload, with size and checksum (Google's `md5Checksum`,
  OneDrive's `quickXorHash`). With no internet, syncs keep going and the outbox empties
  later.

---

## 9. The sync engine: `app/keeper/sync.py`

**When it runs:**
- **The scheduled task,** made at setup (and removed on *Disconnect*):
  `schtasks /Create /TN "Ultimate Accountant Keeper" /SC HOURLY /MO 2 /TR "\"<exe>\"
  --keeper-sync"`. It runs only while the user is signed in to Windows, since DPAPI needs
  that, so no Windows password is stored.
- **`launch.py --keeper-sync`:** it checks before `running_copy()`, as `--serve` does. It
  runs one sync with no window and no server, under the same CPU limit, then exits.
- **In the app:** 30 seconds after start, and on *Sync now*. Both run `sync.run()` in a
  background thread.
- **One sync at a time,** across processes: the named mutex
  `Local\UltimateAccountant.KeeperSync`. A second caller that finds it held stops quietly.
  A sync is also skipped if the last one finished less than 30 minutes ago, unless it is
  *Sync now*.

**Steps:**
1. Flush the outbox.
2. Load the DEK and the Drive token. If either is missing, report *Set up again* and stop.
3. Ask Tally for the companies it has loaded. If Tally is unreachable, every company is
   marked `not_open_in_tally`, the index is rewritten, and the sync stops. This is
   normal when the PC is on and Tally isn't.
4. For each company being kept: compare the alter ids. If they changed, read the masters,
   then the vouchers month by month, then the bills. Build and check the `Snapshot`.
   Encrypt it into the outbox.
5. New Tally backup files (section 10) go into the outbox.
6. Upload the outbox: `current`, `fy-*`, `daily` (if there's none for today), then
   `backups`.
7. Write and upload `index.ukeep`, last, so the viewer never points at a file that isn't
   there yet.
8. Apply retention.
9. Write a line in `keeper\sync.log` (rotated at 1 MB) and update the status for the
   screen.

**Failures:**

| Failure | What happens |
|---|---|
| Tally closed, or the company not loaded | Status `not_open_in_tally`; the last copy stays; nothing uploaded for it |
| Tally drops or times out mid-year | That company is skipped this time, and its last good copy stays |
| A check in section 7 fails | Nothing uploaded; status `error` with which check failed |
| No internet, or the Drive gives 5xx or 429 | Back off (1, 4, 16 s; Google's and Graph's `Retry-After` is honoured); the outbox keeps the files |
| Token revoked or expired (`invalid_grant`) | Status *Sign in to Google/OneDrive again*, shown on the Keeper card and screen |
| Drive full | Status *Your Drive is full*; the outbox keeps the files |
| The PC sleeps mid-sync | The next run starts over; uploads are idempotent (same name, new revision) |

---

## 10. Tally backup and restore: `app/keeper/backups.py`

- **Set-up:** pick the folder TallyPrime's *Data > Backup* writes to: its Backup Data
  Path, set under *Data Path Configuration* (Alt+Y).
- **Backup file names,** found in spike S4 from Tally's
  [Backup and Restore FAQ](https://help.tallysolutions.com/backups-and-restore-faq/):
  - *TallyPrime 7.0 onwards:* `TDBK1800_<company number>.001` for a one-off backup, and
    `TSDBK1800_<company number>.001` for a scheduled one. Further parts are `.002`, and
    so on. A clash adds `_1`, `_2`.
  - *6.2 and earlier:* `TBK1800_<company number>.001`.
  - *Tally.ERP 9:* `TBK900.001`.
  - The Keeper takes files matching `^T(S?D)?BK\d+(_\d+)*\.\d{3}$`. Each part is sent
    and restored as its own file.
- **TallyPrime 7.0 schedules backups itself,** through the Tally Scheduler service, and they
  run even while Tally is closed. Set-up recommends turning on a daily one to the folder
  above, so there is always a fresh one to send.
- **Each sync:** files in that folder newer than the last one sent are streamed through
  `.ukeep` encryption (1 MiB chunks) into the outbox, then uploaded with a resumable
  upload.
- If the newest Tally backup is more than 7 days old, the Keeper screen and the index
  say so, and the viewer shows it. The Keeper can't make Tally take a backup.
- **Restore,** on the PC, on the Keeper screen:
  1. List the backups from `index.ukeep`.
  2. Download one and decrypt it into a folder the user picks. Tally's data folder is
     refused (G7). The SHA-256 is checked against the one recorded at upload.
  3. Show the steps: in Tally, *Data > Restore*, source = that folder.
- **The viewer can't restore anything.** It can only open the snapshots.

---

## 11. The Keeper module in Ultimate Accountant

**Code:** `app/keeper/main.py`, a FastAPI app mounted at `/keeper` in `app/main.py`, its
start-up entered inside the others. Like the rest of the app it listens only on
127.0.0.1.

**The API** (`/keeper/api/…`):

| Method | Path | What it does |
|---|---|---|
| GET | `/status` | Set up or not; provider; account email; per company: status, last good sync, size; outbox count; next run; backup age |
| POST | `/setup/provider` `{provider}` | Opens the system browser for sign-in; returns `{state}` |
| GET | `/setup/provider/{state}` | Waits until the loopback catches the redirect: `{ok, email}` |
| POST | `/setup/passphrase` `{passphrase, hint}` | Makes the DEK, writes `keeper.json`, holds the DEK with DPAPI |
| GET | `/recovery-sheet` | A printable page: hint, recovery key, how to open the viewer |
| POST | `/tally/test` `{host, port}` | One Export ping through `tally_read` |
| GET / PUT | `/companies` | Which loaded companies to keep (all by default) |
| POST | `/sync` `{all_years?}` | Starts a sync: `202` |
| GET | `/log?n=` | The last lines of `sync.log` |
| PUT | `/schedule` `{on}` | Makes or removes the scheduled task |
| PUT | `/backups/folder` `{path}` | Tally's backup folder |
| POST | `/restore` `{file, to}` | Section 10 |
| POST | `/passphrase` `{new, hint}` | Re-wraps; no old one needed (section 5.1) |
| POST | `/lock-out` | A new DEK; re-encrypts current files |
| POST | `/disconnect` | Revokes, deletes the local key and tokens, removes the task |

**The screen:** `app/keeper/ui/index.html` plus one JS file, plain HTML in the app's
design system (DESIGN.md), as `home.html` is. There is a set-up walk-through with four
steps (Drive, passphrase, Tally, companies), then a status page.

**Home:** a third card with the Keeper's own colours and an opening. The lede gains
*…unless you turn on the Keeper, which keeps an encrypted copy in your own Drive*.

---

## 12. The viewer: `keeper-viewer/`

**Stack:** static HTML, CSS and vanilla ES modules with no build step. The files:
- `index.html`, `app.js`
- `crypto.js` (WebCrypto PBKDF2, AES-GCM, `DecompressionStream('gzip')`)
- `drive.js` (Google and Graph `GET`s)
- `books.js` (the screens' arithmetic)
- `types.js`
- `sw.js`, `manifest.webmanifest`
- `vendor/msal-browser.min.js` (pinned)

Google's sign-in script is loaded from `https://accounts.google.com/gsi/client`.

**Hosting:** GitHub Pages from a new *public* repo,
`your-github-user/ultimate-accountant-keeper`, holding only the viewer. It has no data
and no secrets, and the OAuth client IDs are public by design. A custom domain is
optional later.

**Security headers,** as a `<meta>` CSP:
- `default-src 'self'`
- `script-src 'self' https://accounts.google.com`
- `connect-src` as in G6
- `img-src 'self' data:`
- `frame-src https://accounts.google.com`

**Browsers:** Chrome and Edge 80+, Safari 16.4+ (`DecompressionStream`), Firefox 113+.

**Opening it:**
1. **First time:** choose Google or Microsoft and sign in. Read `keeper.json` and
   `index.ukeep`, then ask for the passphrase (or the recovery key). Unwrap the DEK as a
   **non-extractable** `CryptoKey` and keep it in IndexedDB.
2. **After that:** show the cached copy at once, with its time. In the background, if
   online: get a token (silent, or one tap on Google after an hour) and compare
   `index.ukeep`'s revision. If it is newer, fetch it, then fetch the `current.ukeep`
   files that changed.
3. **Offline:** the service worker caches the app's own files. IndexedDB keeps the
   *encrypted* files, and decrypting happens in memory on each open. Nothing decrypted
   is ever written to storage.

**Screens** (mobile first, 16 px gutters, Indian number grouping through
`Intl.NumberFormat('en-IN')`). Every one shows *Books as of 21 Oct, 10:30 · Tally was
open*:

| Screen | Where its figures come from |
|---|---|
| Companies | `index`: name, last good sync, status, backup age |
| Trial balance | Tally's own `closing` per ledger, rolled up the group tree. Dr and Cr totals, with the difference shown if not 0 |
| Ledger | Opening + the FY's voucher lines for that ledger, with a running balance and a date range. Its computed closing is compared with Tally's; if they differ, *This copy may be incomplete* |
| Day book | Vouchers by date, filterable by type |
| A voucher | Lines, bills, items, narration |
| Search | Ledgers, parties, voucher numbers, references, narrations |
| **Outstanding** | Below |

**Outstanding:**
- **Sides:** a ledger is a receivable if its group is under *Sundry Debtors* and a payable
  if under *Sundry Creditors*, found by walking `groups` up to the reserved group.
- **Per bill:** `pending` comes from `bills` (Tally's own bill-wise figure). The age is
  `snapshot date − (due ?? date)` in days. The buckets are *Not due*, 0-30, 31-60, 61-90
  and 90+. Bills without a due date are counted from the bill date, and the screen says
  so.
- **Per party:** bills, plus an *On account* line where the ledger's `closing` differs
  from the sum of its bills, so the party total always matches Tally's ledger.
- **Sorting:** by overdue amount, largest first; changeable to name or total.

---

## 13. The stack at a glance

| Layer | Choice | Why |
|---|---|---|
| Reading Tally | Python, `httpx`, fixed XML templates | Both already in the app |
| Validation | `pydantic` v2 models | Already pinned |
| Encryption | `cryptography` (AES-GCM, PBKDF2) / WebCrypto | Already pinned / built into browsers |
| Holding secrets on the PC | Windows DPAPI through `ctypes` | No new dependency; tied to the Windows user |
| Talking to the Drive | Plain REST with `httpx`: Drive v3, Graph v1.0 | No SDKs needed for ~8 calls each |
| Signing in on the PC | Loopback with PKCE, stdlib `http.server`, system browser | What Google and Microsoft require |
| Scheduling | Windows Task Scheduler (`schtasks`) | No service, no tray icon |
| Module API | FastAPI, mounted at `/keeper` | Same as the other two modules |
| Viewer | Static HTML + ES modules + service worker | No build, no framework, offline |
| Viewer sign-in | Google Identity Services; MSAL.js (vendored) | The providers' own browser libraries |
| Hosting | GitHub Pages, public repo | Free HTTPS, which OAuth and WebCrypto need |

---

## 14. Code layout

```
app/tally_line.py                  named mutex; the one shared piece (G5)
app/bridge/services/tally.py       one line: _line = tally_line.LINE
app/main.py                        mounts /keeper, enters its start-up
app/home.html                      the third card
launch.py                          --keeper-sync
app/keeper/
  __init__.py
  config.py        settings.json, paths under %APPDATA%\Ultimate Accountant\keeper
  tally_read.py    Export-only client (G2)
  schema.py        pydantic types (section 7)
  snapshot.py      build + checks
  crypto.py        keys, .ukeep v1, DPAPI
  drive.py         class GoogleDrive, class OneDrive, class LocalDrive (tests), with
                   sign-in, list, get, put, delete
  sync.py          engine, outbox, retention, mutex
  backups.py       Tally backup files, restore
  main.py          FastAPI routes
  ui/index.html, ui/keeper.js
  tests/           section 15
keeper-viewer/     section 12 (its own repo when published; kept here as the source)
docs/keeper.md
```

---

## 15. Testing

- **Isolation (G1, G2, G3, G8):** `test_isolation.py` (imports), `test_read_only.py`
  (`_send` refuses Import, TDL functions and actions), the fake Tally's request log
  asserted in every sync test, and the viewer grep.
- **The Bridge unchanged:** `app/bridge/tests/*.py` pass after the `_line` swap. A new test
  checks that the mutex excludes across two processes.
- **The fake Tally** (`app/bridge/tests/fake_tally.py`) learns `Company` with alter ids,
  `Group`, ledger closings, `Bills`, monthly vouchers with bill allocations and items,
  and a request log. Keeper tests may use it, because it is a test fake for Tally, not
  the Bridge's code. It also gets a mode that drops every third connection, to test the retries.
- **Crypto across languages:** Python writes fixtures (a snapshot, a 3 MiB backup, a
  tampered header, a cut-short file, a reordered chunk, a wrong key). A Node script
  using WebCrypto must open the good ones and reject each bad one with the right
  message. Node is already here for bridge-ui.
- **Snapshot checks:** Dr = Cr, sign flips, `&` in names, cancelled and optional
  vouchers, an empty month, a year with no vouchers, paise rounding (`1234.505`).
- **Sync engine:** against `LocalDrive` in a temp folder, covering outbox replay,
  retention, *unchanged* skips, Tally closed mid-year, a failed check leaving the last
  good copy, and two syncs at once.
- **Drives:** a real test against a throwaway Google account and a throwaway Microsoft
  account, run by hand before each release (upload, revise, resumable at 25 MB, delete
  to trash, list).
- **Viewer:** `books.js` unit tests in Node (trial balance, ledger running balance,
  ageing buckets, on-account), plus a phone checklist: Android Chrome, iOS Safari, an
  iOS home-screen app, offline, and *Forget this device*.
- **Real data:** dev runs use temp folders (`ASSURANCE_DATA_DIR` and friends) and the
  fake Tally. A real Tally is read only in spikes S3 and S5, which only read, and only
  with the owner's go-ahead.

---

## 16. Phases

| Phase | What | Done when |
|---|---|---|
| **0. Spikes** | S1 `drive.file` across clients; S2 Google sign-in in an iOS home-screen app; S3 Tally field names (alter ids, `Bills`, closings) on a real TallyPrime; S4 Tally backup file names; S5 snapshot size on real books | Each answered in this file, with the fallback chosen where needed |
| **1. Kept apart** | `tally_line.py`, the Bridge's one-line swap, `test_isolation.py`, `test_read_only.py` scaffold | The Bridge's tests pass; the mutex test passes across processes |
| **2. Keys and format** | `crypto.py`, `keeper.json`, `.ukeep` v1, DPAPI, the Node fixtures check | Python ↔ WebCrypto round trip; every tamper case rejected |
| **3. Reading Tally** | `tally_read.py`, `schema.py`, `snapshot.py`, fake Tally extended | A year off the fake: checks pass, zero non-Export requests |
| **4. Drives** | `drive.py` (Google, OneDrive, Local), the sign-in flows | The by-hand test passes on both throwaway accounts |
| **5. Sync engine** | `sync.py`, outbox, retention, `--keeper-sync`, the scheduled task | Every failure in the section 9 table covered by a test |
| **6. Module screen** | `main.py`, `ui/`, home card, recovery sheet | Set-up from nothing to a first sync, by hand, in a temp data folder |
| **7. Viewer** | `keeper-viewer/`, published to Pages | The phone checklist passes on both providers |
| **8. Backup and restore** | `backups.py`, restore screen | A real Tally backup, round-tripped and restored into a scratch Tally data folder |
| **9. Docs and release** | `docs/keeper.md`, README (module table, where data is, the "nothing leaves" line), version bump, build, publish | The release is live; the viewer link is in the docs |

Phases 2, 3 and 4 depend only on Phase 1 and can run side by side. Phase 7 needs 2 and 4.
**Google Drive is built first** (decided 21 Oct); OneDrive follows the same interface.

### Where Phases 0 and 1 stand (21 October 2026)

**Phase 1 is done.**
- `app/tally_line.py` is the shared line. The Bridge's `tally.py` uses it, with one import and
  one changed line. The Bridge's four test files pass unchanged: 252, 47, 13 and 29 checks.
- `app/keeper/tally_read.py` is the Keeper's Export-only client: the check, the clean-up,
  `send()`, `collection()` and `companies()`.
- `app/keeper/tests/test_kept_apart.py` holds G1, G2, G3 and G5 in 33 checks and is part of
  `scripts\dev.ps1 -Test`. The fake Tally now logs every request (`REQUESTS`).

| Spike | State |
|---|---|
| S1 `drive.file` across the project's clients | **Open.** Google's docs don't settle it. Its [cross-client page](https://developers.google.com/identity/protocols/CrossClientAuth) says consent to one client covers every client in the project for that scope, which points the right way, but per-file access is a separate thing. To settle it: make the Cloud project with a *Desktop* client and a *Web* client, upload a file from the first, and list it from the second. Needs the Cloud project (decision 2, section 18) |
| S2 Google sign-in in an iOS home-screen app | **Open.** Needs a phone and a test page. Done at the start of Phase 7, before the viewer is built on it |
| S3 Tally's fields | **Ready, waiting for Tally.** `scripts/keeper_spike_tally.py` asks for each field and reports which came back filled. It is read-only (it goes through `tally_read`) and prints no names or figures. The script is checked against the fake Tally |
| S4 Tally backup files | **Done.** Section 10 |
| S5 Snapshot size | **Ready, waiting for Tally.** Same script: a year of vouchers, as XML and gzipped |

---

## 17. Limits and risks

- **Freshness:** the books are only as fresh as the last sync, which needs the PC on, the
  user signed in to Windows and Tally open. Every screen shows the time.
- **Companies not loaded in Tally** don't sync. The index says so per company.
- **Google, S1:** if `drive.file` isn't shared between the project's clients, use the
  `drive.appdata` fallback, where the user can't see the files in Drive's UI.
- **Google sign-in on iOS, S2:** the fallback is the redirect flow.
- **Tally field names, S3:** if the alter ids are missing, every sync re-reads the year.
  If `Bills` doesn't give due dates, ageing is counted from bill dates only, and the
  screen says so.
- **Malware on the PC** holds the Drive token and could delete the Drive copies. The
  Drive's trash and version history (30 days) are the safety net, and the Keeper deletes
  only through them.
- **A stolen, unlocked phone** can open the books it has cached. *Lock out every device*
  stops it getting newer ones.
- **OneDrive quota:** the app folder counts against it. Backups are the big files, and
  their retention is the lever.
- **One passphrase for everyone** who is given access. Per-person keys mean wrapping the
  DEK once per person in `keeper.json`; the format already allows a list.

## 18. Decisions before Phase 0

1. ~~Which provider first?~~ Google Drive (21 Oct).
2. **The Cloud project and Entra app are made under whose account?** They belong to
   whoever publishes the viewer; users only ever see the consent screen.

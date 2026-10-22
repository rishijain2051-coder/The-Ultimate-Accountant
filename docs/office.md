# The office: the office server, its PCs, and the Keeper

Ultimate Accountant runs on its own on one PC by default. In the office it can do more:

- **The office server** is the PC with Tally. The other office PCs open its Ultimate
  Accountant over the office network, each signed in as its person, and see only what that
  person may use: the Invoice Bridge, the Assurance Console, or both.
- **The Keeper** runs on the office server. It reads every company in Tally, only ever
  reading, and sends sealed copies of the books for viewing on phones and laptops, along
  with Tally's own backups.
- **The control plane** (`control-plane/`, hosted by you at an address such as
  <https://your-control-plane.example>) is where people sign in with Google, devices are
  approved, and the sealed files are handed out. It never holds the books' key, so it
  can't read them. Its address is `PUBLIC_ORIGIN` on the control plane and
  `UA_CONTROL_PLANE` in the app: see [configuration](configuration.md).

The design and its reasons are in `PLAN-office.md`. This page is how to use it.

## Who sees what

Access is set per person on the admin console's **People** tab (`/admin`):

| Tick | Gives |
|---|---|
| **Books** | The books at `/books`, on approved phones and laptops |
| **Invoice Bridge** | The Invoice Bridge, on an office PC |
| **Console** | The Assurance Console, on an office PC |
| **Admin** | The admin console, the Keeper page, updates, and approving devices |

Every new device waits for an admin's approval. Approve one only when the 24 digits it
shows match the ones on the admin console. **Revoke** stops anything newer reaching a
device. Each new data key is sent only to devices still allowed, and what a phone already
holds stays readable on that phone.

## Setting up

### The office server (once, on the PC with Tally)

Press Win+R and run:

    "%LOCALAPPDATA%\Programs\Ultimate Accountant\Ultimate Accountant.exe" --office server

1. Sign in with the admin Google account in the browser that opens.
2. Approve the PC in the admin console.
3. Confirm the approving device's 24 digits (an older app shows only the first 12, which match the start).
4. Print the recovery sheet.
5. Let the PC through Windows Firewall with the command it shows.
6. Close Ultimate Accountant and open it again.

### An office PC

Add its person on **People** first, with their ticks. Then, on that PC:

    "%LOCALAPPDATA%\Programs\Ultimate Accountant\Ultimate Accountant.exe" --office client <office server's name or IP>

1. Check that the 24 digits shown match the office server's. On the office server, run
   `--office status` to see them.
2. Sign in as the PC's person.
3. Approve the PC in the admin console.

A client PC checks for updates by itself too (from 1.3.1): once a new version is downloaded
and checked, it asks in a dialog whether to install it now.

### A phone or laptop for the books

1. Open your control plane's address (`PUBLIC_ORIGIN`) and sign in.
2. Approve the device from an admin's device.
3. Wait a minute while the office server sends it the books' key, then choose
   **Open the books**.

On an iPhone, add it to the home screen: tap Share, then *Add to Home Screen*. Notifications
only work from there.

## The Keeper

On the office server, admins open **The Keeper: sync status** at the bottom of the home
screen.

- **What it reads:** every company open in Tally. A company closed in Tally keeps its last
  copy in the books, with the time it was last read.
- **Sync now:** reads only what changed in Tally since the last read, using Tally's own
  alter numbers. If nothing changed, nothing is read. Use it any time.
- **Read everything now:** reads every year from the first day of the books, month by
  month, and also notices deleted vouchers. It runs by itself every two hours, once one
  sync has gone through cleanly, and keeps running when the app is closed: the office
  server adds a Windows task, "Ultimate Accountant Keeper", that starts
  `Ultimate Accountant.exe --keeper-sync` every two hours. That does nothing while the
  app is open (the app's own timer does it) or once the PC is set back to `--office alone`;
  uninstalling removes the task. Books that didn't change aren't sent again that day; only
  the list of companies says when Tally was last checked.
- **Starts at sign-in:** "Ultimate Accountant Server" in this Windows account's startup list starts
  the office server with no window (`--background`) when the account signs in, so the office
  and the Keeper run without opening the app; opening the app then shows its window on that
  server, and closing it leaves the server running. A quick read follows two minutes later
  (every ten minutes until Tally answers). Turn it off with `--office alone`, or in Task
  Manager > Startup apps; uninstalling removes it.
- **If Tally falls over or a read fails,** the page says which read it was on. Send that
  line to whoever looks after the app.

Every request the Keeper sends is checked, before it goes, to be a plain read (an Export).
It cannot change anything in Tally.

## The books (`/books`)

- **Trial balance** for any year. The current year is as Tally gives it; past years are
  worked out from the vouchers. Income and expense ledgers start each year at nothing, as
  in Tally.
- **Ledgers:** choose any dates, with the opening, a running balance and the closing. If
  the closing differs from Tally's, the page says so.
- **Vouchers:** filter by dates, type, party or ledger, amount from and to, and number or
  narration.
- **Outstanding:** open bills by how late they are against their due dates (not due,
  1-30, 31-60, 61-90, over 90 days), filtered by party, amount or overdue only.
- **Search:** ledgers and vouchers.
- **Offline:** the last copy opens without the internet, and the page says when it is
  showing one.

## Notifications

Turn them on in the books, under **Notifications**, on each device.

| Notification | Who gets it |
|---|---|
| A bill crossing its due date | Anyone with the books tick who wants them |
| A new voucher at or above the big-transaction amount (₹1,00,000 unless an admin changes it there) | Anyone with the books tick who wants them |
| A device waiting for approval | Admins |
| A sync that failed | Admins |
| A Tally backup over a week old | Admins |
| The office server silent for 3 hours in office hours (09:00-20:00 IST, Monday to Saturday) | Admins |

- **Sealed:** overdue and big-transaction notices are sealed on the office server with the
  books' key. Only the device opens them.
- **Once each:** each bill and voucher is told about once. A company's first sync only
  remembers what is there, so nobody gets every old bill at once.

## Tally's backups

1. **Find Tally's backup folder:** in Tally, the *Backup Data Path* under *Data Path
   Configuration* (Alt+Y). Enter it on the Keeper page and choose Save.
2. **Schedule a daily backup:** in TallyPrime 7, turn on a daily scheduled backup to that
   folder. It runs even while Tally is closed, so there is always a fresh one. The Keeper
   can't make Tally take a backup.
3. **Each sync sends the new ones:** any backup file that is new or changed (`TDBK…`,
   `TSDBK…`, `TBK…`) is sealed and sent, kept for 400 days.
4. **Restore:** choose **Restore…** beside a backup on the Keeper page, and name an
   **empty** folder.
   - The backup is downloaded, opened and checked against the fingerprint taken when it
     was sent.
   - Then, in Tally, use *Data > Restore* with that folder as the source.
   - Restore never writes into a folder that already holds files, so it can't land on
     Tally's own data.

## What the cloud side can and can't see

| | Can it read the books? |
|---|---|
| R2 (the file store) or the control plane's database | No: only ciphertext, and keys wrapped to devices they don't hold |
| A notification on its way | Overdue and big-transaction ones: no, they are sealed. Admin alerts are plain, with no figures |
| An approved phone, unlocked | Yes, what it shows. Revoking it stops anything newer reaching it |

## Later

- **Moving the control plane to a new address** (a domain of your own, say): do this
  before onboarding anyone else. Change `PUBLIC_ORIGIN` and `UA_CONTROL_PLANE` together.
  Every device signs in and is approved again afterwards. Point the Supabase job
  `office-check` at the new address too (see `control-plane/migrations/004_office_check.sql`).

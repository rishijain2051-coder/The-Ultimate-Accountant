# Ultimate Accountant

A Windows desktop app for an Indian small business's books, in a window of its
own. On one computer on its own (the default) there is no account and no upload:
the only things it ever fetches are its own updates (when you configure a release
page) and, when you ask for it, the AI reader, once.

In an office it can also run as the **office server** next to Tally, with the other
office PCs opening it by access, and **the Keeper** sending sealed copies of the books
(and Tally's own backups) to the office's phones and laptops. What leaves the office is
encrypted before it goes; the cloud side only ever holds ciphertext. See
[docs/office.md](docs/office.md).

> **Not affiliated with Tally Solutions.** This is an independent project. It is not
> affiliated with, endorsed by, or sponsored by Tally Solutions Pvt. Ltd. "Tally" and
> "TallyPrime" are trademarks of Tally Solutions Pvt. Ltd., used here only to describe
> compatibility: the app reads from and (when you allow it) posts to TallyPrime through
> its own XML gateway.

| Module | What it does | Manual |
|---|---|---|
| **Assurance Console** | Reconciliation - GSTR-2B against the purchase register, the bank statement against the bank book, a party's statement against your ledger, each ending in a working that ties - and Exports: every export invoice's shipping bill, bill of lading, GSTR-1 line, payments and eBRC filed together | [docs/reconciliation-and-exports.md](docs/reconciliation-and-exports.md) |
| **Invoice Bridge** | A bill, photo or bank statement in; the voucher Tally will get, checked side by side with the page; posted to Tally when you agree | [docs/invoice-bridge.md](docs/invoice-bridge.md) |
| **The Keeper** (office server only) | Every company in Tally, read-only, sealed and sent for the books on phones and laptops: trial balance, ledgers, vouchers, outstanding with ageing; notifications of overdue bills and big transactions; Tally's backups kept and restorable | [docs/office.md](docs/office.md) |

The app opens on the choice of modules (`app/home.html`). Each keeps its own
screens, settings and database, and has a *Switch to* link to the other.

## Running it locally

Windows 10/11, Python 3.12, and Node.js for the Invoice Bridge's screens.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
cd bridge-ui; npm ci; npm run build; cd ..     # the Invoice Bridge's screens, once
.venv\Scripts\python.exe launch.py             # or double-click run_app.bat
```

It opens in its own window (WebView2; without it, the browser) on
<http://127.0.0.1:8770>: the choice at `/`, Assurance Console at `/console/`,
the Invoice Bridge at `/bridge/`. Try it on the fictional sample company in
`SampleData/` (made by `tools/generate_sample_data.py` and `tools/synth_exim.py`).

```powershell
.\scripts\dev.ps1              # auto-reload, and the Invoice Bridge UI hot-reloading on :3000/bridge
.\scripts\dev.ps1 -FakeTally   # also a simulated Tally on :9000 (no TallyPrime needed)
.\scripts\dev.ps1 -Test        # every self-check
```

The self-checks need no network and no Tally; they use temporary data folders.
`control-plane/tests/test_api.py` needs a live Postgres and is not part of `-Test`.

## Configuration

Everything that names your deployment - the update repository, your release
signing key, your control plane - is a setting with no real default:
[docs/configuration.md](docs/configuration.md). Copy `deploy.env.example` to
`deploy.env` for the app and `control-plane/.env.example` for the control plane.

## Deploying your own control plane

The office mode needs a small control plane: `control-plane/`, a FastAPI app for
Vercel with Supabase Postgres, Cloudflare R2 and Google sign-in. It handles
sign-in, device approval and push notifications, and stores only sealed blobs;
the phone/laptop viewer is `control-plane/public`. Step by step:
[docs/configuration.md#the-control-plane-control-plane](docs/configuration.md#the-control-plane-control-plane).

## Signing your own releases

Installed copies update from public GitHub releases on the repository you name in
`UA_UPDATE_REPO`, and believe a release only if its `latest.json` is signed by
your Ed25519 key and names this product; each download must also match its
SHA-256. No token is packed into the program.

```powershell
python packaging/publish_release.py --make-key       # once; prints UA_UPDATE_PUBLIC_KEY for deploy.env
python packaging/build_release.py                    # dist/release/Install.exe and Update.exe
python packaging/build_release.py --without-updates  # a copy that never updates
python packaging/publish_release.py "What is new"    # signs latest.json, makes the GitHub release (gh)
# The next update: raise VERSION in app/backend/version.py and packaging/installer.py, build, publish.
```

`dist/release/Install.exe` is the one file to send. `Update.exe` carries the
app's own code alone for copies on the same base. Details:
[docs/reconciliation-and-exports.md](docs/reconciliation-and-exports.md#packaging-it-as-a-windows-program).

## Security

The design - books sealed before they leave the office, data keys handed only to
devices an admin approved, signed updates, Tally only ever read by the Keeper, a
Host/Origin guard on the local server - is described in [SECURITY.md](SECURITY.md),
with how to report a problem. Permissions: [docs/permissions.md](docs/permissions.md).

## Where the data is

| | Where | Moving it |
|---|---|---|
| Reconciliation and Exports | beside the program, or the folder chosen at install (`data-location.txt`) | Settings > *Move it to another folder…* |
| Invoice Bridge | `%APPDATA%\Ultimate Accountant\bridge` | not movable: its database holds each bill's full path |
| Office state | `%APPDATA%\Ultimate Accountant\office` | - |

## Layout

```
app/
  main.py          one server: every module
  home.html        the first page: the choice of module
  backend/         Reconciliation and Exports, and what the app shares: ai.py, updates.py, deploy.py, paths.py, version.py
  frontend/        their screens: plain JS, no build
  bridge/          the Invoice Bridge's backend (code paths keep the old `bridge` name)
    tests/         its self-checks, a simulated Tally, a synthetic sample invoice
  keeper/          the Keeper: reads Tally, seals snapshots
  office/          office server and client modes
bridge-ui/         the Invoice Bridge's screens: Next.js, exported static, basePath /bridge
control-plane/     the control plane (Vercel) and the phone/laptop viewer (public/)
launch.py          the program's entry point
build.spec         the app build; packaging/ the installer, uninstaller, release
SampleData/ tools/ the fictional sample company and its generators
media/             the guides and their screenshot/video tools
brand/             the logos and their generators
docs/              the manuals and configuration
```

## Contributing, support, licence

[CONTRIBUTING.md](CONTRIBUTING.md) · [SUPPORT.md](SUPPORT.md) (best effort) ·
licence below.

## Licence

Copyright (C) 2026 Rishi Jain - Draveta Technologies

This program is free software: you can redistribute it and/or modify it under the
terms of the GNU Affero General Public License, version 3, as published by the Free
Software Foundation. It is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

In short: anyone may use, change and share it, but whoever shares a changed copy - or
runs a changed copy for others over a network, such as a control plane - must offer
those users the changed source under the same licence.

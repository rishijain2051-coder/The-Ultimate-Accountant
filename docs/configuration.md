# Configuration

Everything that names *your* deployment - where updates come from, the key they
are signed with, your control plane - is a setting. Nothing has a real default:
a copy built without them runs on one computer, never updates itself, and has no
office mode until you set a control plane.

## The desktop app

Read by `app/backend/deploy.py`, first from the environment, then from a
`deploy.env` file (`KEY=value` lines) at the repository root. `build.spec` packs
`deploy.env` into the installed program, so set them **before you build**.
Copy [`deploy.env.example`](../deploy.env.example) to `deploy.env` to start.
`deploy.env` is git-ignored; it holds public values only, never a secret.

| Setting | Example | What it is |
|---|---|---|
| `UA_UPDATE_REPO` | `your-github-user/ultimate-accountant-releases` | The **public** GitHub repository whose releases installed copies check (`app/backend/updates.py`). No token: the release page is read anonymously. Empty: no update checks. |
| `UA_UPDATE_PUBLIC_KEY` | `MlLH...=` (base64, 32 bytes) | The public half of **your** Ed25519 release-signing key. A release file whose signature does not verify against it is ignored. Empty: no update checks. |
| `UA_CONTROL_PLANE` | `https://your-control-plane.example` | Your control plane (below), used by office server and client modes (`app/office/settings.py`). Each PC also stores it in its office `settings.json` once set up. Default: the placeholder `https://your-control-plane.example`, which never resolves. |

Other environment variables the app reads (development and tests, not deployment):

| Variable | What it does |
|---|---|
| `ASSURANCE_DATA_DIR` | Put all of the console's data (and the office's, under `office\`) in this folder: a trial run that never touches real books. |
| `ASSURANCE_OFFICE_DIR` | The office state folder (default `%APPDATA%\Ultimate Accountant\office`). |
| `ASSURANCE_AI_DIR` | Where the AI runtime and model live (default `%LOCALAPPDATA%\Ultimate Accountant\ai`). |
| `ASSURANCE_DOWNLOADS_DIR` | A stand-in for the Downloads folder. |
| `ASSURANCE_UPDATE_API` | Point update checks at a stand-in for `https://api.github.com` (the self-tests do), and turn them on when run from the source. |
| `ASSURANCE_SIGNING_KEY` | Path of the release-signing private key for `packaging/publish_release.py` (default `%USERPROFILE%\.ultimate-accountant\release-signing-key.pem`). |
| `DATA_DIR`, `DATABASE_URL`, `TALLY_HOST`, `TALLY_PORT`, `TALLY_ALLOW_POST`, `LLM_ENABLED`, ... | The Invoice Bridge's settings: see [`.env.example`](../.env.example) (`app/bridge/config.py`). |

### Signing your own releases

```powershell
python packaging/publish_release.py --make-key   # once: writes %USERPROFILE%\.ultimate-accountant\release-signing-key.pem
#   and prints UA_UPDATE_PUBLIC_KEY=... - put it, and UA_UPDATE_REPO, in deploy.env
python packaging/build_release.py                # dist/release/Install.exe and Update.exe
python packaging/publish_release.py "What is new"   # signs latest.json; creates the GitHub release with `gh`
```

The private key never goes in the repository or the program (`*.pem` is
git-ignored). Keep an offline copy: without it no update can be signed, and every
installed copy would need a new installer carrying a new key. A release file must
also name the product (`"product": "ultimate-accountant"`), so a key you use for
another product cannot push that product's releases onto this one.

`python packaging/build_release.py --without-updates` builds a copy that never
checks for updates, without any of the above.

## The control plane (`control-plane/`)

A FastAPI app for Vercel (Python), with Supabase Postgres and Cloudflare R2. It
only ever holds ciphertext of the books; see [office.md](office.md). Every value
is an environment variable - in Vercel's project settings for production, and in
`%USERPROFILE%\.ultimate-accountant\office.env` for `cp.migrate` and `dev.py` on
your PC. [`control-plane/.env.example`](../control-plane/.env.example) lists them;
`python scripts/office_secrets.py --generate` fills in the keys you make yourself,
and `python scripts/office_secrets.py` checks the shape of the rest.

| Variable | What it is |
|---|---|
| `PUBLIC_ORIGIN` | The control plane's own address, `https://...` with no path. Token issuer, Google redirect, Web Push subject, and the address the office-check job calls (migration 004). Required. |
| `DATABASE_URL` | Supabase's **transaction pooler** URL (port 6543). |
| `CP_SCHEMA` | The Postgres schema, default `office`. The tests and `dev.py` use their own. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | A Google OAuth *Web application* client; register `PUBLIC_ORIGIN` + `/api/auth/google/callback` (and `http://localhost:8000/...` for `dev.py`) as redirect URIs. |
| `R2_ENDPOINT` | `https://<account id>.r2.cloudflarestorage.com` |
| `R2_BUCKET` | Your bucket's name. Not public. |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | An R2 API token for objects in that bucket only. |
| `JWT_SIGNING_KEY` | Ed25519 private key (base64url DER) for the control plane's tokens. Generated. |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY` | Web Push keys. Generated. |
| `CRON_SECRET` | Generated by `office_secrets.py`; reserved, not read by the control plane today. |

**Content-Security-Policy** (`control-plane/vercel.json`): `connect-src 'self'
https://*.r2.cloudflarestorage.com` lets the viewer fetch sealed books straight
from R2 with pre-signed URLs. To narrow it to your own account, replace `*` with
your account id. If you store blobs elsewhere, change it to match.

**The office check** (`control-plane/migrations/004_office_check.sql`): pg_cron
calls `PUBLIC_ORIGIN/api/cron/office-check` every 20 minutes. `cp.migrate`
fills the address in from `PUBLIC_ORIGIN`; with it unset, nothing is scheduled.
If the address changes, run the `cron.schedule(...)` in that file again by hand
with the new one (the same job name replaces the old job).

### Setting one up

1. Create a Supabase project, a Cloudflare R2 bucket (private) with an
   objects-only token, and a Google OAuth client. Put the values in
   `office.env`; run `python scripts/office_secrets.py --generate`.
2. `cd control-plane` and `python -m cp.migrate --admin you@example.com` to make
   the schema and the first admin.
3. Deploy `control-plane/` to Vercel (Root Directory `control-plane`) with the
   same variables in its Production environment
   (`python scripts/office_secrets.py --push-vercel` does it with the Vercel CLI).
4. Set `UA_CONTROL_PLANE` to its address before building the app (or put it in a
   PC's office `settings.json` as `control_plane`).

# Security

## Reporting a problem

Please report security issues privately, not in a public issue: use GitHub's
**Report a vulnerability** (Security tab > Advisories) on this repository. Say what
you found, how to reproduce it, and what it lets someone do. You should hear back
within a week; this is a small, best-effort project (see [SUPPORT.md](SUPPORT.md)),
so please allow time for a fix before telling anyone else.

In scope: the desktop app (`app/`, `launch.py`, `packaging/`), the control plane and
viewer (`control-plane/`), and the update and signing flow. Out of scope: TallyPrime
itself, and deployments you do not run yourself.

## The design, in short

- **Local first.** On one PC the app has no account and sends nothing but update
  checks (only when a release page is configured). The local server listens on
  127.0.0.1 and refuses requests whose `Host` or `Origin` is not its own, so a web
  page cannot drive it (DNS rebinding, cross-site POSTs).
- **Tally is read, not written, unless you say so.** Posting to Tally is off until
  turned on in Settings, and needs the *Post to Tally* permission. The Keeper only
  ever sends read requests (`app/keeper/tests/test_kept_apart.py` checks every one).
- **Sealed books.** The Keeper encrypts each snapshot on the office server before it
  leaves; the control plane and R2 hold only ciphertext and never see a data key.
- **Device-approved keys.** A phone or laptop gets the books' data key only after an
  admin approves that device, wrapped for that device's own key pair; the office
  server pins the admin keys it trusts. Removing a person or device stops what it
  can fetch.
- **Signed updates.** An update is believed only when `latest.json` verifies against
  the Ed25519 public key built into the program and names this product; the
  installer must match the SHA-256 in it. The private key stays with whoever
  publishes releases (see [docs/configuration.md](docs/configuration.md)). If you fork
  this project, use your own key.
- **Detailed permissions** for each person: [docs/permissions.md](docs/permissions.md).

## Your own deployment

Never commit `deploy.env` values you consider private, `office.env`, `*.pem`, or the
control plane's secrets; `.gitignore` excludes them. Rotate `JWT_SIGNING_KEY`, the R2
token and the Google client secret if they may have leaked.

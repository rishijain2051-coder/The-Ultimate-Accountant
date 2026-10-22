# Settings

**Settings** (in the cover band) saves each change as you make it. Settings apply to every run and client - there is one set.

!shot 30-settings-exports | Settings, Exports: which date the payment deadline counts from, how early to warn, and the rules in force.

## Your business

| Setting | Default | What it changes |
|---|---|---|
| Business name | blank | Printed on reports and letters. Fills in *Business name* on a GST check (and wins over the name in the Tally file unless you clear it). Bank checks always take the business name from here |
| Your GSTIN | blank | Fills in the GST check (it sets your home state for the place-of-supply check); printed on reports; used by the GST sample if set |
| Your name | blank | Printed as preparer on the PDFs; signs the supplier letters, the balance confirmation letter and the export drafts |
| Address | blank | Printed on the export drafts |

## How closely entries must agree

| Setting | Default | What it changes |
|---|---|---|
| GST: allow a difference of (₹) | 2 | The rounding allowed on amounts (the sample always uses 2) |
| GST: look this many days either side | 45 | The date window for *Probably the same bill* and *Entered in parts* |
| Bank: allow a difference of (₹) | 1 | The difference above which a pair is *Amount differs*, and the allowance on several-in-one groups |
| Bank: same transaction if within (days) | 7 | The date window for a match |
| Bank: timing difference up to (days) | 45 | How far apart a *Timing difference* can be |
| Party statements: allow a difference of (₹) | 1 | As for bank |

## Exports

| Setting | Default | What it changes |
|---|---|---|
| Count the payment deadline from | The shipping bill date | Or the let export order date, or the shipped-on-board date on the bill of lading - your bank decides which |
| Warn this many days before a payment is due | 90 | When an unpaid bill counts as *due soon* (0 to 365) |
| Also give 15 months to bills shipped before 14 Nov 2025 that were still unpaid on that day | off | Left off, those bills keep 9 months - the earlier date, and the safer one to work to |

The line above them shows the realisation rules the app is using, and the date they are as of.

## AI checker

| Setting | Default | What it changes |
|---|---|---|
| State | not on this computer | **Get the AI checker** downloads it (about 5 GB, once; you can go on working meanwhile, and a cut-off download carries on where it stopped) - or, when Invoice Bridge has already downloaded the same AI, *Uses the AI that Invoice Bridge downloaded*, with nothing to download; then how many documents it has checked, how many read the same and how many differently, and **Remove it** |
| Check documents in the background | on | Off: it stops checking; what it has read already stays with each document |

See *Exports > The AI checker*.

## The app

| Setting | Default | What it changes |
|---|---|---|
| Version | - | The version you have, whether it is the newest and when that was last checked, **Check now** - and **Restart and update** when a newer version is ready (see *Updating*) |
| Check for updates when this computer is online | on | Off: the app makes no connection to the internet at all |
| Where your data is kept | beside the program | Shows the folder and its size; **Show the folder**; **Move it to another folder…** (see *Where your data is kept*) |
| Keep every check, so you can open it again and download its reports | on | Off: a check is shown once and not kept - no downloads, no kept files, no Match again. Export documents are always kept |
| Close the app | - | Closes the window and stops the program |

x Some finer matching thresholds (how alike names must be, the score for a probable match, the percentage allowance, the size of several-in-one groups) have no place in Settings; they keep their defaults, listed in *Every number the app uses*.
x Settings cannot be kept per client.

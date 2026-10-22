"""The detailed permissions (docs/permissions.md): the keys, their labels, and the implication
rule. The old broad keys still work: console means every con.*, tib every tib.*, books every
books.*. Admin implies nothing else."""

from __future__ import annotations

AREAS = {"con": "console", "tib": "tib", "books": "books"}

# key -> (label, what it gives). Same words as control-plane/public/caps.js.
KEYS = {
    "con.recon": ("Reconciliation", "GST 2B/Tally, bank and party-statement reconciliation; runs, items, decisions, suppliers, workspaces"),
    "con.exports": ("Exports register", "The export documents register: add, correct, settle, mark, read with AI"),
    "con.reports": ("Reports and downloads", "Download Excel/PDF/CSV reports, follow-up packs, kept files and zips"),
    "con.settings": ("Console settings", "Change the console's settings (tolerance, financial year, letter details, update checks)"),
    "con.ai": ("AI checker", "Get or remove the AI runtime, turn the AI second reading on or off"),
    "tib.upload": ("Upload bills", "Upload bills, use the phone code"),
    "tib.review": ("Review entries", "See, edit, reject, rematch and check entries"),
    "tib.post": ("Post to Tally", "Approve entries into Tally (post or draft), create masters"),
    "tib.bank": ("Bank statements", "Upload bank statements, check and post bank rows"),
    "tib.sync": ("Sync from Tally", "Read companies, ledgers, stock and voucher types from Tally"),
    "books.view": ("See the books", "Open the Keeper's books on approved phones and laptops (gets the data key)"),
    "books.alerts": ("Alerts", "Overdue and big-payment notifications"),
    "books.balances": ("Balances", "Cash and bank, accounts and party balances screens"),
    "books.sales": ("Sales and purchases", "Sales/purchase figures and the transactions list"),
}
LEGACY = {"console": "con", "tib": "tib", "books": "books"}      # old key -> area prefix
LEGACY_LABELS = {"console": "Console (everything)", "tib": "Bridge (everything)", "books": "Books (everything)"}


def has(caps, key: str) -> bool:
    """caps holds `key`, or the old broad key of its area (admin is never implied)."""
    caps = caps or ()
    area = key.split(".")[0]
    return key in caps or any(old in caps for old, a in LEGACY.items() if a == area and old != key)


def any_in(caps, area: str) -> bool:
    """caps holds any key of the area ('con', 'tib', 'books')."""
    caps = caps or ()
    return any(c.split(".")[0] == area or (c in LEGACY and LEGACY[c] == area) for c in caps)


def allows(caps, need: str | None) -> bool:
    """need: None (public), 'any' (anyone signed in), 'any:<area>', 'admin', or one key."""
    if need in (None, "any"):
        return True
    if need.startswith("any:"):
        return any_in(caps, need[4:])
    return has(caps, need)

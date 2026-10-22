"""GST rules that are arithmetic, not guesswork.

Everything here is deterministic. The LLM is never asked whether a tax split is
right -- a checksum and a subtraction answer that better than any model can.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z][Zz][0-9A-Z])\b")
PAN_RE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")

_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "28": "Andhra Pradesh (Old)", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}

# Rates that actually exist under GST. Anything else is an extraction error.
VALID_GST_RATES = {0.0, 0.1, 0.25, 1.0, 1.5, 3.0, 5.0, 6.0, 7.5, 12.0, 18.0, 28.0}


def gstin_checksum_ok(gstin: str) -> bool:
    """Validate the 15th character (base-36 weighted mod 36)."""
    g = (gstin or "").strip().upper()
    if len(g) != 15 or not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]", g):
        return False
    total = 0
    factor = 1
    for ch in g[:14]:
        if ch not in _B36:
            return False
        prod = _B36.index(ch) * factor
        total += prod // 36 + prod % 36
        factor = 2 if factor == 1 else 1
    return _B36[(36 - total % 36) % 36] == g[14]


def gstin_state(gstin: Optional[str]) -> Optional[str]:
    if gstin and len(gstin) >= 2 and gstin[:2] in STATE_CODES:
        return gstin[:2]
    return None


def state_name(code: Optional[str]) -> Optional[str]:
    return STATE_CODES.get(code) if code else None


def find_gstins(text: str) -> list[str]:
    """All syntactically-valid GSTINs in document order, de-duplicated."""
    seen: list[str] = []
    for m in GSTIN_RE.finditer(text.upper()):
        g = m.group(1).upper()
        if g not in seen:
            seen.append(g)
    return seen


def expected_split(seller_state: Optional[str], place_of_supply: Optional[str]) -> Optional[str]:
    """'igst' for inter-state, 'cgst_sgst' for intra-state, None if unknown."""
    if not seller_state or not place_of_supply:
        return None
    return "cgst_sgst" if seller_state == place_of_supply else "igst"


def money(v) -> Decimal:
    return Decimal(str(v or 0))


def close(a, b, tol: str = "1.00") -> bool:
    """Within tolerance. Invoices legitimately round at the paisa level."""
    return abs(money(a) - money(b)) <= Decimal(tol)

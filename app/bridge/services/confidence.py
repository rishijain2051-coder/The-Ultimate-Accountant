"""One number for "should I look at this?", plus the reasons behind it.

A score with no explanation is a score nobody trusts, so score() returns the
breakdown it used. The UI shows both.
"""

from __future__ import annotations

from typing import Any

# What actually has to be right before an entry can be posted unread.
WEIGHTS: dict[str, float] = {
    "party": 0.30,      # wrong ledger = wrong books
    "grand_total": 0.18,
    "invoice_no": 0.12,
    "invoice_date": 0.12,
    "taxable_total": 0.10,
    "tax": 0.10,
    "lines": 0.08,
}

ERROR_PENALTY = 0.55
WARN_PENALTY = 0.93


def score(
    fields: dict[str, dict[str, Any]],
    party_confidence: float,
    validations: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    ocr_confidence: float = 1.0,
) -> tuple[float, dict[str, Any]]:
    def fc(key: str) -> float:
        f = fields.get(key)
        return float(f.get("confidence", 0.0)) if f and f.get("value") is not None else 0.0

    tax_conf = max(fc("igst"), (fc("cgst") + fc("sgst")) / 2 if fc("cgst") and fc("sgst") else 0.0)
    if not tax_conf and fields.get("implied_tax_amount"):
        tax_conf = fc("implied_tax_amount")

    line_conf = 0.0
    if lines:
        ok = sum(1 for r in lines if r.get("arith_ok"))
        checkable = sum(1 for r in lines if r.get("quantity") and r.get("rate"))
        # Lines we can't verify aren't wrong, just unproven -- 0.7, not 0.
        line_conf = (ok / checkable) if checkable else 0.7

    parts = {
        "party": party_confidence,
        "grand_total": fc("grand_total"),
        "invoice_no": fc("invoice_no"),
        "invoice_date": fc("invoice_date"),
        "taxable_total": fc("taxable_total"),
        "tax": tax_conf,
        "lines": line_conf,
    }
    base = sum(WEIGHTS[k] * v for k, v in parts.items())

    errors = [v for v in validations if v.get("severity") == "error"]
    warns = [v for v in validations if v.get("severity") == "warn"]
    penalty = (ERROR_PENALTY ** len(errors)) * (WARN_PENALTY ** len(warns))

    final = base * penalty * min(1.0, 0.55 + 0.45 * ocr_confidence)

    weakest = sorted(parts.items(), key=lambda kv: kv[1] * WEIGHTS[kv[0]])[:3]
    breakdown = {
        "components": {k: round(v, 3) for k, v in parts.items()},
        "weights": WEIGHTS,
        "base": round(base, 3),
        "errors": len(errors),
        "warnings": len(warns),
        "penalty": round(penalty, 3),
        "ocr_confidence": round(ocr_confidence, 3),
        "weakest": [k for k, _ in weakest],
        "final": round(final, 3),
    }
    return round(min(max(final, 0.0), 1.0), 3), breakdown


def label(value: float) -> str:
    if value >= 0.90:
        return "high"
    if value >= 0.70:
        return "medium"
    return "low"

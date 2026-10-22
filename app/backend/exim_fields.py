"""
Checking and correcting what was read from an export document.

Every document the Exports module reads waits to be checked before it goes
into the register, and can be corrected at any time after. This module holds
what each kind of document's form shows, the few facts the check puts in front
of the user, how an edit is checked before it is kept, and how corrections are
laid over what the reader found.

The reader's own output is never changed. Corrections are kept beside it - a
value per field, and the whole corrected list for a table the user touched -
and laid over it each time the register is worked out, so what was read and
what it was corrected to both stay known, and the history can show each change.
"""

import copy
import math
import re
from datetime import datetime

from app.backend.exim_read import TYPE_WORDS, _date

MODES = "SEA|AIR|LAND|RAIL|POSTAL|COURIER"
CHANGEABLE = ("sb", "bl", "fira", "invoice")      # one-document kinds a misread file can be turned into

# Each kind of document: its fields, (key, label, kind), and its tables,
# (key, label, rows can be added, a row needs one of these, columns). Kinds:
# text, upper, currency, date, month, number, bool, list (codes or container
# numbers), refs:<number key>:<date key>, choice:<a|b|...>.
FORMS = {
    "sb": ([("sb_no", "Shipping bill number", "text"), ("sb_date", "Shipping bill date", "date"),
            ("port", "Port code", "upper"), ("leo_date", "Let export order date", "date"),
            ("exporter", "Exporter", "text"), ("buyer", "Buyer", "text"), ("gstin", "Exporter's GSTIN", "upper"),
            ("iec", "IEC", "upper"), ("ad_code", "AD code", "text"), ("mode", "Mode", f"choice:{MODES}"),
            ("country", "Country of destination", "text"), ("containers", "Containers", "list"),
            ("dbk_amount", "Drawback claimed (₹)", "number"), ("rodtep_amount", "RoDTEP claimed (₹)", "number")],
           [("invoices", "Invoices on it", True, ("inv_no",),
             [("inv_no", "Invoice number", "text"), ("inv_date", "Invoice date", "date"),
              ("currency", "Currency", "currency"), ("value_fc", "Value", "number"), ("fob_fc", "FOB value", "number"),
              ("rate", "Exchange rate", "number"), ("terms", "Terms", "upper"), ("igst_amount", "IGST (₹)", "number")])]),
    "bl": ([("bl_no", "Bill of lading number", "text"), ("sob_date", "Shipped on board", "date"),
            ("invoice_refs", "Invoice numbers on it", "refs:inv_no:inv_date"),
            ("sb_refs", "Shipping bill numbers on it", "refs:sb_no:sb_date"), ("containers", "Containers", "list"),
            ("vessel", "Vessel", "text"), ("loading_port", "Port of loading", "text"),
            ("consignee", "Consignee", "text"), ("transport", "Sea or air", "choice:sea|air")], []),
    "fira": ([("amount_fc", "Amount", "number"), ("currency", "Currency", "currency"),
              ("remitter", "Remitter", "text"), ("credit_date", "Date of credit", "date"),
              ("ref", "Bank reference", "text"), ("invoice_ref", "Invoice named on it", "text"),
              ("purpose_code", "Purpose code", "upper"), ("advance", "An advance for goods not yet shipped", "bool"),
              ("amount_inr", "Rupee equivalent (₹)", "number"), ("rate", "Exchange rate", "number")], []),
    "invoice": ([("inv_no", "Invoice number", "text"), ("inv_date", "Invoice date", "date"),
                 ("buyer", "Buyer", "text"), ("currency", "Currency", "currency"), ("value_fc", "Value", "number"),
                 ("value_inr", "Value in rupees (₹)", "number"), ("scheme", "IGST paid, or under LUT", "choice:WPAY|LUT"),
                 ("taxable_inr", "Taxable value (₹)", "number"), ("igst_inr", "IGST (₹)", "number")], []),
    "gstr1": ([("gstin", "GSTIN", "upper"), ("period", "Month of the return", "month")],
              [("lines", "Table 6A lines", True, ("inum",),
                [("inum", "Invoice number", "text"), ("idt", "Invoice date", "date"),
                 ("exp_typ", "Export type", "choice:WPAY|WOPAY"), ("val", "Invoice value (₹)", "number"),
                 ("sbpcode", "Port code", "upper"), ("sbnum", "Shipping bill number", "text"),
                 ("sbdt", "Shipping bill date", "date"), ("txval", "Taxable value (₹)", "number"),
                 ("iamt", "IGST (₹)", "number")])]),
    "irm_list": ([], [("rows", "Remittances", True, ("irm_no",),
                       [("irm_no", "IRM number", "text"), ("irm_date", "IRM date", "date"),
                        ("remit_date", "Remittance date", "date"), ("remitter", "Remitter", "text"),
                        ("currency", "Currency", "currency"), ("amount_fc", "Amount", "number"),
                        ("available_fc", "Still available", "number"), ("purpose_code", "Purpose code", "upper"),
                        ("status", "Status", "text"), ("bank_ref", "Bank reference", "text"),
                        ("ifsc", "IFSC", "upper"), ("ad_code", "AD code", "text")])]),
    "ebrc_list": ([], [("rows", "eBRCs", True, ("ebrc_no",),
                        [("ebrc_no", "eBRC number", "text"), ("ebrc_date", "eBRC date", "date"),
                         ("status", "Status", "text"), ("irm_no", "IRM number", "text"),
                         ("sb_no", "Shipping bill number", "text"), ("sb_date", "Shipping bill date", "date"),
                         ("port", "Port code", "upper"), ("bill_no", "Bill number", "text"),
                         ("gst_inv_no", "GST invoice number", "text"), ("gst_inv_date", "GST invoice date", "date"),
                         ("currency", "Currency", "currency"), ("realised_fc", "Realised", "number")])]),
    "sb_status": ([], [("rows", "Shipping bills", True, ("sb_no",),
                        [("sb_no", "Shipping bill number", "text"), ("sb_date", "Shipping bill date", "date"),
                         ("port", "Port code", "upper"), ("status", "Status", "text"), ("error", "Error", "text")])]),
    "refund_status": ([], [("rows", "Refund and drawback lines", True, ("sb_no",),
                            [("sb_no", "Shipping bill number", "text"), ("kind", "IGST or drawback", "choice:igst|dbk"),
                             ("code", "Code", "upper"), ("status", "Status", "text"),
                             ("scroll_no", "Scroll number", "text"), ("scroll_date", "Scroll date", "date"),
                             ("paid_date", "Paid on", "date"), ("amount", "Amount (₹)", "number")])]),
    "dbk_pending": ([], [("rows", "Shipping bills waiting on proof of payment", True, ("sb_no",),
                          [("sb_no", "Shipping bill number", "text"), ("sb_date", "Shipping bill date", "date"),
                           ("expected", "Proof expected by", "date"), ("dbk", "Drawback (₹)", "number"),
                           ("status", "Status", "text")])]),
    "edpms": ([], [("rows", "Shipping bills on the statement", True, ("sb_no",),
                    [("sb_no", "Shipping bill number", "text"), ("sb_date", "Shipping bill date", "date"),
                     ("inv_no", "Invoice number", "text"), ("currency", "Currency", "currency"),
                     ("value_fc", "Value", "number"), ("realised_fc", "Realised", "number"),
                     ("outstanding_fc", "Outstanding", "number"), ("due", "Due", "date"),
                     ("status", "Status", "text")])]),
    # The user's own workbook: its rows can be put right here, but a row that
    # is missing belongs in the workbook itself.
    "own_register": ([], [("rows", "Rows of your register", False, ("sb_no", "inv_no"),
                           [("inv_no", "Invoice number", "text"), ("inv_date", "Invoice date", "date"),
                            ("sb_no", "Shipping bill number", "text"), ("sb_date", "Shipping bill date", "date"),
                            ("port", "Port code", "upper"), ("container", "Container", "text"),
                            ("igst", "IGST paid (₹)", "number"), ("igst_recd", "IGST received (₹)", "number"),
                            ("igst_date", "IGST received on", "date"), ("dbk", "Drawback (₹)", "number"),
                            ("dbk_date", "Drawback received on", "date"), ("codes", "ICEGATE codes", "list")]),
                          ("dbk_pending", "ICEGATE's drawback list in it", False, ("sb_no",),
                           [("sb_no", "Shipping bill number", "text"), ("sb_date", "Shipping bill date", "date"),
                            ("expected", "Proof expected by", "date"), ("dbk", "Drawback (₹)", "number"),
                            ("status", "Status", "text")])]),
}


# ---------------------------------------------------------------------------
# Laying corrections over what was read
# ---------------------------------------------------------------------------

def apply(read, corrections):
    """The document as it stands: what was read, with the user's corrections on top."""
    if not corrections:
        return read
    out = {**read, **copy.deepcopy(corrections)}
    if out.get("type") == "sb" and "invoices" in corrections:
        _sb_totals(out)
    return out


def _sb_totals(sb):
    """A shipping bill's IGST total and scheme follow its invoices once they are corrected."""
    paid = [i.get("igst_amount") for i in sb.get("invoices") or [] if i.get("igst_amount") is not None]
    if paid:
        sb["igst_amount"] = round(sum(paid), 2)
        sb["scheme"] = "WPAY" if sb["igst_amount"] > 0 else "LUT"


def corrected_keys(read, corrections):
    """Which of a document's fields and tables the user has corrected."""
    return sorted(k for k in (corrections or {}) if corrections[k] != read.get(k))


# ---------------------------------------------------------------------------
# Checking an edit
# ---------------------------------------------------------------------------

def clean(kind, raw, label, before=None):
    """One typed value, checked and put in the form the register uses. Blank
    clears the field. Raises ValueError naming the field and the problem."""
    if kind.startswith("refs:"):
        num, dated = kind.split(":")[1:]
        dates = {}                                  # a number kept keeps the date read beside it
        for r in before or []:
            if isinstance(r, dict) and r.get(dated):
                dates.setdefault(r.get(num), r.get(dated))
        words = [w.strip().upper() for w in re.split(r"[,;\n]+", str(raw or "")) if w.strip()]
        return [{num: w, dated: dates.get(w)} for w in dict.fromkeys(words)]
    if kind == "list":
        return list(dict.fromkeys(w.upper() for w in re.split(r"[\s,;]+", str(raw or "")) if w))
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).strip().lower() in ("yes", "true", "1"):
            return True
        if str(raw).strip().lower() in ("no", "false", "0", ""):
            return False
        raise ValueError(f"{label}: answer yes or no.")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if kind == "number":
        try:
            value = float(str(raw).replace(",", "").strip()) if not isinstance(raw, (int, float)) else float(raw)
        except ValueError:
            raise ValueError(f"{label}: “{raw}” is not a number.") from None
        if not math.isfinite(value) or value < 0 or isinstance(raw, bool):
            raise ValueError(f"{label} has to be a number, zero or more.")
        return round(value, 2)
    text = str(raw).strip()
    if len(text) > 200:
        raise ValueError(f"{label} is longer than it can be.")
    if kind == "date":
        value = text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) and _date(text) == text else _date(text)
        if not value:
            raise ValueError(f"{label}: “{text}” is not a date.")
        return value
    if kind == "month":
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
        if not m or not 1 <= int(m.group(2)) <= 12:
            raise ValueError(f"{label} is a month, written like 2026-04.")
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    if kind == "currency":
        if not re.fullmatch(r"[A-Za-z]{3}", text):
            raise ValueError(f"{label} is a three-letter code, like USD or EUR.")
        return text.upper()
    if kind.startswith("choice:"):
        options = kind.split(":", 1)[1].split("|")
        hit = next((o for o in options if o.lower() == text.lower()), None)
        if hit is None:
            raise ValueError(f"{label} is one of {', '.join(options)}.")
        return hit
    return text.upper() if kind == "upper" else text


def words(kind, value):
    """A value as the history writes it."""
    if value in (None, "", []):
        return "blank"
    if kind.startswith("refs:"):
        num = kind.split(":")[1]
        return ", ".join(r.get(num) or "" for r in value) or "blank"
    if kind == "list":
        return ", ".join(value)
    if kind == "bool":
        return "yes" if value else "no"
    if kind == "date":
        d = _date(value)
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d %b %Y").lstrip("0") if d else str(value)
    if kind == "number":
        return f"{value:,.2f}"
    return str(value)


def _same(kind, a, b):
    if kind.startswith("refs:"):
        num = kind.split(":")[1]
        return [r.get(num) for r in a or []] == [r.get(num) for r in b or []]
    if kind == "number" and a is not None and b is not None:
        return abs(a - b) < 0.005
    return (a if a not in ("", []) else None) == (b if b not in ("", []) else None)


def correct(read, corrections, edit):
    """Check an edit and fold it into the corrections kept for one document.

    `read` is what the reader found; `corrections` what the user corrected
    before; `edit` is {"type": kind, "fields": {key: typed value}, "tables":
    {key: [row, ...]}}, each row carrying "_i", its place in the table as it
    stood, unless it is new. Returns the corrections to keep and the changes,
    each {"field", "old", "new"}, for the history. Nothing is kept unless the
    whole edit passes: a bad value raises ValueError saying which and why.
    """
    now, new, changes = apply(read, corrections), dict(corrections or {}), []
    kind = edit.get("type") or now.get("type")
    if kind != now.get("type"):
        if kind not in CHANGEABLE or now.get("type") not in CHANGEABLE + ("unknown",):
            raise ValueError("A list like this one cannot be turned into another kind of document.")
        changes.append({"field": "Kind of document", "old": TYPE_WORDS.get(now.get("type"), now.get("type")),
                        "new": TYPE_WORDS[kind]})
        new["type"] = kind
    fields, tables = FORMS.get(kind, ([], []))
    for key, raw in (edit.get("fields") or {}).items():
        spec = next((f for f in fields if f[0] == key), None)
        if spec is None:
            raise ValueError(f"{TYPE_WORDS.get(kind, kind)} has no field called {key}.")
        _, label, fkind = spec
        value = clean(fkind, raw, label, (read.get(key) or []) + (now.get(key) or [])
                      if fkind.startswith("refs:") else now.get(key))
        if not _same(fkind, value, now.get(key)):
            changes.append({"field": label, "old": words(fkind, now.get(key)), "new": words(fkind, value)})
            new[key] = value
    for key, rows in (edit.get("tables") or {}).items():
        spec = next((t for t in tables if t[0] == key), None)
        if spec is None or not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError(f"{TYPE_WORDS.get(kind, kind)} has no table like that.")
        _, label, can_add, need, columns = spec
        old, out, kept = now.get(key) or [], [], set()
        for n, sent in enumerate(rows, 1):
            if not isinstance(sent, dict):
                raise ValueError(f"{label}: row {n} is not a row.")
            i = sent.get("_i")
            base = copy.deepcopy(old[i]) if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(old) \
                and i not in kept else None
            if base is None and not can_add:
                raise ValueError(f"{label}: a new row cannot be added here - add them to the file itself, "
                                 "then add the file again.")
            row, cells = base if base is not None else {}, []
            for ckey, clabel, ckind in columns:
                if ckey in sent:
                    value = clean(ckind, sent[ckey], f"{label}, row {n}: {clabel}", row.get(ckey))
                    if not _same(ckind, value, row.get(ckey)):
                        cells.append((clabel, words(ckind, row.get(ckey)), words(ckind, value)))
                        row[ckey] = value
            if not any(row.get(k) for k in need):
                names = " or ".join(next(c[1] for c in columns if c[0] == k).lower() for k in need)
                raise ValueError(f"{label}: row {n} has no {names}.")
            name = next((row.get(k) for k in need if row.get(k)), f"row {n}")
            if base is None:
                changes.append({"field": f"{label}: row added", "old": "",
                                "new": "; ".join(f"{c[1]} {words(c[2], row.get(c[0]))}" for c in columns
                                                 if row.get(c[0]) not in (None, "", []))})
            else:
                kept.add(i)
                changes += [{"field": f"{label}, {name}: {c}", "old": o, "new": v} for c, o, v in cells]
            out.append(row)
        for i, r in enumerate(old):
            if i not in kept:
                changes.append({"field": f"{label}: row removed", "new": "",
                                "old": next((r.get(k) for k in need if r.get(k)), f"row {i + 1}")})
        new[key] = out
    # A value put back to what was read is no longer a correction.
    return {k: v for k, v in new.items() if v != read.get(k)}, changes


# ---------------------------------------------------------------------------
# The form, and the facts the check shows
# ---------------------------------------------------------------------------

def _show(kind, value):
    """A value as its box shows it."""
    if kind.startswith("refs:"):
        num = kind.split(":")[1]
        return ", ".join(r.get(num) or "" for r in value or [] if isinstance(r, dict))
    if kind == "list":
        return ", ".join(value or [])
    if kind == "bool":
        return bool(value)
    return "" if value is None else value


def _options(kind):
    return kind.split(":", 1)[1].split("|") if kind.startswith("choice:") else None


def form(read, corrections, kind=None):
    """What the correcting window shows for one document: each field with its
    value now and what was read, each table's rows, and the kinds it can be
    turned into. `kind` asks for another kind's fields, for a document being
    turned into it."""
    now = apply(read, corrections)
    kind = kind or now.get("type")
    fields, tables = FORMS.get(kind, ([], []))
    done = set(corrected_keys(read, corrections))
    return {
        "type": kind, "type_words": TYPE_WORDS.get(kind, kind), "read_type": read.get("type"),
        "types": [[k, TYPE_WORDS[k]] for k in CHANGEABLE] if now.get("type") in CHANGEABLE + ("unknown",) else [],
        "fields": [{"key": k, "label": label, "kind": fk.split(":")[0], "options": _options(fk),
                    "value": _show(fk, now.get(k)), "read": words(fk, read.get(k)), "corrected": k in done}
                   for k, label, fk in fields],
        "tables": [{"key": k, "label": label, "can_add": can_add, "need": list(need),
                    "columns": [{"key": c, "label": cl, "kind": ck.split(":")[0], "options": _options(ck)}
                                for c, cl, ck in columns],
                    "corrected": k in done,
                    "rows": [{**{c: _show(ck, r.get(c)) for c, _, ck in columns}, "_i": i}
                             for i, r in enumerate(now.get(k) or [])]}
                   for k, label, can_add, need, columns in tables],
        "notes": now.get("notes") or [],
    }


def _money(v, cur=""):
    return f"{cur} {v:,.2f}".strip() if isinstance(v, (int, float)) else None


def facts(doc):
    """The few things the check puts in front of the user for one document:
    the numbers that decide where it is filed, the dates and the money."""
    t, g = doc.get("type"), doc.get
    day = lambda v: words("date", v) if v else None
    out = []
    if t == "sb":
        out += [("Shipping bill", " · ".join(x for x in (g("sb_no"), day(g("sb_date")), g("port")) if x) or None),
                ("Invoices", ", ".join(f"{i.get('inv_no')}" + (f" ({_money(i.get('value_fc'), i.get('currency') or '')})"
                                                               if i.get("value_fc") else "")
                                       for i in g("invoices") or [] if i.get("inv_no")) or None),
                ("Buyer", g("buyer")), ("IGST paid", _money(g("igst_amount"), "₹") if g("igst_amount") else None),
                ("Drawback claimed", _money(g("dbk_amount"), "₹") if g("dbk_amount") else None)]
    elif t == "bl":
        out += [("Bill of lading", g("bl_no")), ("Shipped on board", day(g("sob_date"))),
                ("Invoice numbers", ", ".join(r.get("inv_no") or "" for r in g("invoice_refs") or []) or None),
                ("Shipping bill numbers", ", ".join(r.get("sb_no") or "" for r in g("sb_refs") or []) or None),
                ("Containers", ", ".join(g("containers") or []) or None)]
    elif t == "fira":
        repaid = g("repaid") or []
        out += [("Amount", _money(g("amount_fc"), g("currency") or "")), ("From", g("remitter")),
                ("Date of credit", day(g("credit_date"))), ("Reference", g("ref")),
                ("Invoice named", g("invoice_ref") or "none"),
                ("Purpose code", " · ".join(x for x in (g("purpose_code"), "an advance" if g("advance") else None) if x)
                 or None),
                ("Packing credit repaid", _money(sum(r.get("amount_fc") or 0 for r in repaid), repaid[0].get("currency") or "")
                 + f" ({len(repaid)} loan{'s' if len(repaid) != 1 else ''})" if repaid else None),
                ("Credited to your account", _money(g("credited_fc"), g("currency") or ""))]
    elif t == "invoice":
        out += [("Invoice", " of ".join(x for x in (g("inv_no"), day(g("inv_date"))) if x) or None),
                ("Buyer", g("buyer")), ("Value", _money(g("value_fc"), g("currency") or "")
                                        or _money(g("value_inr"), "₹"))]
    elif t == "gstr1":
        lines = g("lines") or []
        out += [("Return", " · ".join(x for x in (g("period"), g("gstin")) if x) or None),
                ("Table 6A lines", f"{len(lines)}" + (f": {', '.join(l.get('inum') or '' for l in lines[:6])}"
                                                      + (" …" if len(lines) > 6 else "") if lines else ""))]
    elif t in FORMS:
        for key, label, _, need, _ in FORMS[t][1]:
            rows = g(key) or []
            if rows or key == "rows":
                keys = [next((r.get(k) for k in need if r.get(k)), "") for r in rows[:6]]
                out.append((label, f"{len(rows)}" + (f": {', '.join(keys)}" + (" …" if len(rows) > 6 else "")
                                                     if rows else "")))
    return [[label, value] for label, value in out if value]

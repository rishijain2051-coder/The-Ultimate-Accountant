"""Reading one company's books out of Tally into a snapshot: plain data, ready for .ukeep.

Amounts are integer paise with debit > 0 (Tally's XML has debit < 0). Every read goes
through tally_read.send, so it can only ever be a read.

Two ways to read:
    full    the masters, then every voucher from the books' first day, month by month
    quick   from the last snapshot: nothing at all if Tally's alter ids haven't moved;
            otherwise the masters and only the vouchers altered since (one request, any year)
Tally numbers every change it saves (the company's AltVchID / AltMstID, a voucher's AlterID),
which is what lets a quick read ask for just the changes. Deleting a voucher leaves no
number behind, so only a full read notices it gone.

A snapshot:
    {"v": 4, "company": {name, books_from, fy_from, fy_to}, "read_at": iso, "full_read_at": iso,
     "alter": {"vch": int, "mst": int},
     "groups":   [{name, parent, nature}],               nature: assets|liabilities|income|expenses
     "ledgers":  [{name, group, opening, closing}],
     "stock":    [{name, group, unit, closing_qty, closing_value}],
     "vouchers": [{id, alter, date, type, number, narration, optional, party,
                   lines: [{ledger, amount, bills?: [[name, type, credit days, amount]]}],
                   items: [{item, qty, rate, amount}]}],
     "opening_bills": [[ledger, name, date, credit days, amount]],
     "bills":    [{ledger, name, date, due, amount}]}    open bills only; amount > 0 is owed to us
"""

from __future__ import annotations

import datetime as dt
import re
import time
from decimal import Decimal, InvalidOperation

from app.keeper import tally_read as tr


def _text(el, tag: str) -> str:
    return (el.findtext(tag) or el.get(tag) or "").strip().lstrip("\x04").strip()


def _decimal(s: str) -> Decimal:
    # A foreign-currency amount comes as "-$1,200.00 @ ₹ 82.50/$ = -₹ 99,000.00": the rupees
    # are after the "=". Keeping every digit would run the three numbers into one.
    s = s or ""
    s = s.rsplit("=", 1)[-1] if "=" in s else s.split("@", 1)[0]
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return Decimal(s) if s not in ("", "-", ".") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def paise(s: str) -> int:
    """Tally's amount (debit negative) as paise, debit positive."""
    return -int((_decimal(s) * 100).to_integral_value())


def _date(s: str) -> str | None:
    s = (s or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else None


def _days(s: str) -> int:
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else 0


def _int(s: str) -> int:
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else 0


def _yes(s: str) -> bool:
    return s.strip().lower() == "yes"


def fy_bounds(today: dt.date) -> tuple[dt.date, dt.date]:
    start = dt.date(today.year if today.month >= 4 else today.year - 1, 4, 1)
    return start, dt.date(start.year + 1, 3, 31)


def months(start: dt.date, end: dt.date):
    d = start.replace(day=1)
    while d <= end:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(d, start), min(nxt - dt.timedelta(days=1), end)
        d = nxt


def _period(a: dt.date, b: dt.date) -> str:
    # TYPE="Date", as TIB sends it: without it Tally may not take these as a period at all,
    # and answer with every voucher the company has.
    return f'<SVFROMDATE TYPE="Date">{a:%Y%m%d}</SVFROMDATE><SVTODATE TYPE="Date">{b:%Y%m%d}</SVTODATE>'


VOUCHER_FETCH = ["DATE", "VOUCHERTYPENAME", "VOUCHERNUMBER", "NARRATION", "ISCANCELLED", "ISOPTIONAL",
                 "PARTYLEDGERNAME", "MASTERID", "GUID", "ALTERID", "ALLLEDGERENTRIES.LIST",
                 "LEDGERENTRIES.LIST", "ALLINVENTORYENTRIES.LIST"]


class Reader:
    def __init__(self, host: str = "localhost", port: int = 9000, step=None, pause: float = 0.0):
        self.host, self.port = host, port
        self.step = step or (lambda text: None)      # told before each read: if Tally falls over, the last one said
        self.pause = pause                           # between reads, so Tally can keep up with the people using it
        self.how = ""                                # what the last snapshot did, for the Keeper page

    def _read(self, cid: str, type_: str, fetch: list[str], company: str, extra: str = "", tdl: str = "",
              label: str = "", filters: str = ""):
        month = re.search(r">(\d{6})\d\d<", extra) if cid == "KeeperVouchers" else None
        self.step(f"{company}: {label or cid.removeprefix('Keeper').lower()}" + (f" {month.group(1)}" if month else ""))
        out = tr.send(tr.collection(cid, type_, fetch, company, extra, filters=filters, formulae=tdl),
                      self.host, self.port)
        if self.pause:
            time.sleep(self.pause)
        return out

    def companies(self) -> list[dict]:
        self.step("the list of companies")
        root = tr.send(tr.collection("KeeperCompanies", "Company",
                                     ["NAME", "STARTINGFROM", "BOOKSFROM", "ALTVCHID", "ALTMSTID"]),
                       self.host, self.port, read=30.0)
        return [{"name": _text(c, "NAME"), "books_from": _date(_text(c, "BOOKSFROM") or _text(c, "STARTINGFROM")),
                 "alter": {"vch": _int(_text(c, "ALTVCHID")), "mst": _int(_text(c, "ALTMSTID"))}}
                for c in root.iter("COMPANY") if _text(c, "NAME")]

    def snapshot(self, company: dict, today: dt.date | None = None, base: dict | None = None) -> dict:
        """The company's books from its first day to the end of this financial year. With
        base (the last snapshot), a quick read; without, a full one."""
        name = company["name"]
        today = today or dt.date.today()
        fy_from, fy_to = fy_bounds(today)
        start = dt.date.fromisoformat(company["books_from"]) if company.get("books_from") else dt.date(fy_from.year - 3, 4, 1)
        alter = company.get("alter") or {"vch": 0, "mst": 0}
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        # v4: lines read once, foreign-currency amounts in rupees, outstanding as Tally's balances: older copies are read again in full
        quick = bool(base and base.get("v") == 4 and base["company"]["fy_to"] == fy_to.isoformat()
                     and base["company"]["books_from"] == start.isoformat() and alter["vch"] and base["alter"]["vch"])
        self.how = ("" if quick or not base
                    else "read everything once more, with foreign-currency amounts and outstanding put right" if base.get("v") != 4
                    else "read everything: Tally didn't give its change counters" if not alter["vch"]
                    else "read everything: a new financial year, or the books' start moved")
        if quick and alter == base["alter"]:
            self.how = "nothing changed in Tally"
            return {**base, "read_at": now}                # nothing saved in Tally since: nothing to read

        snap = {"v": 4, "read_at": now, "full_read_at": base["full_read_at"] if quick else now, "alter": alter,
                "company": {"name": name, "books_from": start.isoformat(), "fy_from": fy_from.isoformat(),
                            "fy_to": fy_to.isoformat()}}
        snap.update(self._masters(name, _period(start, fy_to)))

        if quick:
            try:
                changed, ignored = self._changed(name, start, fy_to, today, int(base["alter"]["vch"]))
            except tr.TallyUnavailable:
                raise
            except tr.TallyError as exc:                  # it won't take the filter: the long way, said so
                quick = False
                self.how = f"read everything: Tally didn't take the change filter ({exc})"
            else:
                if not changed and alter["vch"] != base["alter"]["vch"]:
                    full = self.snapshot(company, today)   # moved, yet nothing altered: a deletion, which only a full read sees
                    self.how = "read everything: something was deleted in Tally, which only a full read notices"
                    return full
                vouchers = [v for v in base["vouchers"] if v["id"] not in changed]
                vouchers += [self._voucher(v) for v in changed.values() if not _yes(_text(v, "ISCANCELLED"))]
                self.how = (f"{len(changed)} changed voucher{'s' * (len(changed) != 1)}"
                            + (": Tally ignored the change filter and sent every voucher" if ignored else ""))
        if not quick:
            vouchers = []
            for a, b in months(start, fy_to):
                if a > today:
                    break
                for v in self._read("KeeperVouchers", "Voucher", VOUCHER_FETCH, name, _period(a, b)).iter("VOUCHER"):
                    if not _yes(_text(v, "ISCANCELLED")):
                        vouchers.append(self._voucher(v))
            self.how = self.how or "read everything"
        # One voucher once, whatever Tally sent twice (the same voucher in two months' reads):
        # the latest alteration of each id stands.
        once = {}
        for v in vouchers:
            key = v["id"] or id(v)                     # no id from Tally: never merged with another
            if key not in once or v["alter"] >= once[key]["alter"]:
                once[key] = v
        snap["vouchers"] = sorted(once.values(), key=lambda v: (v["date"] or "", v["alter"]))
        snap["bills"] = open_bills(snap)
        return snap

    def _changed(self, name: str, start: dt.date, end: dt.date, today: dt.date, since: int) -> tuple[dict, bool]:
        """Vouchers altered since the alter id given, one financial year at a time, so no one
        request is ever the whole history, even if Tally ignores the filter. Returns them by
        id, and whether Tally ignored the filter (sent ones it shouldn't have)."""
        tdl = f'<SYSTEM TYPE="Formulae" NAME="KeeperAltered">$AlterID &gt; {since}</SYSTEM>'
        changed, ignored = {}, False
        fy = fy_bounds(start)[0]
        while fy <= end and fy <= today:
            a, b = max(start, fy), min(fy_bounds(fy)[1], end)
            root = self._read("KeeperVouchers", "Voucher", VOUCHER_FETCH, name, _period(a, b), filters="KeeperAltered",
                              tdl=tdl, label=f"vouchers changed since the last read, {a.year}-{str(a.year + 1)[2:]}")
            for v in root.iter("VOUCHER"):
                changed[_text(v, "GUID") or _text(v, "MASTERID")] = v
                ignored = ignored or 0 < _int(_text(v, "ALTERID")) <= since
            fy = dt.date(fy.year + 1, 4, 1)
        return changed, ignored

    def _masters(self, name: str, span: str) -> dict:
        groups = []
        for g in self._read("KeeperGroups", "Group", ["NAME", "PARENT", "ISREVENUE", "ISDEEMEDPOSITIVE"],
                            name).iter("GROUP"):
            rev, debit = _yes(_text(g, "ISREVENUE")), _yes(_text(g, "ISDEEMEDPOSITIVE"))
            nature = ("expenses" if debit else "income") if rev else ("assets" if debit else "liabilities")
            groups.append({"name": _text(g, "NAME"), "parent": _text(g, "PARENT"), "nature": nature})

        ledgers, opening_bills = [], []
        for le in self._read("KeeperLedgers", "Ledger",
                             ["NAME", "PARENT", "OPENINGBALANCE", "CLOSINGBALANCE", "BILLALLOCATIONS.LIST"],
                             name, span).iter("LEDGER"):
            lname = _text(le, "NAME")
            ledgers.append({"name": lname, "group": _text(le, "PARENT"),
                            "opening": paise(_text(le, "OPENINGBALANCE")),
                            "closing": paise(_text(le, "CLOSINGBALANCE"))})
            for b in le.findall("BILLALLOCATIONS.LIST"):
                opening_bills.append([lname, _text(b, "NAME"), _date(_text(b, "BILLDATE")),
                                      _days(_text(b, "BILLCREDITPERIOD")), paise(_text(b, "OPENINGBALANCE"))])

        stock = [{"name": _text(s, "NAME"), "group": _text(s, "PARENT"), "unit": _text(s, "BASEUNITS"),
                  "closing_qty": _text(s, "CLOSINGBALANCE"), "closing_value": paise(_text(s, "CLOSINGVALUE"))}
                 for s in self._read("KeeperStock", "StockItem",
                                     ["NAME", "PARENT", "BASEUNITS", "CLOSINGBALANCE", "CLOSINGVALUE"],
                                     name, span).iter("STOCKITEM")]
        return {"groups": groups, "ledgers": ledgers, "stock": stock, "opening_bills": opening_bills}

    @staticmethod
    def _voucher(v) -> dict:
        items = []

        def entry(e):
            line = {"ledger": _text(e, "LEDGERNAME"), "amount": paise(_text(e, "AMOUNT"))}
            bills = [[_text(b, "NAME"), _text(b, "BILLTYPE"), _days(_text(b, "BILLCREDITPERIOD")),
                      paise(_text(b, "AMOUNT"))] for b in e.findall("BILLALLOCATIONS.LIST")]
            if bills:
                line["bills"] = bills
            return line

        every = [entry(e) for e in v.findall("ALLLEDGERENTRIES.LIST")]
        invoice = [entry(e) for e in v.findall("LEDGERENTRIES.LIST")]
        allocated = []
        for it in v.findall("ALLINVENTORYENTRIES.LIST"):
            items.append({"item": _text(it, "STOCKITEMNAME"), "qty": _text(it, "BILLEDQTY") or _text(it, "ACTUALQTY"),
                          "rate": _text(it, "RATE"), "amount": paise(_text(it, "AMOUNT"))})
            allocated += [entry(e) for e in it.findall("ACCOUNTINGALLOCATIONS.LIST")]
        # Tally can put the same lines in more than one of these: an accounting voucher's in
        # ALLLEDGERENTRIES; an invoice's party, tax and round-off in LEDGERENTRIES with the
        # sales or purchase ledger inside each item line; and an export may fill both lists.
        # A voucher always balances, so the first set that does is the voucher, once.
        lines = next((c for c in (every, invoice + allocated, every + allocated, invoice, every + invoice + allocated)
                      if c and sum(x["amount"] for x in c) == 0), every + invoice + allocated)
        return {"id": _text(v, "GUID") or _text(v, "MASTERID"), "alter": _int(_text(v, "ALTERID")),
                "date": _date(_text(v, "DATE")), "type": _text(v, "VOUCHERTYPENAME"),
                "number": _text(v, "VOUCHERNUMBER"), "narration": _text(v, "NARRATION"),
                "optional": _yes(_text(v, "ISOPTIONAL")), "party": _text(v, "PARTYLEDGERNAME"),
                "lines": lines, "items": items}


def _chain(snap: dict):
    parent = {g["name"]: g["parent"] for g in snap["groups"]}

    def up(group: str) -> list[str]:
        out = []
        while group and group not in out and len(out) < 20:
            out.append(group)
            group = parent.get(group, "")
        return out
    return up


def _due(date: str | None, days: int) -> str | None:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat() if date else None


def _by_reference(snap: dict) -> dict[str, list[dict]]:
    """Tally's own bill-by-bill: opening bills plus every voucher's bill allocations, netted by
    (ledger, bill name). On Account rows have no bill to net against and stay as they are."""
    bills: dict[tuple, dict] = {}
    for ledger, name, date, days, amount in snap["opening_bills"]:
        bills[(ledger, name)] = {"ledger": ledger, "name": name, "date": date, "days": days, "amount": amount}
    n = 0
    for v in snap["vouchers"]:
        if v["optional"]:
            continue
        for line in v["lines"]:
            for name, kind, days, amount in line.get("bills", ()):
                n += 1
                key = (line["ledger"], name) if kind.lower() != "on account" else (line["ledger"], f"On Account #{n}")
                b = bills.setdefault(key, {"ledger": line["ledger"], "name": key[1], "date": v["date"], "days": days, "amount": 0})
                if kind.lower() in ("new ref", "advance") and not b.get("set"):
                    b.update(date=v["date"] or b["date"], days=days or b["days"], set=True)
                b["amount"] += amount
    out: dict[str, list[dict]] = {}
    for b in bills.values():
        if b["amount"]:
            out.setdefault(b["ledger"], []).append({"ledger": b["ledger"], "name": b["name"], "date": b["date"],
                                                    "due": _due(b["date"], b["days"]), "amount": b["amount"]})
    return out


def _first_in_first_out(snap: dict, ledger: str, balance: int) -> list[dict]:
    """What makes up a balance when the bill references don't: the latest entries on the
    balance's side are the unpaid ones (the oldest are paid first); what is left is the opening."""
    side = 1 if balance > 0 else -1
    entries = []
    for v in snap["vouchers"]:
        if v["optional"] or not v["date"]:
            continue
        amount = sum(x["amount"] for x in v["lines"] if x["ledger"] == ledger)
        if amount * side > 0:
            days = max([d for x in v["lines"] if x["ledger"] == ledger for _, kind, d, _ in x.get("bills", ())] or [0])
            entries.append((v["date"], abs(amount), days, f"{v['type']} {v['number']}".strip()))
    out, left = [], abs(balance)
    for date, amount, days, name in sorted(entries, reverse=True):
        if left <= 0:
            break
        take = min(amount, left)
        out.append({"ledger": ledger, "name": name, "date": date, "due": _due(date, days), "amount": take * side})
        left -= take
    if left > 0:
        start = snap["company"].get("books_from")
        out.append({"ledger": ledger, "name": "Opening balance", "date": start, "due": start, "amount": left * side})
    return out


def open_bills(snap: dict) -> list[dict]:
    """What each party owes, or is owed, and what it is made of. The total is always Tally's own
    balance for the party, so a party is on one side only and agrees with Tally. Its parts are
    Tally's bill references when they add up to that balance; when they don't (receipts against
    other references, on account, in another currency), the latest entries are taken as unpaid."""
    up = _chain(snap)
    by_ref = _by_reference(snap)
    out = []
    for le in snap["ledgers"]:
        chain = up(le["group"])
        if not le["closing"] or not ({"Sundry Debtors", "Sundry Creditors"} & set(chain)):
            continue
        refs = by_ref.get(le["name"], [])
        # Trusted only when they add up to Tally's balance (within a rupee) and all lie on its
        # side: an unmatched receipt left open beside its invoice is a sign they don't pair up.
        if refs and abs(sum(b["amount"] for b in refs) - le["closing"]) <= 100 \
                and all(b["amount"] * le["closing"] > 0 for b in refs):
            out += refs
        else:
            out += _first_in_first_out(snap, le["name"], le["closing"])
    return sorted(out, key=lambda b: (b["ledger"], b["due"] or ""))

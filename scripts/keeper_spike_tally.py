"""Keeper spikes S3 and S5 (PLAN-keeper.md, section 16): what a real Tally gives.

    python scripts/keeper_spike_tally.py [--host localhost] [--port 9000] [--months 12]

Read-only: every request goes through app.keeper.tally_read, which refuses
anything but an Export. It prints which fields Tally answered and how big the
answers were - never names, figures or parties - so the output can be shared.

S3  are ALTMSTID / ALTVCHID on Company, CLOSINGBALANCE on Ledger, and which
    collection type gives pending bills with their dates and due dates?
S5  how big is a year of vouchers, as Tally's XML and gzipped?
"""

from __future__ import annotations

import argparse
import gzip
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.keeper.tally_read import collection, esc, send  # noqa: E402

VOUCHER_FETCH = ["MASTERID", "DATE", "VOUCHERTYPENAME", "VOUCHERNUMBER", "PARTYLEDGERNAME", "REFERENCE",
                 "NARRATION", "ISOPTIONAL", "ISCANCELLED", "ALLLEDGERENTRIES.LIST", "ALLINVENTORYENTRIES.LIST"]
BILL_FETCH = ["NAME", "PARENT", "BILLDATE", "BILLCREDITPERIOD", "BILLDUEDATE", "OPENINGBALANCE", "CLOSINGBALANCE"]


def tags_answered(root: ET.Element, obj: str, wanted: list[str]) -> dict[str, int]:
    """How many <obj> elements had each wanted field filled in."""
    objs = list(root.iter(obj))
    return {"(objects)": len(objs), **{w: sum(1 for o in objs if (o.findtext(w) or "").strip()) for w in wanted}}


def months(fy_start: date, n: int):
    y, m = fy_start.year, fy_start.month
    for _ in range(n):
        first = date(y, m, 1)
        y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
        last = date.fromordinal(date(y2, m2, 1).toordinal() - 1)
        yield first, last
        y, m = y2, m2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--months", type=int, default=12)
    a = ap.parse_args()
    at = dict(host=a.host, port=a.port)

    cmp_fields = ["NAME", "GUID", "STARTINGFROM", "BOOKSFROM", "ALTMSTID", "ALTVCHID", "LASTVOUCHERDATE"]
    root = send(collection("CCList", "Company", cmp_fields), **at, read=30.0)
    companies = list(root.iter("COMPANY"))
    print(f"S3 Company fields filled, of {len(companies)} loaded:", tags_answered(root, "COMPANY", cmp_fields[1:]))
    today = date.today()
    fy = date(today.year if today.month >= 4 else today.year - 1, 4, 1)

    for i, c in enumerate(companies, 1):
        name = (c.get("NAME") or c.findtext("NAME") or "").strip()
        print(f"\n--- company {i} ---")
        led = ["PARENT", "OPENINGBALANCE", "CLOSINGBALANCE", "ISBILLWISEON", "BILLCREDITPERIOD"]
        r = send(collection("LedList", "Ledger", ["NAME", *led], company=name), **at)
        print("S3 Ledger fields filled:", tags_answered(r, "LEDGER", led))
        for kind in ("Bill", "Bills"):
            try:
                r = send(collection("KeeperBills", kind, BILL_FETCH, company=name), **at)
                tags = {t.tag for t in r.iter()} - {"ENVELOPE", "HEADER", "BODY", "DATA", "COLLECTION", "STATUS"}
                print(f"S3 Bills as TYPE {kind}: object tags {sorted(t for t in tags if t.isupper())[:12]}")
                for obj in ("BILL", "BILLS"):
                    if r.find(f".//{obj}") is not None:
                        print(f"   <{obj}> fields filled:", tags_answered(r, obj, BILL_FETCH[1:]))
            except Exception as exc:  # noqa: BLE001 - a spike reports, it does not stop
                print(f"S3 Bills as TYPE {kind}: {type(exc).__name__}: {str(exc)[:160]}")

        raw = gz = count = 0
        took = time.monotonic()
        for first, last in months(fy, a.months):
            extra = (f'<SVFROMDATE TYPE="Date">{first:%Y%m%d}</SVFROMDATE>'
                     f'<SVTODATE TYPE="Date">{last:%Y%m%d}</SVTODATE>')
            r = send(collection("VchDupList", "Voucher", VOUCHER_FETCH, company=name, extra_vars=extra), **at)
            xml = ET.tostring(r)
            raw, gz, count = raw + len(xml), gz + len(gzip.compress(xml)), count + sum(1 for _ in r.iter("VOUCHER"))
            time.sleep(0.25)
        print(f"S5 {a.months} months from {fy}: {count} vouchers, XML {raw / 1e6:.1f} MB, "
              f"gzipped {gz / 1e6:.2f} MB, read in {time.monotonic() - took:.0f}s "
              f"(the snapshot's JSON is smaller than Tally's XML: this is an upper bound)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

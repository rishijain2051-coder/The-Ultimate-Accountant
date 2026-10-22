"""The viewer's sums, under Node: balances across years, ageing, filters, Dr/Cr.  python tests/test_books_js.py"""

import base64
import json
import re
import subprocess
from pathlib import Path

SRC = (Path(__file__).parents[1] / "public" / "books.js").read_text(encoding="utf-8")
SRC = re.sub(r"^import .*$", "", SRC, flags=re.M)        # pure parts only: no browser here
CAPS_JS = (Path(__file__).parents[1] / "public" / "caps.js").read_text(encoding="utf-8")
SRC = CAPS_JS + "const el = () => null, when = () => '';\n" + SRC

V = lambda id, date, lines, optional=False, **kw: {"id": id, "date": date, "optional": optional, "type": kw.get("type", "Sales"),  # noqa: E731
                                                   "number": id, "narration": kw.get("narration", ""), "party": kw.get("party", ""),
                                                   "lines": [{"ledger": l, "amount": a} for l, a in lines], "items": kw.get("items", [])}
BOOKS = {"company": {"books_from": "2023-04-01", "fy_from": "2025-04-01", "fy_to": "2026-03-31"},
         "groups": [{"name": "Sundry Debtors", "parent": "Primary", "nature": "assets"},
                    {"name": "Bank Accounts", "parent": "Current Assets", "nature": "assets"},
                    {"name": "Sales Accounts", "parent": "Primary", "nature": "income"}],
         "ledgers": [{"name": "Kiran", "group": "Sundry Debtors", "opening": 500000, "closing": 2180000},
                     {"name": "Sales", "group": "Sales Accounts", "opening": 0, "closing": -1180000}],
         "stock": [{"name": "Steel Rod 12mm", "group": "", "unit": "Nos", "closing_qty": "12 Nos", "closing_value": 24000},
                   {"name": "Cement", "group": "", "unit": "Bag", "closing_qty": "3 Bag", "closing_value": 900}],
         "vouchers": [V("y1", "2024-02-10", [("Kiran", 500000), ("Sales", -500000)], party="Kiran"),
                      V("a", "2025-09-05", [("Kiran", 1180000), ("Sales", -1180000)], party="Kiran", narration="12 rods"),
                      V("s", "2025-09-08", [("Cash", -20000), ("Rao", 20000)], party="Rao",
                        items=[{"item": "Steel Rod 12mm", "qty": "10 Nos", "rate": "20/Nos", "amount": 20000}]),
                      V("c", "2025-09-09", [("Kiran", 99), ("Sales", -99)], optional=True, type="Journal")]}
BILLS = [{"ledger": "Kiran", "due": "2025-10-20", "amount": 1180000},
         {"ledger": "Kiran", "due": "2025-06-01", "amount": 500000},
         {"ledger": "Steel Co", "due": "2025-11-01", "amount": -250000}]

CHECK = f"""
const b = {json.dumps(BOOKS)}, bills = {json.dumps(BILLS)};
const all = statement(b, "Kiran", "2023-04-01", "2026-03-31");
const sales = statement(b, "Sales", "2023-04-01", "2026-03-31");
const a = ageing(bills, new Date(2025, 9, 21));
const ids = (f) => filterVouchers(b, f).map(v => v.id);
console.log(JSON.stringify({{
  balances: all.rows.map(r => r.balance), closing: all.closing,
  kiranAtFy24End: balanceAt(b, "Kiran", "2024-03-31"), salesAtFy24End: balanceAt(b, "Sales", "2024-03-31"),
  salesNow: balanceAt(b, "Sales", "2026-03-31"), salesBalances: sales.rows.map(r => r.balance),
  thisYear: statement(b, "Kiran", "2025-04-01", "2026-03-31").opening,
  years: years(b), fy: [fyOf("2025-03-31"), fyOf("2025-04-01")],
  nature: natureOf(b)("Bank Accounts"),
  recv: a.receivable.get("Kiran").buckets, pay: a.payable.get("Steel Co").buckets,
  byAmount: ids({{min: "5000"}}), byRange: ids({{from: "2025-04-01", to: "2025-09-06"}}),
  byType: ids({{type: "Journal"}}), byParty: ids({{party: "kir"}}), byText: ids({{q: "rods"}}), small: ids({{max: "1"}}),
  byItemText: ids({{q: "steel rod"}}), byItem: ids({{item: "Steel Rod 12mm"}}), byItemPart: ids({{item: "rod"}}),
  stockHit: stockMatching(b, "rod").map(s => s.name), stockAll: stockMatching(b, "").length,
  implied: [[...expand(["books"])].sort(), has(["tib"], "tib.post"), has(["tib"], "books.view"), has(["books.view"], "books.sales"),
    has(["con.ai"], "con.recon"), expand(["bogus"]).size],
  screens: ["dues", "accounts", "ledger", "activity", "voucher", "summary", "alerts"].map((n) => allowed(n, ["books.view", "books.balances"])),
  alertsAdmin: allowed("alerts", ["admin"]), salesOnly: ["dues", "activity"].map((n) => allowed(n, ["books.view", "books.sales"])),
  money: [money(123456), money(-50), money(0)],
  moves: itemMoves(b, "Steel Rod 12mm").map(r => [r.v.id, r.n, r.amount]),
  found: (() => {{ const r = findAll(b, "rod"); return [r.ledgers.length, r.stock.map(s => s.name), r.vouchers.map(v => v.id)]; }})(),
  foundParty: findAll(b, "kir").ledgers.map(l => l.name), foundNone: findAll(b, " ").vouchers.length,
  days: [shortDay("2025-09-05", "2025-10-01"), shortDay("2024-02-10", "2025-10-01")],
  firstPeriod: periods()[0], count: plural(2500, "entry", "entries"),
  stockScreens: [allowed("stock", ["books.view", "books.balances"]), allowed("item", ["books.view", "books.balances"]), allowed("item", ["books.view", "books.sales"])],
}}));
"""

import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as d:               # a file, not a data URL: Windows caps a command line's length
    mod = Path(d) / "check.mjs"
    mod.write_text(SRC + CHECK, encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(mod)], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
assert out["balances"] == [1000000, 2180000, None], out             # carried across years; optional: no balance
assert out["closing"] == 2180000 == BOOKS["ledgers"][0]["closing"]
assert out["kiranAtFy24End"] == 1000000 and out["salesAtFy24End"] == -500000, out
assert out["salesNow"] == -1180000, out                             # income starts each year at nothing
assert out["salesBalances"] == [-500000, -1180000, None], out
assert out["thisYear"] == 1000000, out
assert out["years"] == ["2025-04-01", "2024-04-01", "2023-04-01"] and out["fy"] == ["2024-04-01", "2025-04-01"], out
assert out["nature"] == "assets"
assert out["recv"] == [0, 1180000, 0, 0, 500000] and out["pay"] == [-250000, 0, 0, 0, 0], out
assert out["byAmount"] == ["y1", "a"] and out["byRange"] == ["a"] and out["byType"] == ["c"], out
assert out["byParty"] == ["y1", "a", "c"] and out["byText"] == ["a"] and out["small"] == ["c"], out   # c touches Kiran's ledger
assert out["byItemText"] == ["s"] and out["byItem"] == ["s"] and out["byItemPart"] == [], out
assert out["stockHit"] == ["Steel Rod 12mm"] and out["stockAll"] == 2, out
assert out["implied"] == [["books.alerts", "books.balances", "books.sales", "books.view"], True, False, False, False, 0], out
assert out["screens"] == [True, True, True, False, False, True, False], out      # no books.sales: transactions hidden
assert out["alertsAdmin"] is True and out["salesOnly"] == [False, True], out
assert out["money"] == ["1,234.56 Dr", "0.50 Cr", "–"], out
assert out["moves"] == [["s", 10, 20000]], out                      # every year, quantity as a number
assert out["found"] == [0, ["Steel Rod 12mm"], ["s", "a"]], out    # items by name; entries by item or narration ("12 rods"), newest first
assert out["foundParty"] == ["Kiran"] and out["foundNone"] == 0, out
assert out["days"][0] == "5 Sept" and "2024" in out["days"][1], out  # other years say the year
assert out["firstPeriod"] == ["All time", "", ""] and out["count"] == "2,500 entries", out   # no period unless picked
assert out["stockScreens"] == [True, False, True], out

# The service worker: the network first (one deploy's files together), the kept copy when offline,
# and every file the books page imports is kept.
SW = (Path(__file__).parents[1] / "public" / "sw.js").read_text(encoding="utf-8")
pages = re.search(r"const PAGES = \[(.*?)\];", SW, re.S).group(1)
for f in re.findall(r'from "(/[^"]+)"|import\("(/[^"]+)"\)', SRC_ALL := (Path(__file__).parents[1] / "public" / "books.js").read_text(encoding="utf-8")
                    + (Path(__file__).parents[1] / "public" / "app.js").read_text(encoding="utf-8")):
    name = f[0] or f[1]
    assert f'"{name}"' in pages, f"sw.js doesn't keep {name}"
assert "WAIT = 2500" in SW and "Promise.race" in SW and 'SHELL = "ua-shell-v4"' not in SW
print("test_books_js: all checks passed")

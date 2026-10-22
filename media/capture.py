"""
Capture the interface for the guide (media/guide.py).

Starts its own copy of the app on an empty database in a temporary folder, so
the only run on screen is the one built here from the invented KESARI sample
and the real database in data/ is never opened. Then it does what a first-time
user does - picks a check, adds two files, reads the verdict, opens an entry,
ticks it off, adds the bank and a party statement to the same run, downloads,
then opens an export register from the sample documents and walks through it -
and screenshots each step, real figures and all. Sections are shot on their
own (`part`), so their text stays readable on a printed page.

    python media/capture.py
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import synth_exim                    # noqa: E402 - the documents added after the sample
SHOTS = ROOT / "media" / "shots"
POINTER = ROOT / "data-location.txt"            # what app/backend/paths.py reads
SAMPLE = ROOT / "SampleData"
DOWNLOADS = Path(tempfile.mkdtemp(prefix="assurance-guide-downloads-"))

# 1280x800 drawn at 1.5x: sharp enough to print, and a section shot on its own
# stays readable at a page's width.
VIEW = {"width": 1280, "height": 800}
SCALE = 1.5
# The Settings link itself - "Update ready", hidden unless an update waits, goes there too.
SETTINGS = '.cover-nav a[href="#/settings"]:not(#updateReady)'

FILES = {
    "gst": {"tally": "Purchases/Tally/PurchaseRegister.xlsx",
            "gst": "Purchases/GST/MERGED_GSTR2B.xlsx"},
    "bank": {"stmtFile": "Bank/Bank_Statement_Jan-Mar2026.xlsx",
             "bankBook": "Bank/Tally_BankBook_Jan-Mar2026.xlsx"},
    "party": {"theirs": "Statements/DUNGARPUR_FABRICATORS_SOA_FY2526.xlsx",
              "ours": "Statements/Tally_Ledger_DUNGARPUR_FABRICATORS.xlsx"},
}


def serve(data):
    """Start the app on a free port, with its database in `data`."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # The folder is named the way an installed copy names a chosen one, so the
    # screens show what a user sees (Settings can offer to move it); main()
    # removes the note again whatever happens.
    POINTER.write_text(str(data), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "ASSURANCE_DATA_DIR"}
    # A stand-in for the Downloads folder: the pictures must never show - and
    # the app here must never read - what is in the real one.
    env["ASSURANCE_DOWNLOADS_DIR"] = str(DOWNLOADS)
    # Settings then shows what the installed app shows - the result of a real
    # check against the release page - rather than a source copy's "comes to
    # the installed app". Needs a release page set (docs/configuration.md).
    if os.environ.get("UA_UPDATE_REPO"):
        env["ASSURANCE_UPDATE_API"] = "https://api.github.com"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.backend.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(240):
        if proc.poll() is not None:
            sys.exit("The app stopped before it started; the message above says why.")
        try:
            urllib.request.urlopen(f"{url}/api/health", timeout=1)
            return proc, url
        except OSError:
            time.sleep(0.25)
    proc.kill()
    sys.exit("The app did not answer within a minute.")


def shot(page, name):
    page.mouse.move(4, 460)             # off every control, so nothing shows hover
    page.wait_for_timeout(600)
    page.screenshot(path=str(SHOTS / f"{name}.png"))
    print(f"  {name}.png")


def part(page, selector, name):
    """One section on its own, without the sticky cover band over its top."""
    page.add_style_tag(content=".cover, .margin { position: static !important; } .toasts { display: none !important; }")
    page.mouse.move(4, 460)
    page.wait_for_timeout(400)
    page.locator(selector).first.screenshot(path=str(SHOTS / f"{name}.png"))
    print(f"  {name}.png")


def to(page, selector, above=76):
    """Scroll an element up to just under the cover band."""
    page.evaluate("""([sel, above]) => {
        const top = document.querySelector(sel).getBoundingClientRect().top;
        window.scrollTo(0, top + window.scrollY - above);
    }""", [selector, above])


def quiet(page):
    """Dismiss the toasts, as a reader would before moving on. One can fade on
    its own between being found and being clicked."""
    page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")
    page.wait_for_timeout(300)


def choose(page, mode):
    """Pick a check on the start screen and add its two files."""
    page.wait_for_selector(".modes")
    page.click(f'label.mode:has(input[value="{mode}"])')
    for slot, rel in FILES[mode].items():
        page.set_input_files(f'input[data-file="{slot}"]', str(SAMPLE / rel))


def check(page, mode):
    page.click(f'[data-run="{mode}"]')
    page.wait_for_selector("#summary h1.verdict", timeout=300_000)
    page.wait_for_selector("#working", state="hidden")


def open_entry(page, fp):
    page.click(f"#e-{fp} .entry-line")
    page.wait_for_selector(f"#e-{fp} dl.why")
    to(page, f"#e-{fp}", 66)


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    for old in SHOTS.glob("*.png"):
        old.unlink()
    # A folder on its own drive, as a user might choose: the pictures then show
    # no one's user name. A folder of that name that already exists is somebody's,
    # and is never used or removed.
    data = Path(ROOT.anchor) / "Assurance Console data"
    if data.exists():
        data = Path(tempfile.mkdtemp(prefix="assurance-guide-"))
    else:
        data.mkdir()
    server, url = serve(data)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome")
            page = browser.new_page(viewport=VIEW, device_scale_factor=SCALE,
                                    reduced_motion="reduce")
            page.goto(f"{url}/console/#/")
            page.wait_for_selector(".modes")
            shot(page, "01-start")

            choose(page, "gst")
            to(page, ".mode-body", 150)
            shot(page, "02-files")

            check(page, "gst")
            shot(page, "03-verdict")
            quiet(page)

            to(page, "#open")
            shot(page, "04-list")

            # one group from the margin, then its first entry opened
            page.click('[data-pick="Bill number differs"]')
            page.wait_for_timeout(900)
            fp = page.eval_on_selector("#open .entry", "li => li.dataset.fp")
            open_entry(page, fp)
            to(page, "#open .tools-row", 112)     # the hint and the chosen group in view
            shot(page, "05-entry")

            # a ticked entry drops to the end of the list, so follow it there
            page.click(f'#e-{fp} [data-decide="resolved"]')
            page.wait_for_selector(f'#e-{fp}[data-done="1"]')
            to(page, f"#e-{fp}", 66)
            shot(page, "06-ticked")
            quiet(page)

            page.click('[data-act="add-to-run"]')
            choose(page, "bank")
            shot(page, "07-add-bank")

            check(page, "bank")
            shot(page, "08-bank")
            quiet(page)

            dot = page.evaluate("""() => [...document.querySelectorAll('#open .entry')]
                .find(li => li.querySelector('.e-who').textContent.includes('only “.”'))
                ?.dataset.fp""")
            if not dot:
                sys.exit("No bank line has only '.' as its description; "
                         "run tools/synth_bank.py to regenerate the sample.")
            open_entry(page, dot)
            shot(page, "09-dot")

            page.click('[data-act="add-to-run"]')
            choose(page, "party")
            check(page, "party")
            shot(page, "10-party")
            quiet(page)

            page.click('.parts a[href$="/gst"]')
            page.wait_for_selector('.parts a[href$="/gst"][aria-current="page"]')
            to(page, "#all", 80)
            page.click('[data-dl="excel"]')
            page.wait_for_selector('.toast:has-text("Saved to your downloads")',
                                   timeout=120_000)
            shot(page, "11-downloads")
            quiet(page)

            part(page, "#files", "14-files")

            page.click(".cover-nav .gold")
            page.wait_for_selector("#runs")
            to(page, "#runs")
            shot(page, "12-runs")

            page.click(SETTINGS)
            page.wait_for_selector('[data-act="close-app"]')
            if page.locator('[data-act="update-check"]').count():
                page.click('[data-act="update-check"]')
                page.wait_for_function("""() => !/Checking|Downloading/.test(
                    document.querySelector('.version')?.textContent || 'Checking')""", timeout=90_000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            shot(page, "13-settings")

            # the export register, from the sample documents
            page.goto(f"{url}/console/#/exports")
            page.wait_for_selector('[data-act="ex-sample"]')
            # The register's history names who did each step: an invented
            # preparer, not the Windows account of whoever runs this.
            page.evaluate("""() => fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},
                              body: JSON.stringify({preparer: 'A. Preparer'})})""")
            shot(page, "20-ex-start")
            page.click('[data-act="ex-sample"]')
            page.wait_for_selector("#invoices", timeout=300_000)
            page.wait_for_selector("#working", state="hidden")
            quiet(page)
            shot(page, "21-ex-register")
            part(page, "#summary", "22-ex-summary")
            page.locator("[data-inv]", has_text="KE/25-26/004").first.click()
            page.wait_for_selector("tr.inv-file")
            part(page, "tr.inv-file", "23-ex-invoice")
            page.click('[data-pick="Refunds and drawback"]')
            page.wait_for_timeout(900)
            fp = page.eval_on_selector("#open .entry", "li => li.dataset.fp")
            open_entry(page, fp)
            part(page, f"#e-{fp}", "24-ex-entry")
            page.click('[data-pick="GSTR-1 and the refund"]')
            page.wait_for_timeout(900)
            fp = page.evaluate("""() => [...document.querySelectorAll('#open .entry')]
                .find(li => /calls invoice|dates invoice/i.test(li.textContent))?.dataset.fp""")
            if fp:
                open_entry(page, fp)
                part(page, f"#e-{fp}", "25-ex-sb005")
            part(page, "#money", "26-ex-money")
            part(page, "#ebrc", "27-ex-ebrc")
            part(page, "#drafts", "28-ex-drafts")
            part(page, "#docs", "29-ex-docs")

            # An ICEGATE download saved while the app was open: offered when it
            # comes back into view, then turned down so it keeps out of the rest.
            synth_exim.status_download(DOWNLOADS)
            page.wait_for_timeout(3200)
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            page.wait_for_selector(".dl-offer [data-dl-add]", timeout=60_000)
            part(page, ".dl-offer", "35-ex-downloads")
            page.click(".dl-offer [data-dl-skip]")
            page.wait_for_selector(".dl-offer", state="detached")
            quiet(page)

            # Two documents added after the sample: checked before they go in,
            # one corrected, the advice's money placed - then the history.
            extra = Path(tempfile.mkdtemp(prefix="assurance-extra-"))
            page.set_input_files('input[data-file="exdocs"]', [str(p) for p in synth_exim.extras(extra)])
            page.wait_for_selector("#review .check-doc", timeout=300_000)
            page.wait_for_selector("#working", state="hidden")
            quiet(page)
            part(page, "#review", "31-ex-check")
            bl = page.locator("#review .check-doc", has_text="Bill of lading")
            bl.locator("[data-correct]").click()
            page.fill("#docEdit[open] [data-f='invoice_refs']", "KE/25-26/058")
            page.click("#docEdit button[type=submit]")
            page.wait_for_selector("#docEdit", state="hidden")
            quiet(page)
            bl.locator("[data-correct]").click()
            page.wait_for_selector("#docEdit[open] .field.fixed")
            page.wait_for_timeout(400)
            page.locator("#docEdit").screenshot(path=str(SHOTS / "32-ex-correct.png"))
            print("  32-ex-correct.png")
            page.click("#docEdit [data-doc-close]")
            page.click("[data-post-all]")
            page.wait_for_selector("#settle[open] input[data-split]", timeout=120_000)
            page.wait_for_timeout(400)
            page.locator("#settle").screenshot(path=str(SHOTS / "33-ex-settle.png"))
            print("  33-ex-settle.png")
            page.click("#settle [data-settle-save]")
            page.wait_for_selector("#settle", state="hidden")
            quiet(page)
            part(page, "#history", "34-ex-history")
            shutil.rmtree(extra, ignore_errors=True)
            page.click(SETTINGS)
            page.wait_for_selector('[data-act="close-app"]')
            part(page, "section:has(h2:text-is('Exports'))", "30-settings-exports")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=15)
        POINTER.unlink(missing_ok=True)
        shutil.rmtree(data, ignore_errors=True)
        shutil.rmtree(DOWNLOADS, ignore_errors=True)

    print(f"\n{len(list(SHOTS.glob('*.png')))} shots in {SHOTS}")


if __name__ == "__main__":
    main()

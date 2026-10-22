"""
HTTP API.

Every reconciliation is written to SQLite together with the parsed source
records, which is what makes the rest of the app possible: a run can be
recomputed at a different tolerance without re-uploading, an export can be
regenerated days later, and a decision taken on one run carries into the next.
"""

import asyncio
import hashlib
import io
import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.backend import ai, ai_check, exim, exim_drafts, exim_fields, exim_out, exim_read, paths, store, updates
from app.backend.bank import (
    parse_bank_book, parse_bank_statement, reconcile_bank,
)
from app.backend.common import fingerprint, fy_label, serialize
from app.backend.docs import get_ocr, ocr_available, ocr_status, read_document
from app.backend.excel_generator import create_reconciliation_excel
from app.backend.explain import explain_run
from app.backend.exports import (
    build_csv, build_followup_zip, build_party_csv, build_pdf_report,
)
from app.backend.insights import build_insights
from app.backend.parser import (
    merge_multiple_gstr2b, parse_gstr1_file, parse_gstr2b_file, parse_tally_file,
    probe_workbook,
)
from app.backend.exports_recon import (
    build_bank_excel, build_bank_pdf, build_brs_csv, build_recon_csv,
    build_statement_excel, build_statement_pdf,
)
from app.backend.reconciler import STATUS_META, reconcile_data, reconcile_sales
from app.backend.version import VERSION
from app.backend.statements import (
    confirmation_letter, parse_statement_workbook, reconcile_statement,
)

app = FastAPI(title="Ultimate Accountant: Reconciliation and Exports", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = paths.RESOURCE_DIR
FRONTEND_DIR = BASE_DIR / "app" / "frontend"
SAMPLE_DIR = BASE_DIR / "SampleData" / "Purchases"
BANK_SAMPLE_DIR = BASE_DIR / "SampleData" / "Bank"
STMT_SAMPLE_DIR = BASE_DIR / "SampleData" / "Statements"
EXPORTS_SAMPLE_DIR = BASE_DIR / "SampleData" / "Exports"


def _sample_manifest():
    """Who the bundled sample belongs to, as the generator declared it.

    Read rather than hardcoded: the previous default here was a real client's
    GSTIN, which then labelled every sample run and shipped inside the build.
    """
    import json
    try:
        return json.loads(
            (SAMPLE_DIR.parent / "sample.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _fail(status, message):
    raise HTTPException(status_code=status, detail=message)


def _log_path():
    return store.DATA_DIR / "log.txt"                  # follows the data if it is moved


def log_problem(what, exc):
    """Write the traceback somewhere a user can find it.

    The packaged build has no console, so an unhandled error would otherwise
    vanish and leave a 500 with nothing behind it. This file is the only way
    anybody - including whoever wrote it - can find out what happened on a
    machine they cannot reach.
    """
    try:
        with _log_path().open("a", encoding="utf-8") as fh:
            stamp = datetime.now().isoformat(" ", "seconds")
            fh.write("\n" + "=" * 70 + "\n")
            fh.write(f"{stamp}  {what}\n")
            fh.write("=" * 70 + "\n")
            fh.write("".join(traceback.format_exception(exc)))
    except OSError:
        pass
    traceback.print_exception(exc)


@app.exception_handler(Exception)
async def unhandled(request, exc):
    """Nothing should reach here, and when it does it must not be silent."""
    log_problem(f"{request.method} {request.url.path}", exc)
    return JSONResponse(status_code=500, content={
        "detail": f"{type(exc).__name__}: {exc}",
        "log": str(_log_path()),
    })


@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "Ultimate Accountant", "version": app.version,
            "db": store.stats()}


@app.get("/api/bootstrap")
async def bootstrap():
    """Everything the shell needs on first paint.

    Only the run that was last open is loaded. Restoring the newest result of
    each kind instead put one client's GST beside another's bank statement.
    """
    sample_tally = SAMPLE_DIR / "Tally" / "PurchaseRegister.xlsx"
    sample_gst = SAMPLE_DIR / "GST" / "MERGED_GSTR2B.xlsx"
    settings = store.get_settings()
    workspaces = store.list_workspaces()
    last = settings.get("last_workspace") or ""
    if last and not any(w["id"] == last for w in workspaces):
        last = ""
    if not last and workspaces:
        last = workspaces[0]["id"]
    return {
        "settings": settings,
        "workspaces": workspaces,
        "workspace_id": last,
        "workspace": store.get_workspace(last) if last else None,
        "parts": store.latest_parts(last) if last else {},
        "runs": store.list_runs(limit=40, workspace_id=last) if last else [],
        "suppliers": store.get_suppliers(),
        "decisions": store.get_decisions(last),
        "open_items": store.open_items_across_runs(limit=300, workspace_id=last) if last else [],
        "activity": store.recent_activity(20),
        "db": store.stats(),
        "status_meta": STATUS_META,
        "sample_available": sample_tally.exists() and sample_gst.exists(),
        "bank_sample_available": (BANK_SAMPLE_DIR / BANK_SAMPLE[0]).exists()
        and (BANK_SAMPLE_DIR / BANK_SAMPLE[1]).exists(),
        "statement_sample_available": (STMT_SAMPLE_DIR / STMT_SAMPLE[0]).exists()
        and (STMT_SAMPLE_DIR / STMT_SAMPLE[1]).exists(),
        "exports_sample_available": EXPORTS_SAMPLE_DIR.is_dir(),
        "exim_rules": exim.load_rules(),
        "ocr": ocr_status(),
        "server_date": datetime.now().strftime("%Y-%m-%d"),
        "data_folder": _data_folder(),
        "update": _update_status(settings),
    }


# ---------------------------------------------------------------------------
# The data folder: shown in Settings, and movable from there
# ---------------------------------------------------------------------------

def _data_folder():
    return {"path": str(store.DATA_DIR), "bytes": store.data_folder_size(),
            "fixed": bool(os.environ.get("ASSURANCE_DATA_DIR"))}


def _pick_folder():
    """Windows' own folder chooser, on top of the browser. None if cancelled."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.Description = 'Choose the folder Ultimate Accountant keeps your data in'; "
        "$d.ShowNewFolderButton = $true; "
        "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $d.SelectedPath }")
    out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                         capture_output=True, text=True, check=False).stdout.strip()
    return out or None


@app.post("/api/data-folder/move")
async def data_folder_move(body: dict = None):
    """Move every run, register and kept file to a folder the user picks (or
    names), and carry on from there. The installer and uninstaller follow it."""
    if os.environ.get("ASSURANCE_DATA_DIR"):
        _fail(400, "This copy was started with its data folder fixed (ASSURANCE_DATA_DIR), so it cannot be moved.")
    typed = ((body or {}).get("path") or "").strip()
    if not typed and sys.platform != "win32":
        _fail(400, "Choosing a folder works on Windows only; name the folder instead.")
    picked = typed or await asyncio.to_thread(_pick_folder)
    if not picked:
        return {"moved": False, **_data_folder()}
    if not Path(picked).is_absolute():
        _fail(400, "Give the full path of the folder, starting with its drive.")
    target = paths.dedicated(Path(picked))
    await asyncio.to_thread(store.move_data, target)
    return {"moved": True, **_data_folder()}


@app.post("/api/data-folder/reveal")
async def data_folder_reveal():
    if sys.platform != "win32":
        _fail(400, f"Opening folders works on Windows only. Your data is in {store.DATA_DIR}")
    os.startfile(store.DATA_DIR)                                        # noqa: S606
    return {"path": str(store.DATA_DIR)}


# ---------------------------------------------------------------------------
# Runs (workspaces): one client, one period, holding its reconciliations
# ---------------------------------------------------------------------------

def _workspace_for(workspace_id, *, client_name="", gstin="", period=None,
                   fallback_name=""):
    """The run this reconciliation belongs to.

    Without an id a new one is made, which is the default the user asked for:
    a reconciliation starts its own run unless it was explicitly added to one.
    """
    period = period or {}
    if workspace_id:
        if not store.get_workspace(workspace_id):
            _fail(404, "That run no longer exists, so nothing was added to it.")
        store.touch_workspace(
            workspace_id, client_name=client_name, gstin=gstin,
            period_from=period.get("from"), period_to=period.get("to"),
            period_label=period.get("label"))
        return workspace_id
    name = " · ".join(x for x in (client_name or fallback_name,
                                  period.get("label")) if x)
    return store.create_workspace(
        name=name or fallback_name or "New run", client_name=client_name,
        gstin=gstin, period_from=period.get("from"), period_to=period.get("to"))


@app.get("/api/workspaces")
async def list_workspaces(include_archived: bool = Query(False)):
    return {"workspaces": store.list_workspaces(include_archived=include_archived)}


@app.post("/api/workspaces")
async def create_workspace(body: dict = None):
    body = body or {}
    ws = store.create_workspace(
        name=body.get("name"), client_name=body.get("client_name") or "",
        gstin=body.get("gstin") or "", period_from=body.get("period_from"),
        period_to=body.get("period_to"), notes=body.get("notes") or "")
    if body.get("copy_decisions_from"):
        store.copy_decisions(body["copy_decisions_from"], ws)
    return store.get_workspace(ws)


@app.get("/api/workspaces/{ws_id}")
async def get_workspace(ws_id: str):
    ws = store.get_workspace(ws_id)
    if not ws:
        _fail(404, "That run is no longer in the database.")
    ws["parts"] = store.latest_parts(ws_id)
    ws["runs"] = store.list_runs(limit=200, workspace_id=ws_id)
    ws["files"] = _files(ws_id)
    return ws


@app.patch("/api/workspaces/{ws_id}")
async def patch_workspace(ws_id: str, body: dict):
    ws = store.update_workspace(ws_id, **(body or {}))
    if not ws:
        _fail(404, "That run is no longer in the database.")
    return ws


@app.delete("/api/workspaces/{ws_id}")
async def remove_workspace(ws_id: str):
    if not store.delete_workspace(ws_id):
        _fail(404, "That run is no longer in the database.")
    if store.get_settings().get("last_workspace") == ws_id:
        store.save_settings({"last_workspace": ""})
    return {"deleted": ws_id}


@app.post("/api/workspaces/{ws_id}/open")
async def mark_workspace_open(ws_id: str):
    """Remember which run to show next time the app starts."""
    if not store.get_workspace(ws_id):
        _fail(404, "That run is no longer in the database.")
    store.save_settings({"last_workspace": ws_id})
    return {"last_workspace": ws_id}


@app.get("/api/settings")
async def get_settings():
    return store.get_settings()


@app.post("/api/settings")
async def post_settings(patch: dict):
    return store.save_settings(patch or {})


# ---------------------------------------------------------------------------
# Upload inspection
# ---------------------------------------------------------------------------

@app.post("/api/probe")
async def probe(files: List[UploadFile] = File(...)):
    """Tell the user what the app thinks each uploaded workbook is."""
    out = []
    for f in files:
        try:
            info = probe_workbook(await f.read())
            info["filename"] = f.filename
            out.append(info)
        except ValueError as exc:
            out.append({"filename": f.filename, "error": str(exc),
                        "sheets": [], "detected": []})
    return {"files": out}


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _run(gst_parsed, tally_parsed, *, tolerance, fy_year, gstin, entity_name,
         settings, source, sales_parsed=None, gstr1_parsed=None, save=True,
         label=None, workspace_id=None):
    """Shared path for sample, upload and recompute."""
    suppliers = store.get_suppliers()

    ent_name = entity_name or tally_parsed["metadata"].get("entity_name") or "COMPANY"
    period = _period_of(tally_parsed["metadata"])
    date_range = tally_parsed["metadata"].get("date_range") or period["label"] \
        or fy_label(fy_year)
    entity_info = {
        "entity_name": ent_name,
        "gstin": (gstin or "").upper(),
        "fy_start": fy_year,
        "date_range": date_range,
        "period": period,
        "period_from": period["from"] or tally_parsed["metadata"].get("first_date"),
        "period_to": period["to"] or tally_parsed["metadata"].get("last_date"),
        "preparer": settings.get("preparer") or "",
        "address": settings.get("entity_address") or "",
    }
    workspace_id = _workspace_for(
        workspace_id, client_name=ent_name, gstin=entity_info["gstin"],
        period=period, fallback_name=ent_name)
    entity_info["workspace_id"] = workspace_id
    decisions = store.get_decisions(workspace_id)

    result = reconcile_data(
        gst_parsed["records"], tally_parsed["records"],
        tolerance=tolerance, entity_info=entity_info,
        settings=settings, decisions=decisions,
    )

    sales_result = None
    if sales_parsed and gstr1_parsed:
        sales_result = reconcile_sales(
            gstr1_parsed["records"], sales_parsed["records"],
            tolerance=tolerance, entity_info=entity_info, settings=settings,
        )

    insights = build_insights(
        result, gst_parsed["records"], tally_parsed["records"],
        sales_result=sales_result,
        sales_records=gstr1_parsed["records"] if gstr1_parsed else None,
        suppliers=suppliers,
    )

    result["insights"] = insights
    result["sales"] = sales_result
    result["sources_meta"] = {
        "gst": {k: v for k, v in gst_parsed["metadata"].items() if k != "periods"},
        "gst_sections": gst_parsed.get("sections", {}),
        "gst_issues": gst_parsed.get("issues", []),
        "tally": tally_parsed["metadata"],
        "gstr1": gstr1_parsed["metadata"] if gstr1_parsed else None,
        "gstr1_sections": gstr1_parsed.get("sections") if gstr1_parsed else None,
        "sales_tally": sales_parsed["metadata"] if sales_parsed else None,
    }
    result["source_info"] = source

    payload = explain_run(serialize(result))

    if save and store.get_settings().get("auto_save_runs", True):
        # The parsed records ride along so the run can be recomputed later.
        payload_with_sources = dict(payload)
        payload_with_sources["_sources"] = serialize({
            "gst": gst_parsed["records"],
            "tally": tally_parsed["records"],
            "gstr1": gstr1_parsed["records"] if gstr1_parsed else None,
            "sales_tally": sales_parsed["records"] if sales_parsed else None,
        })
        run_id = store.save_run(payload_with_sources, kind="purchase", source=source,
                                settings={**settings, "tolerance": tolerance},
                                label=label or date_range,
                                workspace_id=workspace_id)
        payload["run_id"] = run_id
        payload["saved"] = True
    else:
        payload["run_id"] = None
        payload["saved"] = False

    payload["workspace_id"] = workspace_id
    payload["workspace"] = store.get_workspace(workspace_id)
    return _thin(payload)


def _thin(payload):
    """Strip what the client can derive itself.

    `subsets` holds the same record objects as `all_records`, so sending both
    doubles a multi-megabyte response for nothing; the browser groups by status
    in a single pass. Exports keep reading the full structure from the database.
    """
    out = dict(payload)
    subsets = out.pop("subsets", None) or {}
    out["subset_keys"] = {k: len(v) for k, v in subsets.items()}
    return out


@app.post("/api/load-sample")
async def load_sample(tolerance: float = Query(2.0), fy_year: int = Query(2025),
                      workspace_id: Optional[str] = Query(None)):
    tally_file = SAMPLE_DIR / "Tally" / "PurchaseRegister.xlsx"
    gst_file = SAMPLE_DIR / "GST" / "MERGED_GSTR2B.xlsx"
    if not tally_file.exists():
        _fail(404, f"Sample Tally file not found at {tally_file}")
    if not gst_file.exists():
        _fail(404, f"Sample GST file not found at {gst_file}")

    settings = store.get_settings()
    gst = parse_gstr2b_file(str(gst_file), fy_start_year=fy_year,
                            filename=gst_file.name)
    tally = parse_tally_file(str(tally_file), kind="purchase", filename=tally_file.name)

    payload = _run(
        gst, tally, tolerance=tolerance, fy_year=fy_year,
        gstin=settings.get("gstin") or _sample_manifest().get("gstin", ""),
        entity_name=settings.get("entity_name") or "",
        settings=settings,
        source={"tally_file": tally_file.name,
                "gst_file": f"{gst_file.name} (12 months merged)",
                "tolerance": tolerance, "is_sample": True},
        label="Sample data", workspace_id=workspace_id,
    )
    _keep_sources(payload, "GST purchases", [("Tally", tally_file.name, tally_file.read_bytes()),
                                             ("GSTR-2B", gst_file.name, gst_file.read_bytes())])
    return JSONResponse(content=payload)


@app.post("/api/reconcile")
async def reconcile_uploaded(
    tally_file: UploadFile = File(...),
    gst_files: List[UploadFile] = File(...),
    sales_file: Optional[UploadFile] = File(None),
    gstr1_files: Optional[List[UploadFile]] = File(None),
    tolerance: float = Form(2.0),
    fy_year: Optional[str] = Form(None),
    workspace_id: Optional[str] = Form(None),
    gstin: Optional[str] = Form(""),
    entity_name: Optional[str] = Form(""),
    date_window_days: Optional[int] = Form(None),
    party_threshold: Optional[float] = Form(None),
    probable_threshold: Optional[float] = Form(None),
    value_pct_tolerance: Optional[float] = Form(None),
):
    try:
        settings = store.get_settings()
        for key, val in (("date_window_days", date_window_days),
                         ("party_threshold", party_threshold),
                         ("probable_threshold", probable_threshold),
                         ("value_pct_tolerance", value_pct_tolerance)):
            if val is not None:
                settings[key] = val

        tally = parse_tally_file(await tally_file.read(), kind="purchase",
                                 filename=tally_file.filename)

        # The register says which year it is. Asking the user to pick it again
        # only creates a way to get it wrong, and picking the wrong one
        # silently drops every 2B row outside that year.
        asked = int(fy_year) if str(fy_year or "").strip().isdigit() else None
        detected = tally["metadata"].get("fy_detected")
        fy_year = asked if asked is not None else (detected or 2025)
        fy_note = None
        if asked is not None and detected is not None and asked != detected:
            fy_note = (f"The register is dated {fy_label(detected)}, but the run was "
                       f"set to {fy_label(asked)}. GSTR-2B documents dated outside "
                       f"{fy_label(asked)} are still matched, and marked as outside the year.")

        if len(gst_files) == 1:
            gst = parse_gstr2b_file(await gst_files[0].read(), fy_start_year=fy_year,
                                    filename=gst_files[0].filename)
        else:
            buffers, names = [], []
            for gf in gst_files:
                buffers.append(await gf.read())
                names.append(gf.filename)
            gst = merge_multiple_gstr2b(buffers, fy_start_year=fy_year, filenames=names)

        if not gst["records"]:
            _fail(400, "No GSTR-2B rows could be read. " +
                  (" ".join(gst["issues"]) if gst["issues"] else
                   "Check that the workbook contains a B2B sheet."))
        if not tally["records"]:
            _fail(400, "No Tally vouchers could be read from that export. Check that "
                       "it has Date, Particulars, Vch Type, Vch No., Debit and Credit "
                       "columns.")

        sales = gstr1 = None
        if sales_file is not None and sales_file.filename:
            sales = parse_tally_file(await sales_file.read(), kind="sales",
                                     filename=sales_file.filename)
        if gstr1_files:
            real = [f for f in gstr1_files if f.filename]
            if real:
                merged = {"records": [], "sections": {}, "issues": [], "metadata": {}}
                for f in real:
                    part = parse_gstr1_file(await f.read(), fy_start_year=fy_year,
                                            filename=f.filename)
                    offset = len(merged["records"])
                    for i, rec in enumerate(part["records"]):
                        rec["id"] = offset + i
                    merged["records"].extend(part["records"])
                    merged["issues"].extend(part["issues"])
                    for k, v in part["sections"].items():
                        merged["sections"].setdefault(k, v)
                merged["metadata"] = {
                    "total_count": len(merged["records"]),
                    "total_value": round(sum(r["inv_val"] for r in merged["records"]), 2),
                    "total_taxable": round(sum(r["taxable"] for r in merged["records"]), 2),
                    "total_tax": round(sum(r["tax"] for r in merged["records"]), 2),
                    "igst": round(sum(r["igst"] for r in merged["records"]), 2),
                    "cgst": round(sum(r["cgst"] for r in merged["records"]), 2),
                    "sgst": round(sum(r["sgst"] for r in merged["records"]), 2),
                    "cess": round(sum(r["cess"] for r in merged["records"]), 2),
                }
                gstr1 = merged

        names = [f.filename for f in gst_files]
        source = {
            "tally_file": tally_file.filename,
            "gst_file": (names[0] if len(names) == 1 else
                         f"{len(names)} files ({', '.join(names[:2])}"
                         f"{'...' if len(names) > 2 else ''})"),
            "sales_file": sales_file.filename if sales else None,
            "gstr1_file": ", ".join(f.filename for f in gstr1_files if f.filename)
            if gstr1 else None,
            "tolerance": tolerance,
            "fy_detected": detected,
            "fy_used": fy_year,
            "fy_note": fy_note,
            "is_sample": False,
        }

        payload = _run(
            gst, tally, tolerance=tolerance, fy_year=fy_year, gstin=gstin,
            entity_name=entity_name, settings=settings, source=source,
            sales_parsed=sales, gstr1_parsed=gstr1, workspace_id=workspace_id,
        )
        _keep_sources(payload, "GST purchases", await _uploads(
            ("Tally", tally_file), *[("GSTR-2B", f) for f in gst_files], ("Tally sales", sales_file),
            *[("GSTR-1", f) for f in gstr1_files or []]))
        if fy_note:
            payload.setdefault("notes", []).append(fy_note)
        return JSONResponse(content=payload)

    except HTTPException:
        raise
    except ValueError as exc:
        _fail(400, str(exc))
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        _fail(400, f"Reconciliation failed: {exc}")


@app.post("/api/runs/{run_id}/recompute")
async def recompute(run_id: str, body: dict):
    """Re-match a stored run at different settings, without re-uploading."""
    raw = store.get_run(run_id)
    if not raw:
        _fail(404, "That run is no longer in the database.")
    sources = raw.get("_sources")
    if not sources:
        _fail(400, "This run was saved without its source records, so it cannot be "
                   "recomputed. Re-upload the files.")

    settings = store.get_settings()
    settings.update({k: v for k, v in (body or {}).items() if k != "tolerance"})
    tolerance = float((body or {}).get("tolerance", raw.get("config", {}).get("tolerance", 2.0)))

    gst = {"records": _revive_gst(sources["gst"]),
           "metadata": raw.get("sources_meta", {}).get("gst", {}),
           "sections": raw.get("sources_meta", {}).get("gst_sections", {}),
           "issues": []}
    tally = {"records": _revive_tally(sources["tally"]),
             "metadata": raw.get("sources_meta", {}).get("tally", {})}

    sales = gstr1 = None
    if sources.get("gstr1") and sources.get("sales_tally"):
        gstr1 = {"records": _revive_gst(sources["gstr1"], sales=True),
                 "metadata": raw.get("sources_meta", {}).get("gstr1") or {},
                 "sections": {}, "issues": []}
        sales = {"records": _revive_tally(sources["sales_tally"]),
                 "metadata": raw.get("sources_meta", {}).get("sales_tally") or {}}

    ent = raw.get("entity_info") or {}
    return JSONResponse(content=_run(
        gst, tally, tolerance=tolerance, fy_year=ent.get("fy_start") or 2025,
        gstin=ent.get("gstin"), entity_name=ent.get("entity_name"),
        settings=settings,
        source={**(raw.get("source_info") or {}), "recomputed_from": run_id,
                "tolerance": tolerance},
        sales_parsed=sales, gstr1_parsed=gstr1,
        # A recompute belongs to the run it came from, not a new one.
        workspace_id=raw.get("workspace_id") or ent.get("workspace_id")
        or _workspace_of_run(run_id),
        label=(ent.get("date_range") or "") + " (recomputed)",
    ))


def _workspace_of_run(run_id):
    for r in store.list_runs(limit=500):
        if r["id"] == run_id:
            return r.get("workspace_id")
    return None


def _revive_gst(rows, sales=False):
    """Restore the datetime fields a JSON round-trip flattened to strings."""
    from app.backend.common import to_date
    out = []
    for i, r in enumerate(rows or []):
        rec = dict(r)
        rec["id"] = i
        for field in ("inv_date", "filing_date", "irn_date", "orig_inv_date"):
            if field in rec:
                rec[field] = to_date(rec[field])
        rec.setdefault("flags", [])
        if not sales:
            rec.setdefault("itc_eligible", True)
            rec.setdefault("pct_applicable", 100.0)
        out.append(rec)
    return out


def _revive_tally(rows):
    from app.backend.common import to_date
    out = []
    for i, r in enumerate(rows or []):
        rec = dict(r)
        rec["id"] = i
        rec["date"] = to_date(rec.get("date"))
        rec.setdefault("flags", [])
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

@app.get("/api/runs")
async def list_runs(limit: int = Query(40, ge=1, le=200), kind: Optional[str] = None,
                    workspace_id: Optional[str] = Query(None)):
    return {"runs": store.list_runs(limit=limit, kind=kind, workspace_id=workspace_id)}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    payload = store.get_run(run_id)
    if not payload:
        _fail(404, "Run not found.")
    payload.pop("_sources", None)      # the client never needs the raw records
    payload["saved"] = True
    return JSONResponse(content=_thin(explain_run(payload)))


def _load_ocr_quietly():
    try:
        get_ocr()
    except Exception as exc:                          # noqa: BLE001 - a scan will say so when read
        log_problem("loading OCR", exc)


@app.on_event("startup")
def _warm_ocr():
    """Load the recogniser a few seconds after start, in the background, so the
    first scan read does not also pay the second or two it takes to load."""
    if ocr_available():
        timer = threading.Timer(5.0, _load_ocr_quietly)
        timer.daemon = True
        timer.start()


@app.on_event("startup")
def _start_ai_checks():
    ai.announce()                           # tells the apps sharing the AI where our copy is, if there is one
    ai_check.start(lambda: store.get_settings().get("ai_checks", True) is not False)


@app.middleware("http")
async def _mark_busy(request, call_next):
    """Anything the user asks the app to do holds the AI checker back until it has been quiet a while."""
    if request.method != "GET" and not request.url.path.startswith("/api/ai"):
        ai_check.busy()
    return await call_next(request)


@app.on_event("shutdown")
def _settle_database():
    """Close the database however the program ends.

    The close button routes through here, but so does Ctrl+C and so does the
    window being shut, and a journal left behind by one of those would be
    recovered on the next start rather than settled now.
    """
    ai.stop()
    store.close()


def _close_down():
    """Close the program.

    The response has to reach the page before the process goes, so the exit
    runs on a short delay in its own thread. Every run is already on disk, so
    there is nothing to save first - only the journal to settle. In the app's
    own window, closing the window ends the program (launch.py hangs the way
    to do that on app.state); run in a browser, the signal is what uvicorn
    itself listens for. The hard exit behind both is the fallback, because a
    close button that leaves the program running is worse than none.
    """
    def run():
        time.sleep(0.4)
        store.close()
        close_window = getattr(app.state, "close_window", None)
        try:
            if close_window:
                close_window()
            else:
                signal.raise_signal(signal.SIGINT)
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(2.5)
        os._exit(0)

    threading.Thread(target=run, daemon=True).start()


@app.post("/api/shutdown")
async def shutdown():
    """Close the program from the interface."""
    _close_down()
    return {"closing": True}


# ---------------------------------------------------------------------------
# Updates (app/backend/updates.py): the only thing the app sends online
# ---------------------------------------------------------------------------

def _update_status(settings=None):
    settings = settings or store.get_settings()
    return {**updates.status(), "enabled": settings.get("check_updates", True) is not False}


@app.on_event("startup")
def _start_update_checks():
    updates.start(lambda: store.get_settings().get("check_updates", True) is not False)


@app.get("/api/update")
async def update_status():
    return _update_status()


@app.post("/api/update/check")
async def update_check():
    updates.check_soon()
    return _update_status()


@app.post("/api/update/install")
async def update_install():
    """Hand over to the downloaded installer, then close; it opens the new version."""
    try:
        updates.install()
    except updates.UpdateError as exc:
        _fail(400, str(exc))
    _close_down()
    return {"installing": True}


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    if not store.delete_run(run_id):
        _fail(404, "Run not found.")
    return {"deleted": run_id}


@app.post("/api/runs/{run_id}/pin")
async def pin_run(run_id: str, body: dict):
    store.set_run_pinned(run_id, bool((body or {}).get("pinned")))
    return {"run_id": run_id, "pinned": bool((body or {}).get("pinned"))}


@app.get("/api/runs/{run_id}/months")
async def run_months(run_id: str):
    return {"series": store.run_month_series(run_id)}


@app.get("/api/compare")
async def compare(a: str = Query(...), b: str = Query(...)):
    if not store.get_run(a) or not store.get_run(b):
        _fail(404, "One of those runs is no longer in the database.")
    return store.compare_runs(a, b)


@app.get("/api/items/{fp}/history")
async def item_history(fp: str):
    return {"fp": fp, "history": store.resolution_history(fp)}


@app.get("/api/open-items")
async def open_items(limit: int = Query(300, ge=1, le=2000),
                     workspace_id: Optional[str] = Query(None),
                     all_runs: bool = Query(False)):
    # Confined to one run unless the caller asks across them, so a follow-up
    # list never mixes one client's open items with another's.
    return {"items": store.open_items_across_runs(
        limit=limit, workspace_id=None if all_runs else workspace_id)}


# ---------------------------------------------------------------------------
# Decisions and suppliers
# ---------------------------------------------------------------------------

@app.get("/api/decisions")
async def get_decisions(workspace_id: Optional[str] = Query(None)):
    return {"decisions": store.get_decisions(workspace_id or "")}


@app.post("/api/decisions")
async def post_decision(body: dict):
    fp = (body or {}).get("fp")
    action = (body or {}).get("action")
    if not fp or not action:
        _fail(400, "A decision needs both an item id and an action.")
    try:
        decisions = store.set_decision(
            fp, action,
            counterpart=body.get("counterpart"), note=body.get("note"),
            author=body.get("author"), context=body.get("context"),
            workspace_id=body.get("workspace_id") or "",
        )
    except ValueError as exc:
        _fail(400, str(exc))
    ws = store.get_workspace(body.get("workspace_id") or "")
    if ws and ws.get("module") == "exports" and isinstance(body.get("title"), str):
        # An export register's history names the button pressed and the entry it was about.
        did = str(body.get("did") or "").strip()[:80] or {"resolved": "Marked as done", "ignore": "Taken off the list",
                                                          "follow_up": "Marked as being chased", "match": "Confirmed",
                                                          "clear": "Undone"}.get(action, action)
        _log(ws["id"], "decision", f"{did}: {body['title'][:300]}")
    return {"decisions": decisions}


@app.post("/api/decisions/bulk")
async def post_decisions_bulk(body: dict):
    items = (body or {}).get("items") or []
    if not items:
        _fail(400, "No items supplied.")
    ws = (body or {}).get("workspace_id") or ""
    for item in items:
        if not item.get("fp") or not item.get("action"):
            continue
        store.set_decision(item["fp"], item["action"],
                           counterpart=item.get("counterpart"),
                           note=item.get("note"), author=item.get("author"),
                           context=item.get("context"), workspace_id=ws)
    return {"decisions": store.get_decisions(ws), "applied": len(items)}


@app.get("/api/suppliers")
async def get_suppliers():
    return {"suppliers": store.get_suppliers()}


@app.post("/api/suppliers")
async def post_supplier(body: dict):
    gstin = (body or {}).get("gstin")
    if not gstin:
        _fail(400, "A supplier record needs a GSTIN.")
    return {"suppliers": store.upsert_supplier(
        gstin, name=body.get("name"), contact=body.get("contact"),
        email=body.get("email"), phone=body.get("phone"), notes=body.get("notes"))}



# ---------------------------------------------------------------------------
# Document intake
# ---------------------------------------------------------------------------

BANK_SAMPLE = ("Bank_Statement_Jan-Mar2026.xlsx",
               "Tally_BankBook_Jan-Mar2026.xlsx")
BANK_SAMPLE_SCAN = "Bank_Statement_scanned.pdf"
STMT_SAMPLE = ("DUNGARPUR_FABRICATORS_SOA_FY2526.xlsx",
               "Tally_Ledger_DUNGARPUR_FABRICATORS.xlsx")


@app.get("/api/ocr/status")
async def get_ocr_status(probe: bool = False):
    return ocr_status(probe=probe)


@app.post("/api/docs/read")
async def docs_read(file: UploadFile = File(...), force_ocr: bool = Form(False),
                    max_rows: int = Form(400)):
    """
    Read one document and hand back the grid, so the user can see exactly what
    the app got out of their file before any reconciliation depends on it.
    """
    try:
        data = await file.read()
        out = read_document(data, file.filename, force_ocr=force_ocr)
    except ValueError as exc:
        _fail(400, str(exc))
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        _fail(400, f"Could not read that document: {exc}")

    grid = out["grid"][:max_rows]
    return {
        "filename": file.filename,
        "kind": out["kind"],
        "method": out["method"],
        "confidence": out["confidence"],
        "pages": out["pages"],
        "notes": out["notes"],
        "page_detail": out.get("page_detail"),
        "sheets": sorted(out.get("grids", {}).keys()),
        "rows": len(out["grid"]),
        "truncated": len(out["grid"]) > max_rows,
        "grid": grid,
        "guess": _guess_document(out),
        "text_preview": (out.get("text") or "")[:2000],
    }


def _guess_document(out):
    """Say what the document looks like, so the UI can route it."""
    from app.backend.bank import find_header as bank_header
    from app.backend.statements import _find_statement_header

    grid = out["grid"]
    if not grid:
        return {"looks_like": None, "confidence": "none"}
    head = " ".join(str(c) for r in grid[:12] for c in r).lower()

    bank_idx, bank_cols = bank_header(grid)
    stmt_idx, stmt_cols = _find_statement_header(grid)
    hints = []
    if any(w in head for w in ("narration", "withdrawal", "deposit", "ifsc",
                               "statement of account", "value dt")):
        hints.append("bank")
    if any(w in head for w in ("particulars", "vch type", "voucher", "ledger")):
        hints.append("ledger")
    if "gstin of supplier" in head or "trade/legal" in head:
        hints.append("gstr2b")

    looks_like = None
    if "gstr2b" in hints:
        looks_like = "gstr2b"
    elif bank_idx is not None and "bank" in hints:
        looks_like = "bank_statement"
    elif stmt_idx is not None and "ledger" in hints:
        looks_like = "ledger"
    elif bank_idx is not None:
        looks_like = "bank_statement"
    elif stmt_idx is not None:
        looks_like = "ledger"

    return {
        "looks_like": looks_like,
        "hints": hints,
        "bank_header_row": (bank_idx + 1) if bank_idx is not None else None,
        "bank_columns": sorted(bank_cols.keys()),
        "statement_header_row": (stmt_idx + 1) if stmt_idx is not None else None,
        "statement_columns": sorted(stmt_cols.keys()),
    }


# ---------------------------------------------------------------------------
# Bank reconciliation
# ---------------------------------------------------------------------------

def _run_bank(stmt, book, *, settings, source, label=None, save=True,
              workspace_id=None):
    result = reconcile_bank(
        stmt["transactions"], book["entries"], settings=settings,
        bank_meta=stmt["meta"], book_meta=book["meta"])
    result["issues"] = stmt.get("issues", [])
    result["source_info"] = source
    ent = store.get_settings()
    period = _period_of(stmt["meta"], book["meta"])
    result["period"] = period
    result["entity_info"] = {
        "entity_name": ent.get("entity_name") or "",
        "gstin": ent.get("gstin") or "",
        "preparer": ent.get("preparer") or "",
        "account": stmt["meta"].get("account"),
        "bank": stmt["meta"].get("bank"),
        "period_from": period["from"],
        "period_to": period["to"],
        "date_range": period["label"],
    }
    bank_label = " · ".join(x for x in (stmt["meta"].get("bank"),
                                        stmt["meta"].get("account")) if x)
    workspace_id = _workspace_for(
        workspace_id, client_name=ent.get("entity_name") or "",
        gstin=ent.get("gstin") or "", period=period,
        fallback_name=bank_label or "Bank reconciliation")
    result["entity_info"]["workspace_id"] = workspace_id

    payload = explain_run(serialize(result))
    if save and store.get_settings().get("auto_save_runs", True):
        with_sources = dict(payload)
        with_sources["_sources"] = serialize({
            "bank": stmt["transactions"], "book": book["entries"],
            "bank_meta": stmt["meta"], "book_meta": book["meta"],
        })
        run_id = store.save_run(with_sources, kind="bank", source=source,
                                settings=settings, workspace_id=workspace_id,
                                label=label or " · ".join(
                                    x for x in (bank_label, period["label"]) if x)
                                or result["entity_info"]["date_range"])
        payload["run_id"] = run_id
        payload["saved"] = True
    else:
        payload["run_id"] = None
        payload["saved"] = False
    payload["kind"] = "bank"
    payload["workspace_id"] = workspace_id
    payload["workspace"] = store.get_workspace(workspace_id)
    return payload


def _bank_settings(overrides=None):
    from app.backend.bank import DEFAULTS as BANK_DEFAULTS
    cfg = dict(BANK_DEFAULTS)
    saved = store.get_settings()
    for key in BANK_DEFAULTS:
        if saved.get(f"bank_{key}") is not None:
            cfg[key] = saved[f"bank_{key}"]
    for key, val in (overrides or {}).items():
        if key in BANK_DEFAULTS and val is not None:
            cfg[key] = val
    return cfg


@app.post("/api/bank/reconcile")
async def bank_reconcile(
    statement_file: UploadFile = File(...),
    book_file: UploadFile = File(...),
    force_ocr: bool = Form(False),
    amount_tolerance: Optional[float] = Form(None),
    date_window_days: Optional[int] = Form(None),
    wide_date_window_days: Optional[int] = Form(None),
    workspace_id: Optional[str] = Form(None),
):
    try:
        stmt_doc = read_document(await statement_file.read(),
                                 statement_file.filename, force_ocr=force_ocr)
        book_doc = read_document(await book_file.read(), book_file.filename)
        stmt = parse_bank_statement(stmt_doc["grid"],
                                    filename=statement_file.filename)
        book = parse_bank_book(book_doc["grid"], filename=book_file.filename)
        stmt["issues"] = list(stmt.get("issues", [])) + list(stmt_doc.get("notes", []))

        settings = _bank_settings({
            "amount_tolerance": amount_tolerance,
            "date_window_days": date_window_days,
            "wide_date_window_days": wide_date_window_days,
        })
        payload = _run_bank(stmt, book, settings=settings,
                            workspace_id=workspace_id, source={
            "statement_file": statement_file.filename,
            "book_file": book_file.filename,
            "read_as": stmt_doc["method"],
            "ocr_confidence": stmt_doc["confidence"],
            "is_sample": False,
        })
        _keep_sources(payload, "Bank", await _uploads(("Bank statement", statement_file),
                                                      ("Bank book", book_file)))
        return JSONResponse(content=payload)
    except HTTPException:
        raise
    except ValueError as exc:
        _fail(400, str(exc))
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        _fail(400, f"Bank reconciliation failed: {exc}")


@app.post("/api/bank/load-sample")
async def bank_load_sample(scanned: bool = Query(False),
                           workspace_id: Optional[str] = Query(None)):
    stmt_name = BANK_SAMPLE_SCAN if scanned else BANK_SAMPLE[0]
    stmt_path = BANK_SAMPLE_DIR / stmt_name
    book_path = BANK_SAMPLE_DIR / BANK_SAMPLE[1]
    if not stmt_path.exists() or not book_path.exists():
        _fail(404, f"Sample bank files not found in {BANK_SAMPLE_DIR}")
    try:
        stmt_doc = read_document(stmt_path.read_bytes(), stmt_name)
        book_doc = read_document(book_path.read_bytes(), BANK_SAMPLE[1])
        stmt = parse_bank_statement(stmt_doc["grid"], filename=stmt_name)
        book = parse_bank_book(book_doc["grid"], filename=BANK_SAMPLE[1])
        stmt["issues"] = list(stmt.get("issues", [])) + list(stmt_doc.get("notes", []))
        payload = _run_bank(stmt, book, settings=_bank_settings(),
                            workspace_id=workspace_id, source={
            "statement_file": stmt_name, "book_file": BANK_SAMPLE[1],
            "read_as": stmt_doc["method"], "ocr_confidence": stmt_doc["confidence"],
            "is_sample": True,
        }, label="Sample bank data" + (" (scanned)" if scanned else ""))
        _keep_sources(payload, "Bank", [("Bank statement", stmt_name, stmt_path.read_bytes()),
                                        ("Bank book", BANK_SAMPLE[1], book_path.read_bytes())])
        return JSONResponse(content=payload)
    except ValueError as exc:
        _fail(400, str(exc))


# ---------------------------------------------------------------------------
# Party statement reconciliation
# ---------------------------------------------------------------------------

def _run_statement(theirs, ours, *, relation, settings, source, gst_records=None,
                   label=None, save=True, workspace_id=None):
    result = reconcile_statement(
        theirs["entries"], ours["entries"], settings=settings,
        their_meta=theirs["meta"], our_meta=ours["meta"],
        gst_records=gst_records, relation=relation)

    ent = store.get_settings()
    suppliers = store.get_suppliers()
    gstin = theirs["meta"].get("gstin") or ""
    contact = suppliers.get(gstin, {})
    period = _period_of(theirs["meta"], ours["meta"])
    result["period"] = period
    result["entity_info"] = {
        "entity_name": theirs["meta"].get("company") if theirs["meta"].get("side") == "ours"
        else (ours["meta"].get("company") or ent.get("entity_name") or ""),
        "gstin": ent.get("gstin") or "",
        "preparer": ent.get("preparer") or "",
        "party_name": theirs["meta"].get("party_name") or "",
        "party_gstin": gstin,
        "period_from": period["from"],
        "period_to": period["to"],
        "date_range": period["label"],
    }
    result["letter"] = confirmation_letter(result, entity=result["entity_info"],
                                           contact=contact)
    result["source_info"] = source
    workspace_id = _workspace_for(
        workspace_id, client_name=result["entity_info"]["entity_name"] or "",
        gstin=ent.get("gstin") or "", period=period,
        fallback_name=theirs["meta"].get("party_name") or "Party statement")
    result["entity_info"]["workspace_id"] = workspace_id

    payload = explain_run(serialize(result))
    if save and store.get_settings().get("auto_save_runs", True):
        with_sources = dict(payload)
        with_sources["_sources"] = serialize({
            "theirs": theirs["entries"], "ours": ours["entries"],
            "their_meta": theirs["meta"], "our_meta": ours["meta"],
            "relation": relation,
        })
        run_id = store.save_run(
            with_sources, kind="statement", source=source, settings=settings,
            workspace_id=workspace_id,
            label=label or " · ".join(
                x for x in (theirs["meta"].get("party_name"), period["label"]) if x)
            or "statement")
        payload["run_id"] = run_id
        payload["saved"] = True
    else:
        payload["run_id"] = None
        payload["saved"] = False
    payload["kind"] = "statement"
    payload["workspace_id"] = workspace_id
    payload["workspace"] = store.get_workspace(workspace_id)
    return payload


def _period_of(*metas):
    """
    The period a run covers, taken from the documents themselves.

    A statement prints the period it covers in its heading, and where it does
    not, the entries say. Preferring the printed period keeps a quarter that
    happens to have no entry in its first fortnight from being labelled as
    starting late. The widest span across the documents wins, because a
    reconciliation covers whatever both sides between them cover.
    """
    stated = [(m.get("period_from"), m.get("period_to")) for m in metas
              if m and m.get("period_from") and m.get("period_to")]
    seen = [(m.get("first_date"), m.get("last_date")) for m in metas
            if m and m.get("first_date") and m.get("last_date")]
    source = "stated" if stated else "entries"
    spans = stated or seen
    if not spans:
        return {"from": None, "to": None, "label": "", "source": "none"}
    start, end = min(s for s, _ in spans), max(e for _, e in spans)
    outside = stated and any(
        (m.get("first_date") and m["first_date"] < start)
        or (m.get("last_date") and m["last_date"] > end)
        for m in metas if m)
    return {
        "from": start, "to": end, "source": source,
        "label": f"{start} to {end}",
        "entries_outside": bool(outside),
    }


def _statement_settings(overrides=None):
    from app.backend.statements import DEFAULTS as STMT_DEFAULTS
    cfg = dict(STMT_DEFAULTS)
    saved = store.get_settings()
    for key in STMT_DEFAULTS:
        if saved.get(f"stmt_{key}") is not None:
            cfg[key] = saved[f"stmt_{key}"]
    for key, val in (overrides or {}).items():
        if key in STMT_DEFAULTS and val is not None:
            cfg[key] = val
    return cfg


def _gst_rows_for(gstin):
    """GSTR-2B rows for one supplier, taken from the newest purchase run."""
    if not gstin:
        return None
    for run in store.list_runs(limit=12, kind="purchase"):
        payload = store.get_run(run["id"])
        if not payload:
            continue
        sources = payload.get("_sources") or {}
        rows = [g for g in (sources.get("gst") or [])
                if (g.get("gstin") or "").upper() == gstin.upper()]
        if rows:
            return rows
    return None


@app.post("/api/statement/reconcile")
async def statement_reconcile(
    their_file: UploadFile = File(...),
    our_file: UploadFile = File(...),
    relation: str = Form("supplier"),
    party_name: Optional[str] = Form(""),
    force_ocr: bool = Form(False),
    amount_tolerance: Optional[float] = Form(None),
    date_window_days: Optional[int] = Form(None),
    cross_check_gst: bool = Form(True),
    their_sheet: Optional[str] = Form(None),
    our_sheet: Optional[str] = Form(None),
    workspace_id: Optional[str] = Form(None),
):
    try:
        their_doc = read_document(await their_file.read(), their_file.filename,
                                  force_ocr=force_ocr)
        our_doc = read_document(await our_file.read(), our_file.filename)
        theirs, their_pick = parse_statement_workbook(
            their_doc, "theirs", filename=their_file.filename,
            party_name=party_name or None, sheet=their_sheet)
        # Our export often holds a sheet per ledger, so the party named on
        # their statement is what picks ours - the biggest sheet is somebody
        # else's account.
        ours, our_pick = parse_statement_workbook(
            our_doc, "ours", filename=our_file.filename, sheet=our_sheet,
            match=theirs["meta"].get("party_name"))

        gst_rows = None
        if cross_check_gst and relation == "supplier":
            gst_rows = _gst_rows_for(theirs["meta"].get("gstin"))

        settings = _statement_settings({
            "amount_tolerance": amount_tolerance,
            "date_window_days": date_window_days,
        })
        payload = _run_statement(
            theirs, ours, relation=relation, settings=settings,
            gst_records=gst_rows, workspace_id=workspace_id,
            source={"their_file": their_file.filename,
                    "our_file": our_file.filename,
                    "read_as": their_doc["method"],
                    "ocr_confidence": their_doc["confidence"],
                    "cross_checked_gst": bool(gst_rows),
                    "their_sheet": their_pick, "our_sheet": our_pick,
                    "is_sample": False})
        payload["notes"] = (their_doc.get("notes", [])
                            + theirs["meta"].get("issues", [])
                            + ours["meta"].get("issues", []))
        _keep_sources(payload, "Party statements", await _uploads(("Their statement", their_file),
                                                                  ("Our ledger", our_file)))
        return JSONResponse(content=payload)
    except HTTPException:
        raise
    except ValueError as exc:
        _fail(400, str(exc))
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        _fail(400, f"Statement reconciliation failed: {exc}")


@app.post("/api/statement/load-sample")
async def statement_load_sample(workspace_id: Optional[str] = Query(None)):
    their_path = STMT_SAMPLE_DIR / STMT_SAMPLE[0]
    our_path = STMT_SAMPLE_DIR / STMT_SAMPLE[1]
    if not their_path.exists() or not our_path.exists():
        _fail(404, f"Sample statement files not found in {STMT_SAMPLE_DIR}")
    try:
        theirs, _ = parse_statement_workbook(
            read_document(their_path.read_bytes(), STMT_SAMPLE[0]),
            "theirs", filename=STMT_SAMPLE[0])
        ours, _ = parse_statement_workbook(
            read_document(our_path.read_bytes(), STMT_SAMPLE[1]),
            "ours", filename=STMT_SAMPLE[1],
            match=theirs["meta"].get("party_name"))
        gst_rows = _gst_rows_for(theirs["meta"].get("gstin"))
        payload = _run_statement(
            theirs, ours, relation="supplier", settings=_statement_settings(),
            gst_records=gst_rows, workspace_id=workspace_id,
            source={"their_file": STMT_SAMPLE[0], "our_file": STMT_SAMPLE[1],
                    "read_as": "spreadsheet",
                    "cross_checked_gst": bool(gst_rows), "is_sample": True},
            label="Sample statement")
        _keep_sources(payload, "Party statements", [("Their statement", STMT_SAMPLE[0], their_path.read_bytes()),
                                                    ("Our ledger", STMT_SAMPLE[1], our_path.read_bytes())])
        return JSONResponse(content=payload)
    except ValueError as exc:
        _fail(400, str(exc))


@app.post("/api/statement/letter/{run_id}")
async def statement_letter(run_id: str, body: dict):
    """Rebuild the confirmation letter with the contact details just entered."""
    payload = store.get_run(run_id)
    if not payload or payload.get("kind") != "statement":
        _fail(404, "That statement reconciliation is no longer in the database.")
    payload.pop("_sources", None)
    letter = confirmation_letter(payload, entity=payload.get("entity_info"),
                                  contact=body or {})
    return letter


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def _load_for_export(run_id):
    payload = store.get_run(run_id)
    if not payload:
        _fail(404, "That run is no longer in the database, so it cannot be exported.")
    payload.pop("_sources", None)
    return explain_run(payload)


KIND_TAG = {"purchase": "Reconciliation", "bank": "BankReconciliation",
            "statement": "StatementReconciliation"}


def _filename(payload, ext, tag=""):
    ent = (payload.get("entity_info") or {}).get("entity_name") or "GST"
    kind = payload.get("kind") or "purchase"
    if kind == "statement":
        ent = (payload.get("entity_info") or {}).get("party_name") or ent
    safe = "".join(c if c.isalnum() else "-" for c in ent).strip("-")[:28]
    bits = [safe or "GST", KIND_TAG.get(kind, "Reconciliation")]
    if tag:
        bits.append(tag)
    bits.append(_stamp())
    return "_".join(bits) + ext


@app.get("/api/download/excel/{run_id}")
async def download_excel(run_id: str):
    payload = _load_for_export(run_id)
    kind = payload.get("kind") or "purchase"
    if kind == "bank":
        buf = build_bank_excel(payload)
    elif kind == "statement":
        buf = build_statement_excel(payload)
    else:
        buf = create_reconciliation_excel(payload, insights=payload.get("insights"),
                                          sales=payload.get("sales"))
    return Response(
        content=buf.getvalue(), media_type=XLSX_MIME,
        headers={"Content-Disposition":
                 f'attachment; filename="{_filename(payload, ".xlsx")}"'},
    )


@app.get("/api/download/pdf/{run_id}")
async def download_pdf(run_id: str):
    payload = _load_for_export(run_id)
    kind = payload.get("kind") or "purchase"
    if kind == "bank":
        buf = build_bank_pdf(payload, entity=payload.get("entity_info"))
    elif kind == "statement":
        buf = build_statement_pdf(payload, entity=payload.get("entity_info"))
    else:
        if not payload.get("insights"):
            _fail(400, "This run was saved before the analysis step, so no PDF is "
                       "available. Recompute it first.")
        buf = build_pdf_report(payload, payload["insights"],
                               sales=payload.get("sales"))
    return Response(
        content=buf.getvalue(), media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="{_filename(payload, ".pdf")}"'},
    )


@app.get("/api/download/csv/{run_id}")
async def download_csv(run_id: str, view: str = Query("all")):
    payload = _load_for_export(run_id)
    kind = payload.get("kind") or "purchase"
    if kind in ("bank", "statement"):
        body = (build_brs_csv(payload) if view in ("brs", "bridge")
                else build_recon_csv(payload, kind))
    elif view == "party":
        body = build_party_csv(payload)
    else:
        body = build_csv(payload, view)
    return Response(
        content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{_filename(payload, ".csv", view)}"'},
    )


@app.get("/api/download/followups/{run_id}")
async def download_followups(run_id: str):
    payload = _load_for_export(run_id)
    packets = (payload.get("insights") or {}).get("followups") or []
    if not packets:
        _fail(400, "Nothing outstanding, so there is no follow-up pack to build.")
    buf = build_followup_zip(packets, payload.get("entity_info"))
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{_filename(payload, ".zip", "Followups")}"'},
    )


# ---------------------------------------------------------------------------
# Kept files: every file given to the app, in a folder per run or register
# ---------------------------------------------------------------------------

def _keep_sources(payload, part, files):
    """Keep the files a run was made from, in its folder under the part they
    belong to, so they can be found and handed over later. A run that was not
    saved keeps nothing; a copy that cannot be written does not undo the run."""
    ws, run_id = payload.get("workspace_id"), payload.get("run_id")
    if not (ws and run_id):
        return
    for role, name, data in files:
        try:
            if data:
                store.add_document(ws, name, data, {"type": "source", "part": part, "role": role,
                                                    "run_id": run_id}, folder=part, name=f"{role} - {name}")
        except OSError as exc:
            log_problem(f"keeping {name}", exc)


async def _uploads(*pairs):
    """(role, upload) pairs as (role, name, bytes). The readers have already
    been through each upload, so it is read again from the start."""
    out = []
    for role, f in pairs:
        if f is not None and f.filename:
            await f.seek(0)
            out.append((role, f.filename, await f.read()))
    return out


def _files(ws_id):
    """What the run page lists: each kept file, newest first."""
    docs = store.list_documents(ws_id)
    return {"folder": str(store.group_folder(ws_id) or ""), "items": [
        {k: d.get(k) for k in ("id", "name", "kept_as", "folder", "role", "size", "added_at")}
        for d in reversed(docs)]}


def _kept(ws_id, doc_id):
    if not store.get_workspace(ws_id):
        _fail(404, "That run is no longer in the database.")
    doc = store.get_document(ws_id, doc_id)
    if not doc or not doc["path"].exists():
        _fail(404, "That file is no longer on file.")
    return doc


@app.get("/api/files/{ws_id}/zip")
async def files_zip(ws_id: str, ids: str = Query(""), name: str = Query("")):
    """Several kept files in one zip: all of them in their folders, or the
    ones named (an invoice's papers) side by side."""
    want = {i for i in ids.split(",") if i}
    docs = [store.get_document(ws_id, d["id"]) for d in store.list_documents(ws_id) if not want or d["id"] in want]
    docs = [d for d in docs if d and d["path"].exists()]
    if not docs:
        _fail(404, "There are no kept files to put in a zip.")
    folder = store.group_folder(ws_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in docs:
            inside = d["kept_as"] if want or not folder or not d["path"].is_relative_to(folder)                 else d["path"].relative_to(folder).as_posix()
            z.write(d["path"], inside)
    name = store.safe_name(name or (folder.name if folder else "Files"), 80)
    return Response(content=buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{_ascii(name)}.zip"; filename*=UTF-8\'\'{quote(name)}.zip'})


_INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "text/plain"}


@app.get("/api/files/{ws_id}/{doc_id}")
async def files_get(ws_id: str, doc_id: str, download: bool = Query(False)):
    """A kept file: opened in the browser where it can be (PDFs, images), or
    saved as a copy under its kept name."""
    doc = _kept(ws_id, doc_id)
    # Only inert types open in the browser; anything else (html, svg...) is saved, never run.
    kind = mimetypes.guess_type(doc["kept_as"])[0]
    inline = kind in _INLINE_TYPES and not download
    return FileResponse(doc["path"], filename=doc["kept_as"], media_type=kind if inline else "application/octet-stream",
                        content_disposition_type="inline" if inline else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@app.post("/api/files/{ws_id}/reveal")
async def files_reveal(ws_id: str, doc: str = Query("")):
    """Open the run's folder in File Explorer, with one file picked out if
    asked - the quickest way to drag it into an email. The path comes from
    the database, never from the request."""
    if not store.get_workspace(ws_id):
        _fail(404, "That run is no longer in the database.")
    folder = store.group_folder(ws_id)
    if not folder or not folder.exists():
        _fail(404, "Nothing has been kept for this run yet.")
    if sys.platform != "win32":
        _fail(400, f"Opening folders works on Windows only. The files are in {folder}")
    if doc:
        subprocess.Popen(f'explorer /select,"{_kept(ws_id, doc)["path"]}"')   # noqa: S602 - path from our own records
    else:
        os.startfile(folder)                                                 # noqa: S606
    return {"folder": str(folder)}


def _ascii(text):
    return "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in text)


# ---------------------------------------------------------------------------
# Exports: a register per exporter, each document filed against its invoice
# ---------------------------------------------------------------------------

def _register(ws_id):
    ws = store.get_workspace(ws_id)
    if not ws or ws.get("module") != "exports":
        _fail(404, "That export register is no longer in the database.")
    return ws


def _exim_rules():
    """The rules file, with the choices Settings lets the user make."""
    rules = exim.load_rules()
    s = store.get_settings()
    if s.get("exim_counted_from") in rules["counted_from_choices"]:
        rules["counted_from"] = s["exim_counted_from"]
    if isinstance(s.get("exim_extend_open"), bool):
        rules["extend_open_before_2025_11_14"] = s["exim_extend_open"]
    if isinstance(s.get("exim_due_soon_days"), (int, float)) and 0 <= s["exim_due_soon_days"] <= 365:
        rules["due_soon_days"] = int(s["exim_due_soon_days"])
    return rules


def _exim_docs(ws_id):
    """Every document in a register as it stands: what was read, with the
    user's corrections laid over it, which fields those are, and what the AI
    checker made of it."""
    out, checks = [], store.ai_checks(ws_id)
    for d in store.list_documents(ws_id):
        fixes = d.pop("corrections")
        doc = exim_fields.apply(d, fixes)
        doc["corrected"] = exim_fields.corrected_keys(d, fixes)
        doc["ai"] = ai_check.summary(doc, checks.get(doc["id"]))
        out.append(doc)
    return out


def _register_view(ws_id, **extra):
    """The register worked out afresh from every posted document: a document
    added today can settle a question an older one left open. Documents still
    waiting to be checked are listed with what was read from each and where it
    would be filed - worked out with them in, but kept out of the register."""
    ws = _register(ws_id)
    decisions = store.get_decisions(ws_id)
    rules = _exim_rules()
    docs = _exim_docs(ws_id)
    posted = [d for d in docs if d["status"] == "posted"]
    waiting = [d for d in docs if d["status"] == "pending"]
    view = exim.build(posted, decisions, today=date.today(), rules=rules)
    view["drafts"] = exim_drafts.summary(view, rules["declaration_limit_inr"])
    view["pending"] = []
    if waiting:
        trial = {s["id"]: s for s in exim.build(docs, decisions, today=date.today(), rules=rules)["documents"]}
        view["pending"] = [{**trial[d["id"]], "facts": exim_fields.facts(d), "status": "pending"} for d in waiting]
    view["audit"] = store.list_audit(ws_id, 400)
    return {**view, "workspace_id": ws_id, "workspace": ws, "decisions": decisions,
            "folder": str(store.group_folder(ws_id) or ""), **extra}


def _by():
    """Who the history says did it: the name in Settings, else the Windows account."""
    return store.get_settings().get("preparer") or os.environ.get("USERNAME") or None


def _log(ws_id, what, detail, doc_id=None, changes=None):
    store.audit(ws_id, what, detail, doc_id=doc_id, changes=changes, by=_by())


def _doc_words(doc):
    """A document as the history names it: what it is, and the name it is kept under."""
    words = exim_read.TYPE_WORDS.get(doc.get("type"), "document")
    words = words if words[:2].isupper() else words[0].lower() + words[1:]      # ICEGATE, GSTR-1, DGFT stay as they are
    return f"{words} “{doc.get('kept_as') or doc.get('name')}”"


def _placed_words(doc):
    """Where a posted document went: money is counted against invoices, papers are filed against them."""
    placed, money = doc.get("placed") or [], doc.get("type") in ("fira", "irm_list")
    if placed:
        return f"{'counted against' if money else 'filed against'} {', '.join(placed[:12])}" \
               f"{' and more' if len(placed) > 12 else ''}"
    return "its money is not set against an invoice yet" if money else "it matches no invoice on file yet"


def _name_register(ws, view):
    if view["entity"].get("name"):
        view["workspace"] = store.touch_workspace(
            ws, client_name=view["entity"]["name"], gstin=view["entity"].get("gstin") or "",
            period_label="exports")
    return view


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _letter_entity(view):
    """Who the letters come from: Settings first, then what the documents say."""
    s = store.get_settings()
    ent = view.get("entity") or {}
    return {"name": s.get("entity_name") or ent.get("name"), "address": s.get("entity_address") or "",
            "gstin": s.get("gstin") or ent.get("gstin"), "iec": ent.get("iec"), "signatory": s.get("preparer") or ""}


# What an ICEGATE, DGFT or bank download is read as; only these are picked up from Downloads.
DOWNLOAD_KINDS = {"sb_status", "refund_status", "dbk_pending", "irm_list", "ebrc_list", "edpms"}
_seen_downloads = {}                     # (path, modified, size) -> (sha256, what it was read as)


def _downloads(ws_id):
    """Lists saved to the Downloads folder in the last week - ICEGATE's status
    downloads, DGFT's IRM and eBRC lists, the bank's EDPMS statement - that
    this register does not hold, newest first. Each is named by its fingerprint,
    so only a file found here can be added. The folder is only ever read:
    nothing in it is moved, changed or deleted."""
    folder = paths.downloads_dir()
    try:
        files = sorted(((p, p.stat()) for p in folder.iterdir()
                        if p.suffix.lower() in (".xlsx", ".xls", ".csv") and p.is_file()),
                       key=lambda x: x[1].st_mtime, reverse=True)[:40]
    except OSError:
        return folder, []
    held, week = store.document_hashes(ws_id), time.time() - 7 * 86400
    skipped = set(store.get_settings().get("downloads_skipped") or [])
    out = []
    for p, st in files:
        if st.st_mtime < week or st.st_size > 20 * 1024 * 1024:
            continue
        key = (str(p), st.st_mtime_ns, st.st_size)
        if key not in _seen_downloads:
            try:
                data = p.read_bytes()
                _seen_downloads[key] = (hashlib.sha256(data).hexdigest(), exim_read.read(data, p.name).get("type"))
            except Exception:                              # noqa: BLE001 - a file still being written, or locked
                continue
        sha, kind = _seen_downloads[key]
        if kind in DOWNLOAD_KINDS and sha not in held and sha not in skipped:
            out.append({"id": sha, "name": p.name, "path": p, "type": kind,
                        "type_words": exim_read.TYPE_WORDS.get(kind, kind),
                        "saved": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes")})
    return folder, out


def _file_documents(files, workspace_id, check=True, source=""):
    """Read and keep each document. Without a register named, a new one is
    made - the same separate-by-default rule a reconciliation follows.

    What was read waits to be checked: nothing counts in the register until
    the user has seen it and posted it. The sample is posted straight away (a
    demonstration should not open with sixty documents to check)."""
    ws = workspace_id or ""
    if ws:
        _register(ws)
    read = []
    for name, data in files:
        try:
            meta = exim_read.read(data, name)
        except Exception as exc:                  # noqa: BLE001 - one bad file must not lose the rest
            log_problem(f"reading export document {name}", exc)
            meta = {"type": "unknown", "confidence": 0.0, "notes": [f"It could not be opened ({exc})."]}
        read.append((name, data, meta))
    if not ws:
        ws = store.create_workspace(name="New run", module="exports")
    who = next((m["exporter"] for _, _, m in read if m.get("type") == "sb" and m.get("exporter")), None)
    added, repeats, unread = [], [], []
    for name, data, meta in read:
        kept_as = exim_read.kept_name(meta, name)
        doc_id, fresh = store.add_document(ws, name, data, meta,
                                           folder=exim_read.FOLDERS.get(meta.get("type"), "Other"),
                                           name=kept_as, group=f"{who or 'Export register'} - exports",
                                           status="pending" if check else "posted")
        (added if fresh else repeats).append(name)
        if fresh and meta.get("type") == "unknown":
            unread.append(name)
        if fresh and check:
            _log(ws, "added", f"Added {_doc_words({**meta, 'kept_as': store.file_name(name, kept_as)})}{source}: "
                              "waiting to be checked.", doc_id)
    if added and not check:
        _log(ws, "added", f"Added the {len(added)} sample documents and posted them without a check.")
    return _name_register(ws, _register_view(ws, added=added, repeats=repeats, unread=unread))


@app.post("/api/exports/documents")
async def exports_add(files: List[UploadFile] = File(...), workspace_id: Optional[str] = Form(None)):
    got = [(f.filename or "document", await f.read()) for f in files]
    return _file_documents(got, workspace_id)


@app.post("/api/exports/load-sample")
async def exports_sample(workspace_id: Optional[str] = Query(None)):
    if not EXPORTS_SAMPLE_DIR.is_dir():
        _fail(404, "The sample documents are not installed with this copy.")
    files = [(p.name, p.read_bytes()) for p in sorted(EXPORTS_SAMPLE_DIR.rglob("*")) if p.is_file()]
    return _file_documents(files, workspace_id, check=False)


@app.get("/api/exports/{ws_id}/downloads")
async def exports_downloads(ws_id: str):
    """Lists in the Downloads folder this register could take."""
    _register(ws_id)
    folder, found = _downloads(ws_id)
    return {"folder": str(folder), "files": [{k: v for k, v in f.items() if k != "path"} for f in found]}


@app.post("/api/exports/{ws_id}/downloads")
async def exports_downloads_take(ws_id: str, body: dict):
    """Add one of them - it waits to be checked like any other document - or,
    with `skip`, stop offering it."""
    _register(ws_id)
    body = body or {}
    want = body.get("skip") or body.get("add")
    found = {f["id"]: f for f in _downloads(ws_id)[1]}
    if not isinstance(want, str) or want not in found:
        _fail(404, "That file is no longer in your Downloads folder, or the register has it already.")
    f = found[want]
    if body.get("skip"):
        kept = store.get_settings().get("downloads_skipped") or []
        store.save_settings({"downloads_skipped": [want] + [x for x in kept if x != want][:199]})
        return await exports_downloads(ws_id)
    try:
        data = f["path"].read_bytes()
    except OSError:
        _fail(404, "That file could not be opened - it may still be downloading. Try again in a moment.")
    return _file_documents([(f["name"], data)], ws_id, source=" from your Downloads folder")


@app.get("/api/exports/{ws_id}")
async def exports_view(ws_id: str):
    return _register_view(ws_id)


@app.delete("/api/exports/{ws_id}/documents/{doc_id}")
async def exports_remove(ws_id: str, doc_id: str):
    _register(ws_id)
    doc = next((d for d in _exim_docs(ws_id) if d["id"] == doc_id), None)
    if not doc or not store.delete_document(ws_id, doc_id):
        _fail(404, "That document is no longer on file.")
    _log(ws_id, "removed", f"Removed {_doc_words(doc)}"
         + (" before it was posted." if doc["status"] == "pending" else " from the register."), doc_id)
    return _register_view(ws_id)


def _waiting_or_posted(ws_id, doc_id):
    doc = next((d for d in store.list_documents(ws_id) if d["id"] == doc_id), None)
    if not doc:
        _fail(404, "That document is no longer on file.")
    return doc


@app.post("/api/exports/{ws_id}/post")
async def exports_post(ws_id: str, body: dict):
    """Put checked documents into the register - the ones named, or every one
    waiting. A bank advice posted whose money no document places is listed in
    `ask_settle`, so the screen can ask which invoices it is for."""
    _register(ws_id)
    body = body or {}
    waiting = [d["id"] for d in store.list_documents(ws_id, "pending")]
    ids = waiting if body.get("all") else [i for i in body.get("ids") or [] if isinstance(i, str)]
    before = {d["id"]: d for d in _register_view(ws_id)["pending"]}
    posted = store.post_documents(ws_id, ids)
    if not posted:
        _fail(400, "Nothing is waiting to be posted.")
    for i in posted:
        d = before.get(i) or {}
        _log(ws_id, "posted", f"Checked and posted {_doc_words(d)}: {_placed_words(d)}.", i)
    view = _name_register(ws_id, _register_view(ws_id, posted=posted))
    rules = _exim_rules()
    advices = {i for i in posted if (before.get(i) or {}).get("type") == "fira"}
    view["ask_settle"] = [r["id"] for r in view["remittances"] if set(r["doc_ids"]) & advices
                          and not r["cancelled"] and not r["chosen"] and (r["available_fc"] or 0) > 0.005
                          and exim.settle_options(view, r["id"], rules)["candidates"]]
    return view


@app.get("/api/exports/{ws_id}/documents/{doc_id}/form")
async def exports_doc_form(ws_id: str, doc_id: str, kind: Optional[str] = Query(None)):
    """What the correcting window shows for one document; `kind` asks for
    another kind's fields, for a document being turned into it."""
    _register(ws_id)
    doc = _waiting_or_posted(ws_id, doc_id)
    fixes = doc.pop("corrections")
    if kind and kind not in exim_fields.FORMS:
        _fail(400, "There is no such kind of document.")
    return {**exim_fields.form(doc, fixes, kind), "id": doc_id, "name": doc["name"], "kept_as": doc["kept_as"],
            "status": doc["status"], "read_by": doc.get("read_by"), "confidence": doc.get("confidence")}


@app.post("/api/exports/{ws_id}/documents/{doc_id}/correct")
async def exports_doc_correct(ws_id: str, doc_id: str, body: dict):
    """Keep the user's corrections to what was read from one document, each
    change written into the history with what it was before."""
    _register(ws_id)
    doc = _waiting_or_posted(ws_id, doc_id)
    return _correct(ws_id, doc_id, doc, doc.pop("corrections"), body or {}, "Corrected")


def _correct(ws_id, doc_id, doc, fixes, edit, how):
    """Check one edit, keep it beside what was read, refile the kept copy under
    what it now says it is, and write each change into the history."""
    try:
        new, changes = exim_fields.correct(doc, fixes, edit)
    except ValueError as exc:
        _fail(400, str(exc))
    if not changes:
        return _register_view(ws_id, changes=0)
    now = exim_fields.apply(doc, new)
    store.save_corrections(ws_id, doc_id, new, now.get("type"))
    # Kept under what it really is: a misread number or kind leaves the file's name.
    refiled = store.refile_document(ws_id, doc_id, exim_read.FOLDERS.get(now.get("type"), "Other"),
                                    exim_read.kept_name(now, doc["name"]))
    kept_as = refiled.rsplit("/", 1)[-1] if refiled else doc["kept_as"]
    _log(ws_id, "corrected", f"{how} {_doc_words({**now, 'kept_as': kept_as})}"
         + (" before posting it." if doc["status"] == "pending" else "."), doc_id, changes)
    return _register_view(ws_id, changes=len(changes))


@app.post("/api/exports/{ws_id}/documents/{doc_id}/ai-use")
async def exports_doc_ai_use(ws_id: str, doc_id: str):
    """Put the AI checker's reading in place of the app's where the two differ - as
    the user's own corrections, each in the history, undone like any other."""
    _register(ws_id)
    doc = _waiting_or_posted(ws_id, doc_id)
    fixes = doc.pop("corrections")
    now, row = exim_fields.apply(doc, fixes), store.ai_checks(ws_id).get(doc_id)
    if (ai_check.summary(now, row) or {}).get("state") != "differs":
        _fail(400, "The AI checker has no other reading of this document as it stands now.")
    return _correct(ws_id, doc_id, doc, fixes, ai_check.edit_for(now, row["result"]),
                    "Used the AI checker's reading of")


# ---------------------------------------------------------------------------
# The AI checker (app/backend/ai.py, ai_check.py): an add-on fetched on request
# ---------------------------------------------------------------------------

def _ai_status():
    s = store.get_settings()
    counts = {"agrees": 0, "differs": 0, "waiting": 0, "checking": 0, "failed": 0}
    for w in store.list_workspaces(limit=100000):
        if w.get("module") == "exports":
            for d in _exim_docs(w["id"]):
                state = (d.get("ai") or {}).get("state")
                if state in counts:
                    counts[state] += 1
    return {**ai.status(), "enabled": s.get("ai_checks", True) is not False, "counts": counts}


@app.get("/api/ai")
async def ai_status():
    return _ai_status()


@app.post("/api/ai/get")
async def ai_get():
    """Fetch the AI checker onto this computer, in the background."""
    ai.get_soon()
    return _ai_status()


@app.post("/api/ai/remove")
async def ai_remove():
    try:
        ai.remove()
    except ai.AiError as exc:
        _fail(400, str(exc))
    return _ai_status()


@app.post("/api/exports/{ws_id}/documents/{doc_id}/reread")
async def exports_doc_reread(ws_id: str, doc_id: str):
    """Read a kept file again, for one read before the reader learnt its
    layout. Corrections that still say something the new reading does not
    stay; the document waits to be checked again before it counts."""
    _register(ws_id)
    doc = _waiting_or_posted(ws_id, doc_id)
    fixes = doc.pop("corrections")
    try:
        data = store.get_document(ws_id, doc_id)["path"].read_bytes()
    except OSError:
        _fail(404, "The kept copy of that file is not in its folder any more, so it cannot be read again.")
    meta = json.loads(json.dumps(exim_read.read(data, doc["name"]), default=str))
    kind = fixes.get("type") or meta.get("type")
    fields, tables = exim_fields.FORMS.get(kind, ([], []))
    known = {"type"} | {f[0] for f in fields} | {t[0] for t in tables}
    keep = {k: v for k, v in fixes.items() if k in known and v != meta.get(k)}
    read_before = {k: v for k, v in doc.items() if k not in ("id", "name", "added_at", "size", "kept_as", "folder", "status")}
    if read_before == meta and keep == fixes:
        _log(ws_id, "reread", f"Read {_doc_words(exim_fields.apply(doc, fixes))} again: it reads the same as "
             "before.", doc_id)
        return _register_view(ws_id, changes=0)
    before, now = exim_fields.apply(doc, fixes), exim_fields.apply(meta, keep)
    was, got = dict(exim_fields.facts(before)), dict(exim_fields.facts(now))
    changes = ([{"field": "Kind of document", "old": exim_read.TYPE_WORDS.get(before.get("type"), "?"),
                 "new": exim_read.TYPE_WORDS.get(now.get("type"), "?")}] if before.get("type") != now.get("type") else []) \
        + [{"field": k, "old": was.get(k, "blank"), "new": got.get(k, "blank")}
           for k in dict.fromkeys([*was, *got]) if was.get(k) != got.get(k)]
    store.replace_reading(ws_id, doc_id, meta, keep)
    refiled = store.refile_document(ws_id, doc_id, exim_read.FOLDERS.get(now.get("type"), "Other"),
                                    exim_read.kept_name(now, doc["name"]))
    kept_as = refiled.rsplit("/", 1)[-1] if refiled else doc["kept_as"]
    _log(ws_id, "reread", f"Read {_doc_words({**now, 'kept_as': kept_as})} again: waiting to be checked.", doc_id, changes)
    return _register_view(ws_id, changes=max(len(changes), 1))


def _settle_options(ws_id, rem_id):
    view = _register_view(ws_id)
    options = exim.settle_options(view, rem_id, _exim_rules())
    if options is None:
        _fail(404, "That payment is no longer in the register.")
    return options


@app.get("/api/exports/{ws_id}/settle/{rem_id}")
async def exports_settle_options(ws_id: str, rem_id: str):
    """The invoices one payment could be set against, with a split to start from."""
    return _settle_options(ws_id, rem_id)


@app.post("/api/exports/{ws_id}/settle")
async def exports_settle(ws_id: str, body: dict):
    """Set one payment against the invoices the user chose, in the amounts
    chosen - or, with `forget`, drop the choice and go back to what the
    documents say. The split replaces every pairing confirmed one at a time
    for the same payment, so there is one answer per payment."""
    body = body or {}
    rem_id, splits = body.get("remittance"), body.get("splits") or []
    if not isinstance(rem_id, str) or not isinstance(splits, list) or len(splits) > 1000:
        _fail(400, "Say which payment, and how much of it goes to each invoice.")
    options = _settle_options(ws_id, rem_id)
    try:
        clean = [] if body.get("forget") else exim.check_splits(options, splits)
    except ValueError as exc:
        _fail(400, str(exc))
    for fp, dec in store.get_decisions(ws_id).items():
        ctx = dec.get("context") or {}
        if dec.get("action") == "match" and ctx.get("kind") == "payment" and ctx.get("remittance") == rem_id:
            store.set_decision(fp, "clear", workspace_id=ws_id)
    store.set_decision(fingerprint("EX", "settle", rem_id), "clear" if body.get("forget") else "match",
                       context={"kind": "settle", "remittance": rem_id, "splits": clean}, workspace_id=ws_id)
    m, names = options["remittance"], {c["key"]: c["inv_no"] for c in options["candidates"]}
    money = f"{m['currency']} {m['amount_fc']:,.2f} received on {exim_fields.words('date', m['date'])} " \
            f"from {m['remitter'] or 'a remitter not named'}"
    _log(ws_id, "payment", f"Cleared your split of {money}; it is placed as its documents place it."
         if body.get("forget") else f"Set {money} against " + (
             "; ".join(f"{names[s['invoice']]} ({m['currency']} {s['amount']:,.2f})" for s in clean)
             if clean else "no invoice for now") + ".")
    return _register_view(ws_id)


@app.post("/api/exports/{ws_id}/mark")
async def exports_mark(ws_id: str, body: dict):
    """The user's own yes or no on whether an invoice is in GSTR-1, or its
    IGST refund or drawback came back - for when no document says, or one
    says wrong. No value goes back to what the documents say."""
    body = body or {}
    key, what, value = body.get("invoice"), body.get("what"), body.get("value") or None
    if not isinstance(key, str) or what not in exim.MARKS or value not in ("yes", "no", None):
        _fail(400, "Say which invoice, which question, and yes or no.")
    row = next((r for r in _register_view(ws_id)["invoices"] if r["key"] == key), None)
    if row is None:
        _fail(404, "That invoice is no longer in the register.")
    store.set_decision(fingerprint("EX", "mark", key, what), "mark" if value else "clear",
                       context={"kind": "mark", "invoice": key, "what": what, "value": value},
                       workspace_id=ws_id)
    question = {"gstr1": "Filed in GSTR-1?", "igst": "IGST refund received?", "dbk": "Drawback received?"}[what]
    _log(ws_id, "marked", f"Invoice {row['inv_no']}: {question} "
         + (f"You answered {value}." if value else "Back to what the documents say."))
    return _register_view(ws_id)


@app.get("/api/exports/{ws_id}/download/{what}")
async def exports_download(ws_id: str, what: str):
    view = _register_view(ws_id)
    rules, today, who, drafts = _exim_rules(), date.today(), _letter_entity(view), view["drafts"]
    empty = {"ebrc": (not view["bulk_rows"], "No payment is waiting for an eBRC, so there is nothing to put in the sheet."),
             "sb005": (not drafts["sb005"]["count"], "No shipping bill needs the port route for SB005."),
             "table9a": (not drafts["table9a"]["count"] and not drafts["table9a"]["add"], "Nothing needs amending in GSTR-1."),
             "sb003": (not drafts["sb003"]["count"], "No shipping bill was filed under another GSTIN of the same PAN."),
             "declaration": (not drafts["declaration"]["count"], "No bill qualifies for the declaration."),
             "extension": (not drafts["extension"]["count"], "No bill is overdue or falling due."),
             "lookup": (not drafts["lookup"]["count"], "Every refund and drawback is accounted for.")}
    if empty.get(what, (False,))[0]:
        _fail(400, empty[what][1])
    made = {
        "register": lambda: (exim_out.register_workbook({**view, "audit": store.list_audit(ws_id)}).getvalue(),
                             "ExportRegister", ".xlsx", XLSX_MIME),
        "ebrc": lambda: (exim_out.ebrc_bulk_workbook(view).getvalue(), "eBRC-bulk", ".xlsx", XLSX_MIME),
        "own_format": lambda: (exim_drafts.own_format_workbook(view), "S-BILL-DETAIL", ".xlsx", XLSX_MIME),
        "lookup": lambda: (exim_drafts.lookup_workbook(view), "Look-up-on-ICEGATE", ".xlsx", XLSX_MIME),
        "table9a": lambda: (exim_drafts.table9a_workbook(view), "Table-9A-worksheet", ".xlsx", XLSX_MIME),
        "sb005": lambda: (exim_drafts.sb005_pack(view, who, today, rules["sb005_fee_inr"]), "SB005-pack", ".zip",
                          "application/zip"),
        "sb003": lambda: (exim_drafts.sb003_undertaking(view, who, today), "SB003-undertaking", ".docx", DOCX_MIME),
        "declaration": lambda: (exim_drafts.declaration(view, who, today, rules["declaration_limit_inr"],
                                                        rules["declaration_from"]), "EDPMS-declaration", ".zip",
                                "application/zip"),
        "extension": lambda: (exim_drafts.extension(view, who, today), "Extension-request", ".zip", "application/zip"),
    }.get(what)
    if not made:
        _fail(404, "There is no such download.")
    data, tag, ext, mime = made()
    ent = "".join(c if c.isalnum() else "-" for c in view["entity"].get("name") or "Exports").strip("-")[:28]
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="{ent or "Exports"}_{tag}_{_stamp()}{ext}"'})


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

class NoCacheStatic(StaticFiles):
    """The app is served from disk on localhost; never let the browser hold a
    stale bundle after an edit."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


@app.get("/favicon.ico")
async def favicon():
    icon = FRONTEND_DIR / "assets" / "favicon.svg"
    if icon.exists():
        return FileResponse(icon, media_type="image/svg+xml")
    return Response(status_code=204)


# At /console/: Ultimate Accountant's own page, the choice of module, is at / (app/main.py).
if FRONTEND_DIR.exists():
    app.mount("/console", NoCacheStatic(directory=str(FRONTEND_DIR), html=True),
              name="frontend")

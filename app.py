"""FB EMM local web application.

The browser UI talks to this Flask service. Selenium stays in Python because
WimTech is an internal site and must be controlled from the user's workstation.
"""

from __future__ import annotations

import copy
import csv
import hmac
import io
import json
import os
import sqlite3
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import uuid4

from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from bulk_excel import parse_bulk_workbook, write_bulk_results
from assignment_excel import (
    parse_assignment_workbook, parse_msan_mapping, resolve_msan_spl,
    save_msan_mapping,
)
from config import load_config, save_config
from job_store import JobStore
from pco_logic import parse_spl
from wimtech_assigner import assign_login_to_first_port
from wimtech_bulk_mutator import mutate_bulk_rows
from wimtech_checker import check_all_pcos, lookup_login_msan_port


BASE_DIR = Path(__file__).resolve().parent
LATEST_RESULTS_PATH = BASE_DIR / "data" / "available_pcos.json"
BULK_RESULTS_DIR = BASE_DIR / "data" / "bulk_results"
MSAN_MAPPING_PATH = BASE_DIR / "data" / "msan_spl_mapping.json"
JOB_STORE = JobStore(BASE_DIR / "data" / "jobs.sqlite3", max_jobs=20)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024
jobs: dict[str, dict] = {}
jobs_lock = Lock()


def authentication_enabled() -> bool:
    return bool(os.getenv("APP_PASSWORD", ""))


@app.before_request
def protect_lan_access():
    """Require shared credentials and a non-simple header in LAN mode."""

    password = os.getenv("APP_PASSWORD", "")
    if password:
        username = os.getenv("APP_USERNAME", "fb-emm")
        credentials = request.authorization
        valid = bool(
            credentials
            and hmac.compare_digest(credentials.username or "", username)
            and hmac.compare_digest(credentials.password or "", password)
        )
        if not valid:
            return Response(
                "Authentification requise.",
                401,
                {"WWW-Authenticate": 'Basic realm="FB EMM", charset="UTF-8"'},
            )
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.path.startswith("/api/")
        and request.headers.get("X-Requested-With") != "FB-EMM"
    ):
        return jsonify(ok=False, error="Requête non autorisée."), 403
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'",
    )
    return response


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_dashboard() -> None:
    time.sleep(1.2)
    webbrowser.open("http://127.0.0.1:5055")


def add_log(job_id: str, level: str, message: str) -> None:
    entry = {"time": utc_now(), "level": level, "message": message}
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["logs"].append(entry)
        job["logs"] = job["logs"][-500:]
        job["updated_at"] = entry["time"]


def public_job(job: dict) -> dict:
    safe = {
        key: copy.deepcopy(value)
        for key, value in job.items()
        if key not in {
            "thread", "run_event", "stop_event", "output_path", "bulk_rows"
        }
    }
    safe["available_pcos"] = [
        row for row in safe["results"] if row.get("status") == "AVAILABLE"
    ]
    safe["available_count"] = len(safe["available_pcos"])
    safe["saturated_count"] = sum(
        1 for row in safe["results"] if row.get("status") == "SATURATED"
    )
    safe["not_found_count"] = sum(
        1 for row in safe["results"] if row.get("status") == "NOT_FOUND"
    )
    safe["assigned_result"] = next(
        (row for row in safe["results"] if row.get("status") == "ASSIGNED"),
        None,
    )
    safe["error_count"] = sum(
        1 for row in safe["results"] if row.get("status") in {"ERROR", "UNKNOWN"}
    )
    safe["bulk_success_count"] = sum(
        1 for row in safe["results"] if row.get("status") == "MUTATED"
    )
    safe["bulk_failed_count"] = sum(
        1
        for row in safe["results"]
        if row.get("status") not in {"PENDING", "MUTATED"}
    )
    safe["output_available"] = bool(
        job.get("output_path") and Path(job["output_path"]).exists()
    )
    safe["progress_percent"] = round(
        (safe["completed_count"] / safe["total"]) * 100, 1
    ) if safe["total"] else 0
    return safe


def snapshot_job(job_id: str) -> dict | None:
    with jobs_lock:
        job = jobs.get(job_id)
        snapshot = public_job(job) if job else None
    snapshot = snapshot or JOB_STORE.load(job_id)
    if snapshot:
        snapshot.pop("_output_path", None)
    return snapshot


def persist_completed_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        snapshot = public_job(job)
        snapshot["_output_path"] = job.get("output_path")
    try:
        JOB_STORE.save(job_id, snapshot["kind"], snapshot["updated_at"], snapshot)
    except (OSError, sqlite3.Error) as exc:
        add_log(job_id, "ERROR", f"Historique local non enregistré : {exc}")


def prune_memory_jobs() -> None:
    """Keep active jobs and only the newest completed jobs in memory."""

    with jobs_lock:
        completed = sorted(
            (
                job for job in jobs.values()
                if job.get("status") not in {"QUEUED", "RUNNING", "PAUSED", "STOPPING"}
            ),
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )
        for job in completed[20:]:
            jobs.pop(job["job_id"], None)


def prune_bulk_exports(max_files: int = 20) -> None:
    if not BULK_RESULTS_DIR.exists():
        return
    files = sorted(
        BULK_RESULTS_DIR.glob("bulk_mutation_*.xlsx"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in files[max_files:]:
        try:
            stale.unlink()
        except OSError:
            pass


def persist_available(job_id: str) -> None:
    snapshot = snapshot_job(job_id)
    if not snapshot:
        return
    payload = {
        "job_id": snapshot["job_id"],
        "spl": snapshot["spl"],
        "odf": snapshot["odf"],
        "zr": snapshot["zr"],
        "saved_at": utc_now(),
        "available_pcos": snapshot["available_pcos"],
    }
    LATEST_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LATEST_RESULTS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(LATEST_RESULTS_PATH)


def run_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job["status"] = "RUNNING"
        job["started_at"] = utc_now()
        job["updated_at"] = job["started_at"]
        spl_data = job["spl_data"]
        run_event = job["run_event"]
        stop_event = job["stop_event"]

    def wait_if_paused() -> None:
        while not run_event.wait(timeout=0.2):
            if stop_event.is_set():
                return

    def on_result(index: int, result: dict) -> None:
        with jobs_lock:
            current = jobs.get(job_id)
            if not current:
                return
            current["results"][index] = result
            current["completed_count"] = sum(
                1 for row in current["results"] if row.get("status") != "PENDING"
            )
            current["updated_at"] = utc_now()
        persist_available(job_id)

    try:
        check_all_pcos(
            config=load_config(),
            odf=spl_data["odf"],
            zr=spl_data["zr"],
            candidates=spl_data["pco_candidates"],
            wait_if_paused=wait_if_paused,
            is_stopped=stop_event.is_set,
            on_log=lambda level, message: add_log(job_id, level, message),
            on_result=on_result,
        )
        with jobs_lock:
            current = jobs[job_id]
            current["status"] = "STOPPED" if stop_event.is_set() else "COMPLETED"
            current["finished_at"] = utc_now()
            current["updated_at"] = current["finished_at"]
    except Exception as exc:
        add_log(job_id, "ERROR", f"Erreur générale : {exc}")
        with jobs_lock:
            current = jobs[job_id]
            current["status"] = "ERROR"
            current["error"] = str(exc)
            current["finished_at"] = utc_now()
            current["updated_at"] = current["finished_at"]
    finally:
        persist_available(job_id)
        persist_completed_job(job_id)
        prune_memory_jobs()


def run_assignment_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job["status"] = "RUNNING"
        job["started_at"] = utc_now()
        job["updated_at"] = job["started_at"]
        spl_data = job["spl_data"]
        login = job["login"]
        stop_event = job["stop_event"]

    def on_result(index: int, result: dict) -> None:
        with jobs_lock:
            current = jobs.get(job_id)
            if not current:
                return
            current["results"][index] = result
            current["completed_count"] = sum(
                1 for row in current["results"] if row.get("status") != "PENDING"
            )
            current["updated_at"] = utc_now()

    try:
        assign_login_to_first_port(
            config=load_config(),
            login=login,
            odf=spl_data["odf"],
            zr=spl_data["zr"],
            candidates=spl_data["pco_candidates"],
            is_stopped=stop_event.is_set,
            on_log=lambda level, message: add_log(job_id, level, message),
            on_result=on_result,
        )
        with jobs_lock:
            current = jobs[job_id]
            if any(row.get("status") == "ASSIGNED" for row in current["results"]):
                current["status"] = "COMPLETED"
            elif any(
                row.get("status") == "MUTATION_UNKNOWN"
                for row in current["results"]
            ):
                current["status"] = "REVIEW_REQUIRED"
            elif stop_event.is_set():
                current["status"] = "STOPPED"
            else:
                current["status"] = "COMPLETED"
            current["finished_at"] = utc_now()
            current["updated_at"] = current["finished_at"]

    except Exception as exc:
        add_log(job_id, "ERROR", f"Erreur générale : {exc}")
        with jobs_lock:
            current = jobs[job_id]
            current["status"] = "ERROR"
            current["error"] = str(exc)
            current["finished_at"] = utc_now()
            current["updated_at"] = current["finished_at"]
    finally:
        persist_completed_job(job_id)
        prune_memory_jobs()


def process_batch_assignment_row(job_id: str, row: dict, stop_event: Event) -> dict:
    if row.get("validation_error"):
        return {**row, "status": "INVALID", "status_label": "Ligne invalide", "message": row["validation_error"]}
    if not row.get("spl"):
        msan_port = lookup_login_msan_port(load_config(), row["login"])
        resolved_spl = resolve_msan_spl(MSAN_MAPPING_PATH, msan_port)
        if not resolved_spl:
            raise ValueError(f"Aucun SPL configuré pour le port MSAN {msan_port}.")
        row["msan_port"], row["spl"] = msan_port, resolved_spl
    spl_data = parse_spl(row["spl"]).to_dict()
    detail_results = []
    add_log(job_id, "INFO", f"Ligne {row['excel_row']} : affectation {row['login']} / {row['spl']}")
    assigned = assign_login_to_first_port(
        config=load_config(), login=row["login"], odf=spl_data["odf"],
        zr=spl_data["zr"], candidates=spl_data["pco_candidates"],
        is_stopped=stop_event.is_set,
        on_log=lambda level, message: add_log(job_id, level, message),
        on_result=lambda _i, item: detail_results.append(item),
    )
    uncertain = next((item for item in detail_results if item.get("status") == "MUTATION_UNKNOWN"), None)
    if assigned:
        return {**row, "status": "ASSIGNED", "status_label": "Affecté", "pco": assigned["pco"], "selected_port": assigned["selected_port"], "message": assigned["message"]}
    if uncertain:
        return {**row, "status": "MUTATION_UNKNOWN", "status_label": "À confirmer", "pco": uncertain.get("pco"), "selected_port": uncertain.get("selected_port"), "message": uncertain["message"]}
    return {**row, "status": "NO_PORT", "status_label": "Aucun port", "pco": None, "selected_port": None, "message": "Aucun port utilisable trouvé."}


def run_batch_assignment_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job["status"] = "RUNNING"
        job["started_at"] = utc_now()
        rows = copy.deepcopy(job["assignment_rows"])
        stop_event = job["stop_event"]

    try:
        for index, row in enumerate(rows):
            if stop_event.is_set():
                break
            started = time.monotonic()
            try:
                result = process_batch_assignment_row(job_id, row, stop_event)
            except Exception as exc:
                result = {**row, "status": "ERROR", "status_label": "Erreur", "pco": None, "selected_port": None, "message": str(exc)}
                add_log(job_id, "ERROR", f"Ligne {row['excel_row']} : {exc}")
            result["duration_seconds"] = round(time.monotonic() - started, 2)
            result["checked_at"] = utc_now()
            with jobs_lock:
                jobs[job_id]["results"][index] = result
                jobs[job_id]["completed_count"] = index + 1
                jobs[job_id]["updated_at"] = result["checked_at"]
            if result["status"] == "MUTATION_UNKNOWN":
                break
        with jobs_lock:
            current = jobs[job_id]
            if any(row.get("status") == "MUTATION_UNKNOWN" for row in current["results"]):
                current["status"] = "REVIEW_REQUIRED"
            else:
                current["status"] = "STOPPED" if stop_event.is_set() else "COMPLETED"
            current["finished_at"] = utc_now()
            current["updated_at"] = current["finished_at"]
    except Exception as exc:
        add_log(job_id, "ERROR", f"Erreur affectation en lot : {exc}")
        with jobs_lock:
            jobs[job_id]["status"] = "ERROR"
            jobs[job_id]["error"] = str(exc)
            jobs[job_id]["finished_at"] = utc_now()
    finally:
        persist_completed_job(job_id)
        prune_memory_jobs()

def run_bulk_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job["status"] = "RUNNING"
        job["started_at"] = utc_now()
        job["updated_at"] = job["started_at"]
        rows = copy.deepcopy(job["bulk_rows"])
        stop_event = job["stop_event"]

    def on_result(index: int, result: dict) -> None:
        with jobs_lock:
            current = jobs.get(job_id)
            if not current:
                return
            current["results"][index] = result
            current["completed_count"] = sum(
                1 for row in current["results"] if row.get("status") != "PENDING"
            )
            current["updated_at"] = utc_now()

    try:
        mutate_bulk_rows(
            config=load_config(),
            rows=rows,
            is_stopped=stop_event.is_set,
            on_log=lambda level, message: add_log(job_id, level, message),
            on_result=on_result,
        )
        with jobs_lock:
            current = jobs[job_id]
            results = copy.deepcopy(current["results"])

        output_path = BULK_RESULTS_DIR / f"bulk_mutation_{job_id}.xlsx"
        try:
            write_bulk_results(output_path, rows, results)
            prune_bulk_exports()
            with jobs_lock:
                jobs[job_id]["output_path"] = str(output_path)
        except Exception as exc:
            add_log(job_id, "ERROR", f"Export Excel impossible : {exc}")
            with jobs_lock:
                jobs[job_id]["output_error"] = str(exc)

        with jobs_lock:
            current = jobs[job_id]
            if any(
                row.get("status") == "MUTATION_UNKNOWN"
                for row in current["results"]
            ):
                current["status"] = "REVIEW_REQUIRED"
            elif stop_event.is_set():
                current["status"] = "STOPPED"
            else:
                current["status"] = "COMPLETED"
            current["finished_at"] = utc_now()
            current["updated_at"] = current["finished_at"]
    except Exception as exc:
        add_log(job_id, "ERROR", f"Erreur générale Bulk : {exc}")
        with jobs_lock:
            current = jobs[job_id]
            current["status"] = "ERROR"
            current["error"] = str(exc)
            current["finished_at"] = utc_now()
            current["updated_at"] = current["finished_at"]
    finally:
        persist_completed_job(job_id)
        prune_memory_jobs()


@app.get("/")
def index():
    return render_template("index.html")


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(_exc):
    return jsonify(ok=False, error="Le fichier dépasse la limite de 15 Mo."), 413


@app.get("/api/health")
def health():
    return jsonify(ok=True, service="FB EMM", time=utc_now())


@app.post("/api/generate-pcos")
def generate_pcos():
    payload = request.get_json(silent=True) or {}
    try:
        result = parse_spl(payload.get("spl", ""))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(ok=True, **result.to_dict())


@app.get("/api/config")
def get_config():
    return jsonify(ok=True, config=load_config())


@app.post("/api/config")
def update_config():
    try:
        config = save_config(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(ok=True, config=config)


@app.post("/api/config/msan-mapping")
def upload_msan_mapping():
    uploaded = request.files.get("file")
    suffix = Path(uploaded.filename or "").suffix.lower() if uploaded else ""
    if not uploaded or suffix not in {".xlsx", ".xlsm", ".csv"}:
        return jsonify(ok=False, error="Format accepté : .xlsx, .xlsm ou .csv."), 400
    try:
        mappings = parse_msan_mapping(uploaded.stream.read(15 * 1024 * 1024 + 1), suffix)
        save_msan_mapping(MSAN_MAPPING_PATH, mappings)
    except (ValueError, RuntimeError) as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except OSError:
        app.logger.exception("Impossible d'enregistrer la correspondance MSAN")
        return jsonify(
            ok=False,
            error="Impossible d'enregistrer la correspondance MSAN sur le serveur.",
        ), 500
    return jsonify(ok=True, count=len(mappings))


@app.get("/api/config/msan-mapping/resolve")
def resolve_msan_mapping():
    port = request.args.get("port", "").strip()
    if not port:
        return jsonify(ok=False, error="Le port MSAN est obligatoire."), 400
    spl = resolve_msan_spl(MSAN_MAPPING_PATH, port)
    if not spl:
        return jsonify(ok=False, error=f"Aucun SPL trouvé pour {port}."), 404
    return jsonify(ok=True, port=port, spl=spl)


@app.post("/api/check/start")
def start_check():
    payload = request.get_json(silent=True) or {}
    try:
        spl_result = parse_spl(payload.get("spl", ""))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    with jobs_lock:
        active = next(
            (
                value for value in jobs.values()
                if value["status"] in {"QUEUED", "RUNNING", "PAUSED", "STOPPING"}
            ),
            None,
        )
        if active:
            return jsonify(
                ok=False,
                error="Un contrôle Selenium est déjà en cours.",
                job_id=active["job_id"],
            ), 409

        job_id = uuid4().hex
        spl_data = spl_result.to_dict()
        now = utc_now()
        job = {
            "job_id": job_id,
            "kind": "CHECK",
            "spl": spl_result.spl,
            "odf": spl_result.odf,
            "zr": spl_result.zr,
            "spl_data": spl_data,
            "status": "QUEUED",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
            "error": None,
            "total": len(spl_result.pco_candidates),
            "completed_count": 0,
            "results": [
                {
                    "pco": pco,
                    "status": "PENDING",
                    "status_label": "En attente",
                    "free_ports": [],
                    "free_count": 0,
                    "message": "En attente du contrôle.",
                }
                for pco in spl_result.pco_candidates
            ],
            "logs": [],
            "run_event": Event(),
            "stop_event": Event(),
            "thread": None,
        }
        job["run_event"].set()
        jobs[job_id] = job
        thread = Thread(target=run_job, args=(job_id,), daemon=True)
        job["thread"] = thread
        thread.start()

    return jsonify(ok=True, job_id=job_id, job=public_job(job))


@app.post("/api/assign/start")
def start_assignment():
    payload = request.get_json(silent=True) or {}
    login = str(payload.get("login", "")).strip()
    if not login:
        return jsonify(ok=False, error="Le Login client est obligatoire."), 400
    try:
        spl_result = parse_spl(payload.get("spl", ""))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    with jobs_lock:
        active = next(
            (
                value for value in jobs.values()
                if value["status"] in {"QUEUED", "RUNNING", "PAUSED", "STOPPING"}
            ),
            None,
        )
        if active:
            return jsonify(
                ok=False,
                error="Une automatisation Selenium est déjà en cours.",
                job_id=active["job_id"],
            ), 409

        job_id = uuid4().hex
        spl_data = spl_result.to_dict()
        now = utc_now()
        job = {
            "job_id": job_id,
            "kind": "ASSIGNMENT",
            "login": login,
            "spl": spl_result.spl,
            "odf": spl_result.odf,
            "zr": spl_result.zr,
            "spl_data": spl_data,
            "status": "QUEUED",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
            "error": None,
            "total": len(spl_result.pco_candidates),
            "completed_count": 0,
            "results": [
                {
                    "pco": pco,
                    "status": "PENDING",
                    "status_label": "En attente",
                    "selected_port": None,
                    "message": "En attente du test.",
                }
                for pco in spl_result.pco_candidates
            ],
            "logs": [],
            "run_event": Event(),
            "stop_event": Event(),
            "thread": None,
        }
        job["run_event"].set()
        jobs[job_id] = job
        thread = Thread(target=run_assignment_job, args=(job_id,), daemon=True)
        job["thread"] = thread
        thread.start()

    return jsonify(ok=True, job_id=job_id, job=public_job(job))


@app.post("/api/assign/batch/start")
def start_batch_assignment():
    uploaded = request.files.get("file")
    if not uploaded or Path(uploaded.filename or "").suffix.lower() not in {".xlsx", ".xlsm"}:
        return jsonify(ok=False, error="Sélectionnez un fichier Excel .xlsx ou .xlsm avec Login et SPL."), 400
    try:
        rows = parse_assignment_workbook(uploaded.stream.read(15 * 1024 * 1024 + 1))
    except (ValueError, RuntimeError) as exc:
        return jsonify(ok=False, error=str(exc)), 400
    with jobs_lock:
        active = next((value for value in jobs.values() if value["status"] in {"QUEUED", "RUNNING", "PAUSED", "STOPPING"}), None)
        if active:
            return jsonify(ok=False, error="Une automatisation Selenium est déjà en cours.", job_id=active["job_id"]), 409
        job_id, now = uuid4().hex, utc_now()
        job = {
            "job_id": job_id, "kind": "BATCH_ASSIGNMENT", "assignment_rows": rows,
            "status": "QUEUED", "created_at": now, "started_at": None,
            "finished_at": None, "updated_at": now, "error": None,
            "total": len(rows), "completed_count": 0,
            "results": [{**row, "status": "PENDING", "status_label": "En attente", "pco": None, "selected_port": None, "message": row.get("validation_error") or "En attente."} for row in rows],
            "logs": [], "run_event": Event(), "stop_event": Event(), "thread": None,
        }
        job["run_event"].set()
        jobs[job_id] = job
        job["thread"] = Thread(target=run_batch_assignment_job, args=(job_id,), daemon=True)
        job["thread"].start()
    return jsonify(ok=True, job_id=job_id, job=public_job(job))


@app.post("/api/assign/logins/start")
def start_login_only_assignment():
    payload = request.get_json(silent=True) or {}
    logins = []
    seen = set()
    for value in str(payload.get("logins", "")).replace(";", "\n").replace(",", "\n").splitlines():
        login = value.strip()
        if login and login.upper() not in seen:
            seen.add(login.upper())
            logins.append(login)
    if not logins:
        return jsonify(ok=False, error="Saisissez au moins un Login."), 400
    if len(logins) > 2000:
        return jsonify(ok=False, error="La liste dépasse 2000 Logins."), 400
    rows = [{"excel_row": index, "login": login, "spl": "", "validation_error": None} for index, login in enumerate(logins, 1)]
    with jobs_lock:
        active = next((value for value in jobs.values() if value["status"] in {"QUEUED", "RUNNING", "PAUSED", "STOPPING"}), None)
        if active:
            return jsonify(ok=False, error="Une automatisation Selenium est déjà en cours.", job_id=active["job_id"]), 409
        job_id, now = uuid4().hex, utc_now()
        job = {
            "job_id": job_id, "kind": "BATCH_ASSIGNMENT", "assignment_rows": rows,
            "source": "LOGIN_ONLY", "status": "QUEUED", "created_at": now,
            "started_at": None, "finished_at": None, "updated_at": now,
            "error": None, "total": len(rows), "completed_count": 0,
            "results": [{**row, "status": "PENDING", "status_label": "En attente", "pco": None, "selected_port": None, "message": "Recherche du port MSAN et du SPL en attente."} for row in rows],
            "logs": [], "run_event": Event(), "stop_event": Event(), "thread": None,
        }
        job["run_event"].set(); jobs[job_id] = job
        job["thread"] = Thread(target=run_batch_assignment_job, args=(job_id,), daemon=True)
        job["thread"].start()
    return jsonify(ok=True, job_id=job_id, job=public_job(job))


@app.get("/api/assign/<job_id>")
def assignment_status(job_id: str):
    job = snapshot_job(job_id)
    if not job or job.get("kind") not in {"ASSIGNMENT", "BATCH_ASSIGNMENT"}:
        return jsonify(ok=False, error="Affectation introuvable."), 404
    return jsonify(ok=True, job=job)


@app.post("/api/assign/<job_id>/stop")
def stop_assignment(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("kind") not in {"ASSIGNMENT", "BATCH_ASSIGNMENT"}:
            return jsonify(ok=False, error="Affectation introuvable."), 404
        if job["status"] not in {"RUNNING", "QUEUED"}:
            return jsonify(ok=False, error="L’affectation est déjà terminée."), 409
        job["stop_event"].set()
        job["status"] = "STOPPING"
        job["updated_at"] = utc_now()
    return jsonify(ok=True, status="STOPPING")


@app.post("/api/bulk/start")
def start_bulk_mutation():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify(ok=False, error="Sélectionnez un fichier Excel .xlsx ou .xlsm."), 400
    filename = Path(uploaded.filename).name
    if Path(filename).suffix.lower() not in {".xlsx", ".xlsm"}:
        return jsonify(ok=False, error="Format accepté : .xlsx ou .xlsm."), 400

    content = uploaded.stream.read(15 * 1024 * 1024 + 1)
    if len(content) > 15 * 1024 * 1024:
        return jsonify(ok=False, error="Le fichier Excel dépasse 15 Mo."), 400
    try:
        rows = parse_bulk_workbook(content)
    except (ValueError, RuntimeError) as exc:
        return jsonify(ok=False, error=str(exc)), 400

    with jobs_lock:
        active = next(
            (
                value for value in jobs.values()
                if value["status"] in {"QUEUED", "RUNNING", "PAUSED", "STOPPING"}
            ),
            None,
        )
        if active:
            return jsonify(
                ok=False,
                error="Une automatisation Selenium est déjà en cours.",
                job_id=active["job_id"],
            ), 409

        job_id = uuid4().hex
        now = utc_now()
        job = {
            "job_id": job_id,
            "kind": "BULK_MUTATION",
            "source_filename": filename,
            "bulk_rows": rows,
            "status": "QUEUED",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
            "error": None,
            "output_error": None,
            "output_path": None,
            "total": len(rows),
            "completed_count": 0,
            "results": [
                {
                    "excel_row": row["excel_row"],
                    "command": row["command"],
                    "login": row["login"],
                    "pco": row["pco"],
                    "brin": row["brin"],
                    "target_brin": row.get("target_brin"),
                    "status": "PENDING",
                    "status_label": "En attente",
                    "search_mode": None,
                    "previous_login": None,
                    "spl": None,
                    "message": row.get("validation_error") or "En attente du traitement.",
                }
                for row in rows
            ],
            "logs": [],
            "run_event": Event(),
            "stop_event": Event(),
            "thread": None,
        }
        job["run_event"].set()
        jobs[job_id] = job
        thread = Thread(target=run_bulk_job, args=(job_id,), daemon=True)
        job["thread"] = thread
        thread.start()

    return jsonify(ok=True, job_id=job_id, job=public_job(job))


@app.get("/api/bulk/<job_id>")
def bulk_status(job_id: str):
    job = snapshot_job(job_id)
    if not job or job.get("kind") != "BULK_MUTATION":
        return jsonify(ok=False, error="Traitement Bulk introuvable."), 404
    return jsonify(ok=True, job=job)


@app.post("/api/bulk/<job_id>/stop")
def stop_bulk_mutation(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("kind") != "BULK_MUTATION":
            return jsonify(ok=False, error="Traitement Bulk introuvable."), 404
        if job["status"] not in {"RUNNING", "QUEUED"}:
            return jsonify(ok=False, error="Le traitement Bulk est déjà terminé."), 409
        job["stop_event"].set()
        job["status"] = "STOPPING"
        job["updated_at"] = utc_now()
    return jsonify(ok=True, status="STOPPING")


@app.get("/api/bulk/<job_id>/result.xlsx")
def download_bulk_result(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        output_path = job.get("output_path") if job else None
    if not job:
        stored = JOB_STORE.load(job_id)
        if not stored or stored.get("kind") != "BULK_MUTATION":
            return jsonify(ok=False, error="Traitement Bulk introuvable."), 404
        output_path = stored.get("_output_path")
    elif job.get("kind") != "BULK_MUTATION":
        return jsonify(ok=False, error="Traitement Bulk introuvable."), 404
    if not output_path or not Path(output_path).exists():
        return jsonify(ok=False, error="Le fichier résultat n’est pas encore disponible."), 404
    return send_file(
        output_path,
        as_attachment=True,
        download_name=f"resultats_bulk_mutation_{job_id[:8]}.xlsx",
    )


@app.get("/api/check/<job_id>")
def check_status(job_id: str):
    job = snapshot_job(job_id)
    if not job or job.get("kind") != "CHECK":
        return jsonify(ok=False, error="Contrôle introuvable."), 404
    return jsonify(ok=True, job=job)


@app.post("/api/check/<job_id>/pause")
def pause_check(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("kind") != "CHECK":
            return jsonify(ok=False, error="Contrôle introuvable."), 404
        if job["status"] != "RUNNING":
            return jsonify(ok=False, error="Le contrôle ne peut pas être mis en pause."), 409
        job["run_event"].clear()
        job["status"] = "PAUSED"
        job["updated_at"] = utc_now()
    add_log(job_id, "WARNING", "Contrôle mis en pause.")
    return jsonify(ok=True, status="PAUSED")


@app.post("/api/check/<job_id>/resume")
def resume_check(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("kind") != "CHECK":
            return jsonify(ok=False, error="Contrôle introuvable."), 404
        if job["status"] != "PAUSED":
            return jsonify(ok=False, error="Le contrôle n’est pas en pause."), 409
        job["run_event"].set()
        job["status"] = "RUNNING"
        job["updated_at"] = utc_now()
    add_log(job_id, "INFO", "Contrôle repris.")
    return jsonify(ok=True, status="RUNNING")


@app.post("/api/check/<job_id>/stop")
def stop_check(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("kind") != "CHECK":
            return jsonify(ok=False, error="Contrôle introuvable."), 404
        if job["status"] not in {"RUNNING", "PAUSED"}:
            return jsonify(ok=False, error="Le contrôle est déjà terminé."), 409
        job["stop_event"].set()
        job["run_event"].set()
        job["status"] = "STOPPING"
        job["updated_at"] = utc_now()
    return jsonify(ok=True, status="STOPPING")


@app.get("/api/available/latest")
def latest_available():
    if not LATEST_RESULTS_PATH.exists():
        return jsonify(ok=True, spl=None, available_pcos=[])
    try:
        data = json.loads(LATEST_RESULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return jsonify(ok=False, error="Le fichier des PCO disponibles est illisible."), 500
    return jsonify(ok=True, **data)


@app.get("/api/check/<job_id>/available.csv")
def export_available_csv(job_id: str):
    job = snapshot_job(job_id)
    if not job:
        return jsonify(ok=False, error="Contrôle introuvable."), 404

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["SPL", "ODF", "ZR", "PCO", "brin", "Date contrôle"])
    for row in job["available_pcos"]:
        for brin in row.get("free_ports", []):
            writer.writerow([
                job["spl"], job["odf"], job["zr"], row["pco"], brin,
                row.get("checked_at", ""),
            ])

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=pco_disponibles_{job['spl']}.csv"},
    )


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5055"))
    if host not in {"127.0.0.1", "localhost", "::1"} and not authentication_enabled():
        raise RuntimeError(
            "APP_PASSWORD est obligatoire lorsque l'application est exposée sur le réseau."
        )
    if os.getenv("OPEN_BROWSER", "1").lower() in {"1", "true", "yes"}:
        Thread(target=open_dashboard, daemon=True).start()
    if host in {"127.0.0.1", "localhost", "::1"}:
        app.run(host=host, port=port, debug=False, threaded=True)
    else:
        from waitress import serve

        serve(app, host=host, port=port, threads=8)

import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.db import session_store
from backend.errors import AppError
from backend.schemas.common import ok
from backend.sessions import service as session_svc
from backend.storage import paths

router = APIRouter(tags=["reports"])
log = logging.getLogger(__name__)

REPORT_TYPES = {"executive", "operational", "technical", "inventory"}
FORMAT_EXTENSIONS = {"excel": ".xlsx", "pdf": ".pdf"}

# report_runs.status vocabulary. Deliberately not a DB CHECK constraint — see
# the create_report_runs migration.
RUN_RUNNING   = "running"
RUN_COMPLETED = "completed"
RUN_FAILED    = "failed"

_ERROR_DETAIL_MAX_CHARS = 500


# ── report_runs store ────────────────────────────────────────────────────────
# `execute` / `query_one` are imported inside each function on purpose: the
# offline endpoint suite patches backend.db.connection.* globally, and a
# module-level `from ... import` would bind the unpatched originals.

def _start_report_run(
    tenant_id: str, session_id: str, report_type: str, formats: list, created_by: str,
) -> str | None:
    """Insert the RUNNING row before the background task is scheduled, so a
    status poll right after POST /generate already sees the attempt."""
    from backend.db.connection import query_one
    try:
        row = query_one(
            """INSERT INTO report_runs
               (tenant_id, session_id, report_type, formats, status, created_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (tenant_id, session_id, report_type, list(formats), RUN_RUNNING, created_by),
        )
        return row["id"] if row else None
    except Exception as e:
        # Losing the status row must not block the report itself.
        log.error("Could not open report run for session %s: %s", session_id, e, exc_info=True)
        return None


def _finish_report_run(
    run_id: str | None, status: str, error_code: str | None = None, error_detail: str | None = None,
) -> None:
    if not run_id:
        return
    from backend.db.connection import execute
    detail = error_detail[:_ERROR_DETAIL_MAX_CHARS] if error_detail else None
    try:
        execute(
            """UPDATE report_runs
               SET status = %s, error_code = %s, error_detail = %s, finished_at = NOW()
               WHERE id = %s""",
            (status, error_code, detail, run_id),
        )
    except Exception as e:
        log.error("Could not close report run %s: %s", run_id, e, exc_info=True)


def _latest_report_run(tenant_id: str, session_id: str, fmt: str) -> dict | None:
    """Most recent generation attempt that was asked to produce `fmt`."""
    from backend.db.connection import query_one
    try:
        return query_one(
            """SELECT id, status, error_code, error_detail, report_type, created_at, finished_at
               FROM report_runs
               WHERE tenant_id = %s AND session_id = %s AND %s = ANY(formats)
               ORDER BY created_at DESC LIMIT 1""",
            (tenant_id, session_id, fmt),
        )
    except Exception as e:
        log.error("Could not read report runs for session %s: %s", session_id, e, exc_info=True)
        return None


def _list_report_runs(tenant_id: str, session_id: str, limit: int = 10) -> list[dict]:
    from backend.db.connection import query
    try:
        rows = query(
            """SELECT id, report_type, formats, status, error_code, error_detail,
                      created_at, finished_at
               FROM report_runs
               WHERE tenant_id = %s AND session_id = %s
               ORDER BY created_at DESC LIMIT %s""",
            (tenant_id, session_id, limit),
        )
        return [dict(r) for r in rows]
    except Exception as e:
        log.error("Could not list report runs for session %s: %s", session_id, e, exc_info=True)
        return []


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/reports/generate")
def generate_report(
    session_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s["status"] != "COMPLETED":
        raise HTTPException(status_code=409, detail="Session must be COMPLETED before generating reports")

    report_type = body.get("type", "operational")
    if report_type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid report type. Options: {sorted(REPORT_TYPES)}")

    formats = body.get("formats", ["excel"])
    if not isinstance(formats, list) or not formats:
        raise HTTPException(status_code=400, detail="formats must be a non-empty list")
    unknown = [f for f in formats if f not in FORMAT_EXTENSIONS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format(s) {unknown}. Use: {sorted(FORMAT_EXTENSIONS)}",
        )

    run_id = _start_report_run(user.tenant_id, session_id, report_type, formats, user.user_id)
    background_tasks.add_task(
        _generate_background, user.tenant_id, session_id, report_type, formats, run_id
    )
    return ok({
        "message": "Report generation started",
        "type": report_type,
        "formats": formats,
        "run_id": run_id,
        "status": RUN_RUNNING,
    })


@router.get("/sessions/{session_id}/reports/status")
def report_status(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Outcome of the recent generation attempts for this session.

    Registered before /reports/{format} so "status" is not parsed as a format.
    """
    if not session_svc.get_session(user.tenant_id, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return ok({"runs": _list_report_runs(user.tenant_id, session_id)})


@router.get("/sessions/{session_id}/reports/{format}")
def download_report(
    session_id: str,
    format: str,
    user: CurrentUser = Depends(get_current_user),
):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    ext = FORMAT_EXTENSIONS.get(format)
    if not ext:
        raise HTTPException(status_code=400, detail=f"Unknown format '{format}'. Use: excel, pdf")

    report_dir = paths.reports_artifact_dir(user.tenant_id, session_id)
    for f in report_dir.glob(f"*{ext}"):
        return FileResponse(f, filename=f.name)

    # No file. Say what actually happened instead of "generate one first",
    # which used to be the answer even right after a generation had failed.
    run = _latest_report_run(user.tenant_id, session_id, format)
    status = run["status"] if run else None

    if status == RUN_RUNNING:
        raise AppError(
            "report_generation_in_progress",
            f"The {format} report is still being generated. Try again in a moment.",
            status_code=409,
            params={"format": format},
        )
    if status == RUN_FAILED:
        raise AppError(
            "report_generation_failed",
            f"Report generation failed ({run.get('error_code') or 'unknown_error'}).",
            status_code=409,
            params={
                "format": format,
                "reason": run.get("error_code") or "unknown_error",
                "detail": run.get("error_detail") or "",
            },
        )
    if status == RUN_COMPLETED:
        raise AppError(
            "report_file_missing",
            f"The {format} report was generated but its file is no longer on disk. "
            "Generate it again.",
            status_code=404,
            params={"format": format},
        )

    raise HTTPException(
        status_code=404,
        detail=f"No {format} report found. Generate one first via POST /reports/generate",
    )


def _generate_background(
    tenant_id: str, session_id: str, report_type: str, formats: list,
    run_id: str | None = None,
) -> None:
    try:
        result = session_store.get_training_result(tenant_id, session_id)
        if not result:
            log.error("No training result found for session %s — cannot generate report", session_id)
            _finish_report_run(
                run_id, RUN_FAILED, "no_training_result",
                "The session has no stored training result to build a report from.",
            )
            return

        report_data = {
            "run_id": result.get("run_id", "N/A"),
            "config_hash": result.get("job_id", "N/A"),
            "completed_at": result.get("completed_at", "N/A"),
            "metrics": result.get("metrics", {}),
            "inventory": result.get("inventory", {}),
            "routing": result.get("routing", {}),
            "data_quality": result.get("data_quality", {}),
            "config": result.get("config", {}),
            "report_type": report_type,
        }

        report_dir = paths.reports_artifact_dir(tenant_id, session_id)
        report_dir.mkdir(parents=True, exist_ok=True)

        if "excel" in formats:
            _export_excel(report_data, report_dir / f"report_{session_id}.xlsx")
        if "pdf" in formats:
            _export_pdf(report_data, report_dir / f"report_{session_id}.pdf")

        log.info(f"Report generated for session {session_id}")
        _finish_report_run(run_id, RUN_COMPLETED)
    except Exception as e:
        log.error(f"Report generation failed for {session_id}: {e}", exc_info=True)
        _finish_report_run(run_id, RUN_FAILED, "generation_error", str(e))


def _export_excel(report_data: dict, path: Path) -> None:
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Metrics"
        metrics = report_data.get("metrics", {})
        rows = metrics.get("rows", [])
        if rows:
            ws.append(list(rows[0].keys()))
            for row in rows:
                ws.append([str(v) if v is not None else "" for v in row.values()])
        wb.save(path)
    except ImportError:
        # openpyxl not installed — write JSON fallback
        import json
        path.with_suffix(".json").write_text(json.dumps(report_data, indent=2, default=str))


def _export_pdf(report_data: dict, path: Path) -> None:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
        )

        doc = SimpleDocTemplate(str(path), pagesize=letter,
                                leftMargin=0.75*inch, rightMargin=0.75*inch,
                                topMargin=0.75*inch, bottomMargin=0.75*inch)
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=6)
        h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceAfter=4)
        body = styles["Normal"]

        story = []
        story.append(Paragraph("ForecastPlatform — Session Report", h1))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
        story.append(Spacer(1, 0.1*inch))

        # Header info
        meta = [
            ["Run ID", report_data.get("run_id", "N/A")],
            ["Completed", report_data.get("completed_at", "N/A")],
            ["Report type", report_data.get("report_type", "operational")],
        ]
        t = Table(meta, colWidths=[1.5*inch, 5*inch])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.2*inch))

        # Metrics by model
        story.append(Paragraph("Model Metrics Summary", h2))
        by_model = report_data.get("metrics", {}).get("by_model", {})
        if by_model:
            header = ["Model", "Avg MAE", "Avg WAPE", "SKUs"]
            rows = [header]
            for model, stats in by_model.items():
                rows.append([
                    model,
                    f"{stats.get('avg_mae', 0):.4f}" if stats.get('avg_mae') is not None else "N/A",
                    f"{stats.get('avg_wape', 0):.4f}" if stats.get('avg_wape') is not None else "N/A",
                    str(stats.get("n_skus", "")),
                ])
            t2 = Table(rows, colWidths=[2*inch, 1.5*inch, 1.5*inch, 1*inch])
            t2.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ]))
            story.append(t2)
        else:
            story.append(Paragraph("No model metrics available.", body))
        story.append(Spacer(1, 0.2*inch))

        # Per-SKU metrics
        story.append(Paragraph("Per-SKU Metrics", h2))
        sku_rows = report_data.get("metrics", {}).get("rows", [])
        if sku_rows:
            header = ["SKU", "Model", "Type", "MAE", "RMSE", "WAPE"]
            sku_table = [header]
            for row in sku_rows[:100]:
                sku_table.append([
                    str(row.get("sku", "—")),
                    str(row.get("model", "—")),
                    str(row.get("type", "—")),
                    f"{row.get('mae', 0):.4f}" if row.get("mae") is not None else "N/A",
                    f"{row.get('rmse', 0):.4f}" if row.get("rmse") is not None else "N/A",
                    f"{row.get('wape', 0):.4f}" if row.get("wape") is not None else "N/A",
                ])
            t_sku = Table(sku_table, colWidths=[1.5*inch, 1.4*inch, 0.8*inch, 1.1*inch, 1.1*inch, 1.1*inch])
            t_sku.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("ALIGN", (3, 0), (-1, -1), "CENTER"),
            ]))
            story.append(t_sku)
            if len(sku_rows) > 100:
                story.append(Spacer(1, 0.05*inch))
                story.append(Paragraph(f"Showing first 100 of {len(sku_rows)} rows.", body))
        else:
            story.append(Paragraph("No per-SKU metrics available.", body))
        story.append(Spacer(1, 0.2*inch))

        # Data quality
        dq = report_data.get("data_quality", {})
        if dq:
            story.append(Paragraph("Data Quality", h2))
            dq_rows = [[k, str(v)] for k, v in dq.items() if not isinstance(v, dict)]
            if dq_rows:
                t3 = Table(dq_rows, colWidths=[2.5*inch, 4*inch])
                t3.setStyle(TableStyle([
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.whitesmoke, colors.white]),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ]))
                story.append(t3)

        doc.build(story)

    except ImportError:
        # reportlab not installed — fall back to plain text
        lines = [
            "FORECAST PLATFORM — SESSION REPORT",
            "=" * 60,
            f"Run ID: {report_data.get('run_id', 'N/A')}",
            f"Completed: {report_data.get('completed_at', 'N/A')}",
            "",
            "METRICS SUMMARY",
            "-" * 40,
        ]
        by_model = report_data.get("metrics", {}).get("by_model", {})
        for model, stats in by_model.items():
            lines.append(f"  {model}: MAE={stats.get('avg_mae', 'N/A')} WAPE={stats.get('avg_wape', 'N/A')}")
        path.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
        log.warning("reportlab not installed — wrote text fallback at %s", path.with_suffix(".txt"))

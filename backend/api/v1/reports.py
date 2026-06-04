import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.auth.guards import CurrentUser, get_current_user
from backend.db import session_store
from backend.schemas.common import ok
from backend.sessions import service as session_svc
from backend.storage import paths

router = APIRouter(tags=["reports"])
log = logging.getLogger(__name__)

REPORT_TYPES = {"executive", "operational", "technical", "inventory"}


@router.post("/sessions/{session_id}/reports/generate")
def generate_report(
    session_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
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
    background_tasks.add_task(
        _generate_background, user.tenant_id, session_id, report_type, formats
    )
    return ok({"message": "Report generation started", "type": report_type, "formats": formats})


@router.get("/sessions/{session_id}/reports/{format}")
def download_report(
    session_id: str,
    format: str,
    user: CurrentUser = Depends(get_current_user),
):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    ext_map = {"excel": ".xlsx", "pdf": ".pdf"}
    ext = ext_map.get(format)
    if not ext:
        raise HTTPException(status_code=400, detail=f"Unknown format '{format}'. Use: excel, pdf")

    report_dir = paths.reports_artifact_dir(user.tenant_id, session_id)
    for f in report_dir.glob(f"*{ext}"):
        return FileResponse(f, filename=f.name)

    raise HTTPException(
        status_code=404,
        detail=f"No {format} report found. Generate one first via POST /reports/generate",
    )


def _generate_background(tenant_id: str, session_id: str, report_type: str, formats: list) -> None:
    try:
        result = session_store.get_training_result(tenant_id, session_id)
        if not result:
            log.error("No training result found for session %s — cannot generate report", session_id)
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
    except Exception as e:
        log.error(f"Report generation failed for {session_id}: {e}", exc_info=True)


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

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from .config import HTTP_HOST, HTTP_PORT
from .security import redact_data, redact_text
from .state import StateStore


class EvidenceExporter:
    last_capture_error = ""

    def __init__(self, store: StateStore):
        self.store = store

    def export_zip(self, run_id: str) -> bytes:
        run = self._run(run_id)
        self.capture_dashboard(run_id)
        staged = Path(run["staged_path"])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for record in self.store.get_artifacts(run_id):
                path = staged / record["path"]
                if path.exists() and path.is_file():
                    try:
                        text = redact_text(path.read_text(encoding="utf-8"))
                        archive.writestr(f"artifacts/{record['path']}", text)
                    except UnicodeDecodeError:
                        archive.write(path, f"artifacts/{record['path']}")
            archive.writestr("evidence/run.json", json.dumps(redact_data(run), indent=2, default=str))
            archive.writestr(
                "evidence/events.json",
                json.dumps(self.store.get_events(run_id), indent=2, default=str),
            )
            for item in self.store.list_evidence(run_id):
                path = Path(item["path"])
                if path.exists() and path.is_file():
                    archive.write(path, f"evidence/screenshots/{path.name}")
        return buffer.getvalue()

    def export_pdf(self, run_id: str) -> bytes:
        run = self._run(run_id)
        self.capture_dashboard(run_id)
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        except ImportError as exc:
            raise RuntimeError("reportlab is not installed. Run setup.ps1.") from exc
        buffer = io.BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=16 * mm,
            leftMargin=16 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title=f"Deployment Report - {run['project_name']}",
        )
        styles = getSampleStyleSheet()
        story: list[Any] = [
            Paragraph("Deployment Agent Evidence Report", styles["Title"]),
            Paragraph(f"Project: {run['project_name']}", styles["Heading2"]),
            Paragraph(f"Run: {run_id}", styles["BodyText"]),
            Paragraph(f"State: {run['state']}", styles["BodyText"]),
            Spacer(1, 8),
        ]
        readiness = run.get("readiness") or {}
        story.append(Paragraph(f"Readiness: {readiness.get('score', 0)}/100", styles["Heading2"]))
        categories = readiness.get("categories", {})
        if categories:
            rows = [["Category", "Score"]] + [[key.upper(), str(value)] for key, value in categories.items()]
            table = Table(rows, colWidths=[90 * mm, 35 * mm])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("PADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.extend([table, Spacer(1, 12)])
        spec = run.get("spec") or {}
        services = spec.get("services", [])
        story.append(Paragraph("Detected Services", styles["Heading2"]))
        for service in services:
            story.append(
                Paragraph(
                    f"{service.get('name')} — Next.js {service.get('version')} — root {service.get('root') or '.'} — port {service.get('port')}",
                    styles["BodyText"],
                )
            )
        story.extend([Spacer(1, 8), Paragraph("Pipeline Evidence", styles["Heading2"])])
        for event in self.store.get_events(run_id)[-80:]:
            message = self._xml(redact_text(event.get("message", "")))
            story.append(Paragraph(f"[{event.get('stage')}] {message}", styles["BodyText"]))
        monitor = run.get("monitor") or {}
        if monitor:
            story.extend([Spacer(1, 8), Paragraph("Remote Snapshot", styles["Heading2"])])
            story.append(Paragraph(self._xml(json.dumps(monitor, default=str)[:5000]), styles["Code"]))
        screenshots = [Path(item["path"]) for item in self.store.list_evidence(run_id)]
        screenshots = [path for path in screenshots if path.exists() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        if screenshots:
            story.extend([Spacer(1, 10), Paragraph("Dashboard and Supporting Screenshots", styles["Heading2"])])
            for path in screenshots[:10]:
                try:
                    story.append(Paragraph(self._xml(path.stem.replace("-", " ").title()), styles["Heading3"]))
                    story.append(Image(str(path), width=178 * mm, height=118 * mm, kind="proportional"))
                    story.append(Spacer(1, 7))
                except Exception:
                    continue
        document.build(story)
        return buffer.getvalue()

    def capture_dashboard(self, run_id: str) -> list[str]:
        """Best-effort local dashboard capture."""
        run = self._run(run_id)
        evidence_dir = Path(run["staged_path"]).parent / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        captured: list[str] = []
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=1)
                page.goto(
                    f"http://{HTTP_HOST}:{HTTP_PORT}/?run={run_id}&capture=1",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                page.wait_for_function(
                    "() => document.body.dataset.captureReady === 'true'",
                    timeout=30000,
                )
                page.wait_for_timeout(750)
                if run["state"] == "REVIEW_READY":
                    path = evidence_dir / "auto-review.png"
                    page.screenshot(path=str(path), full_page=True)
                    captured.append(str(path))
                else:
                    target = ((run.get("plan") or {}).get("target")) or "aws_ec2"
                    tabs = (
                        ("overview", "pipeline", "vercel", "ecr", "logs", "api")
                        if target == "vercel"
                        else ("overview", "pipeline", "aws", "ecr", "security", "logs", "api")
                    )
                    for tab in tabs:
                        button = page.locator(f'[data-tab="{tab}"]')
                        if button.count() == 0 or not button.is_visible():
                            continue
                        button.click()
                        page.wait_for_timeout(250)
                        path = evidence_dir / f"auto-{tab}.png"
                        page.screenshot(path=str(path), full_page=True)
                        captured.append(str(path))
                browser.close()
        except Exception as exc:                                # noqa: BLE001
            self.last_capture_error = str(exc)[:300]
            return []
        self.last_capture_error = ""
        existing = {Path(item["path"]).resolve() for item in self.store.list_evidence(run_id)}
        for path_value in captured:
            path = Path(path_value)
            if path.resolve() not in existing:
                self.store.add_evidence(run_id, path.stem, str(path))
        return captured

    def save_evidence(self, run_id: str, name: str, data_url: str) -> dict[str, Any]:
        run = self._run(run_id)
        match = re.match(r"data:image/(png|jpeg|webp);base64,(.+)", data_url, flags=re.DOTALL)
        if not match:
            raise ValueError("Evidence must be a PNG, JPEG, or WebP data URL")
        extension = "jpg" if match.group(1) == "jpeg" else match.group(1)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-")[:80] or "evidence"
        evidence_dir = Path(run["staged_path"]).parent / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        path = evidence_dir / f"{safe_name}.{extension}"
        payload = base64.b64decode(match.group(2), validate=True)
        if len(payload) > 12 * 1024 * 1024:
            raise ValueError("Evidence image exceeds 12 MB")
        path.write_bytes(payload)
        self.store.add_evidence(run_id, safe_name, str(path))
        return {"name": safe_name, "path": str(path), "size": len(payload)}

    def _run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError("Run not found")
        return run

    @staticmethod
    def _xml(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

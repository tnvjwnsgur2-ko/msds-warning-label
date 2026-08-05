from __future__ import annotations

import os
import re
import tempfile
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.pdfgen import canvas

from app.module_service import (
    pictogram_catalog,
    pictogram_path,
    run_management_modules,
    run_warning_modules,
)

BASE_DIR = Path(__file__).resolve().parent
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
SAVE_DIR = BASE_DIR / "saved_results"
MAX_FILES = int(os.getenv("MAX_UPLOAD_FILES", "10"))
MAX_FILE_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
REQUEST_HISTORY: dict[str, deque[float]] = defaultdict(deque)
FONT_REGULAR_CANDIDATES = (
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path(r"C:\Windows\Fonts\malgun.ttf"),
)
FONT_BOLD_CANDIDATES = (
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    Path(r"C:\Windows\Fonts\malgunbd.ttf"),
)

app = FastAPI(title="MSDS 문서 생성", version="2.2.0")
allowed_origins = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "").split(",") if item.strip()]
allowed_origin_regex = os.getenv("ALLOWED_ORIGIN_REGEX", "").strip() or None
if not allowed_origins:
    allowed_origins = ["http://127.0.0.1:8000", "http://localhost:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allowed_origin_regex,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def public_app_security(request: Request, call_next):
    if request.method == "POST":
        client = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        client = client or (request.client.host if request.client else "unknown")
        now = time.monotonic()
        history = REQUEST_HISTORY[client]
        while history and now - history[0] > RATE_LIMIT_WINDOW_SECONDS:
            history.popleft()
        if len(history) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(status_code=429, content={"detail": "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."})
        history.append(now)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'"
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/styles.css")
async def styles() -> FileResponse:
    return FileResponse(
        BASE_DIR / "styles.css",
        media_type="text/css",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/app.js")
async def javascript() -> FileResponse:
    return FileResponse(
        BASE_DIR / "app.js",
        media_type="application/javascript",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/api/health")
@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "warning_implementation": "legacy_parser_with_image_adapter",
        "management_implementation": "layout_sorted_section_adapter",
    }


@app.get("/api/pictograms")
async def list_pictograms() -> dict[str, Any]:
    return {"assets": pictogram_catalog()}


@app.get("/api/pictograms/{asset_id}")
async def get_pictogram(asset_id: str) -> FileResponse:
    if not asset_id.isdigit():
        raise HTTPException(status_code=400, detail="잘못된 그림문자 식별자입니다.")
    target = pictogram_path(asset_id)
    if target is None:
        raise HTTPException(status_code=404, detail="그림문자를 찾을 수 없습니다.")
    return FileResponse(target, media_type="image/gif")


async def _run_pdf_modules(
    files: list[UploadFile],
    *,
    temp_prefix: str,
    service: Any,
    result_key: str,
    executed_modules: list[str],
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="PDF 파일이 필요합니다.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"PDF는 최대 {MAX_FILES}개까지 처리할 수 있습니다.")

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=temp_prefix) as temp_dir:
        temp_path = Path(temp_dir)

        for index, upload in enumerate(files, start=1):
            original_name = Path(upload.filename or f"document-{index}.pdf").name
            is_pdf = upload.content_type == "application/pdf" or original_name.lower().endswith(".pdf")
            if not is_pdf:
                results.append({
                    "source_file": original_name,
                    "status": "error",
                    "error": "PDF 형식이 아닙니다.",
                })
                await upload.close()
                continue

            target = temp_path / f"{index:02d}_{original_name}"
            try:
                content = await upload.read()
                if not content:
                    raise ValueError("빈 파일입니다.")
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError(f"파일 크기는 {MAX_FILE_BYTES // (1024 * 1024)}MB 이하여야 합니다.")
                if not content.startswith(b"%PDF-"):
                    raise ValueError("PDF 파일 서명이 올바르지 않습니다.")
                target.write_bytes(content)

                mapped = await run_in_threadpool(service, target)
                results.append({
                    "source_file": original_name,
                    "status": "success",
                    "page_count": mapped.pop("page_count"),
                    "implementation": mapped.get("implementation"),
                    result_key: mapped,
                })
            except Exception as exc:
                results.append({
                    "source_file": original_name,
                    "status": "error",
                    "error": str(exc) if isinstance(exc, ValueError) else "자동 처리 중 오류가 발생했습니다.",
                })
            finally:
                await upload.close()

    success_count = sum(item["status"] == "success" for item in results)
    return {
        "requested_count": len(files),
        "success_count": success_count,
        "failure_count": len(files) - success_count,
        "executed_modules": executed_modules,
        "results": results,
    }


@app.post("/api/warning-labels")
async def create_warning_labels(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """PDF마다 W-1~W-6을 모두 실행하고 편집 가능한 텍스트 결과를 반환한다."""
    return await _run_pdf_modules(
        files,
        temp_prefix="msds-warning-",
        service=run_warning_modules,
        result_key="warning_label",
        executed_modules=["W-1", "W-2", "W-3", "W-4", "W-5", "W-6"],
    )


@app.post("/api/management-guides")
async def create_management_guides(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """PDF마다 M-1~M-5를 모두 실행하고 편집 가능한 관리요령 결과를 반환한다."""
    return await _run_pdf_modules(
        files,
        temp_prefix="msds-management-",
        service=run_management_modules,
        result_key="management_guide",
        executed_modules=["M-1", "M-2", "M-3", "M-4", "M-5"],
    )


def _register_pdf_fonts() -> tuple[str, str]:
    regular = next((path for path in FONT_REGULAR_CANDIDATES if path.is_file()), None)
    bold = next((path for path in FONT_BOLD_CANDIDATES if path.is_file()), regular)
    if regular is None:
        return "Helvetica", "Helvetica-Bold"
    try:
        if "MSDSRegular" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("MSDSRegular", str(regular)))
        if bold and "MSDSBold" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("MSDSBold", str(bold)))
        return "MSDSRegular", "MSDSBold" if bold else "MSDSRegular"
    except TTFError:
        return "Helvetica", "Helvetica-Bold"


def _wrap_pdf_line(value: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    if not value:
        return [""]
    lines: list[str] = []
    current = ""
    for character in value:
        candidate = current + character
        if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
            lines.append(current.rstrip())
            current = character.lstrip()
        else:
            current = candidate
    lines.append(current.rstrip())
    return lines


def _draw_lines(
    pdf: canvas.Canvas,
    values: list[str],
    *,
    x: float,
    y: float,
    width: float,
    font_name: str,
    font_size: float = 9,
    line_height: float = 13,
) -> float:
    pdf.setFont(font_name, font_size)
    for value in values:
        source_lines = str(value or "").splitlines() or [""]
        for source_line in source_lines:
            for line in _wrap_pdf_line(source_line, font_name, font_size, width):
                if y < 42:
                    pdf.showPage()
                    y = A4[1] - 42
                    pdf.setFont(font_name, font_size)
                pdf.drawString(x, y, line)
                y -= line_height
    return y


def _draw_section(
    pdf: canvas.Canvas,
    title: str,
    value: str,
    *,
    y: float,
    regular_font: str,
    bold_font: str,
) -> float:
    margin = 42
    if y < 75:
        pdf.showPage()
        y = A4[1] - margin
    pdf.setFont(bold_font, 11)
    pdf.drawString(margin, y, title)
    y -= 17
    content = str(value or "").strip() or "입력되지 않음"
    y = _draw_lines(
        pdf,
        content.splitlines(),
        x=margin + 8,
        y=y,
        width=A4[0] - margin * 2 - 8,
        font_name=regular_font,
    )
    return y - 10


def _field(record: dict[str, Any], name: str) -> str:
    fields = record.get("final_fields")
    if isinstance(fields, dict):
        return str(fields.get(name) or "").strip()
    return ""


def _pictogram_ids(record: dict[str, Any]) -> list[str]:
    for module in record.get("modules") or []:
        if isinstance(module, dict) and module.get("module_id") == "W-2":
            result: list[str] = []
            for asset in module.get("pictogram_assets") or []:
                asset_id = str(asset.get("id") or "") if isinstance(asset, dict) else ""
                if asset_id.isdigit() and asset_id not in result:
                    result.append(asset_id)
            return result
    return []


def _draw_warning_record(pdf: canvas.Canvas, record: dict[str, Any], regular_font: str, bold_font: str) -> None:
    width, height = A4
    margin = 42
    y = height - margin
    pdf.setTitle("MSDS 경고표지")
    pdf.setFont(bold_font, 18)
    pdf.drawCentredString(width / 2, y, "경고표지")
    y -= 28
    product = _field(record, "product_name") or Path(str(record.get("source_file") or "MSDS")).stem
    y = _draw_lines(pdf, [product], x=margin, y=y, width=width - margin * 2, font_name=bold_font, font_size=14, line_height=19)
    y -= 8

    assets = _pictogram_ids(record)
    if assets:
        icon_size = 48
        gap = 6
        total_width = len(assets) * icon_size + max(0, len(assets) - 1) * gap
        x = (width - total_width) / 2
        for asset_id in assets:
            path = pictogram_path(asset_id)
            if path:
                pdf.drawImage(ImageReader(str(path)), x, y - icon_size, icon_size, icon_size, preserveAspectRatio=True, mask="auto")
            x += icon_size + gap
        y -= icon_size + 20

    signal_word = _field(record, "signal_word")
    if signal_word:
        pdf.setFont(bold_font, 16)
        pdf.drawCentredString(width / 2, y, signal_word)
        y -= 24
    y = _draw_section(pdf, "유해·위험 문구", _field(record, "hazard_statements"), y=y, regular_font=regular_font, bold_font=bold_font)
    y = _draw_section(pdf, "예방조치 문구", _field(record, "precautionary_statements"), y=y, regular_font=regular_font, bold_font=bold_font)
    y = _draw_section(pdf, "공급자 정보", _field(record, "supplier_information"), y=y, regular_font=regular_font, bold_font=bold_font)
    source = Path(str(record.get("source_file") or "MSDS.pdf")).name
    _draw_lines(pdf, [f"원본 PDF: {source}"], x=margin, y=y, width=width - margin * 2, font_name=regular_font, font_size=8, line_height=11)


def _draw_management_record(pdf: canvas.Canvas, record: dict[str, Any], regular_font: str, bold_font: str) -> None:
    width, height = A4
    margin = 42
    y = height - margin
    pdf.setTitle("MSDS 관리요령")
    pdf.setFont(bold_font, 18)
    pdf.drawCentredString(width / 2, y, "관리요령")
    y -= 30
    work_name = str(record.get("work_name") or "").strip()
    y = _draw_lines(pdf, [f"작업명: {work_name}"], x=margin, y=y, width=width - margin * 2, font_name=bold_font, font_size=13, line_height=18)
    y -= 8
    sections = (
        ("M-1 제품명", "product_name"),
        ("M-2 건강 및 환경 유해성·물리적 위험성", "hazard_risk_summary"),
        ("M-3 안전 및 보건상의 취급주의 사항", "safe_handling_precautions"),
        ("M-4 적절한 보호구", "personal_protective_equipment"),
        ("M-5 응급조치 요령 및 사고 시 대처방법", "emergency_response"),
    )
    for title, field_name in sections:
        y = _draw_section(pdf, title, _field(record, field_name), y=y, regular_font=regular_font, bold_font=bold_font)
    source = Path(str(record.get("source_file") or "MSDS.pdf")).name
    _draw_lines(pdf, [f"원본 PDF: {source}"], x=margin, y=y, width=width - margin * 2, font_name=regular_font, font_size=8, line_height=11)


def _save_pdf(*, records: list[dict[str, Any]], filename_prefix: str, renderer: Any) -> dict[str, str]:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{filename_prefix}_{timestamp}.pdf"
    target = SAVE_DIR / filename
    regular_font, bold_font = _register_pdf_fonts()
    pdf = canvas.Canvas(str(target), pagesize=A4, pageCompression=1)
    for index, record in enumerate(records):
        if index:
            pdf.showPage()
        renderer(pdf, record, regular_font, bold_font)
    pdf.save()
    return {
        "message": "최종 내용을 PDF로 저장했습니다.",
        "filename": filename,
        "download_url": f"/api/saved/{filename}",
    }


@app.post("/api/warning-labels/save")
async def save_warning_labels(payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    """사용자가 수정한 최종 W-1~W-6 결과를 PDF로 저장한다."""
    labels = payload.get("labels")
    _validate_final_records(labels, expected_modules=6)
    return _save_pdf(
        records=labels,
        filename_prefix="warning_labels",
        renderer=_draw_warning_record,
    )


@app.post("/api/management-guides/save")
async def save_management_guides(payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    """PDF별 작업명과 사용자가 수정한 최종 M-1~M-5 결과를 저장한다."""
    _validate_final_records(payload.get("guides"), expected_modules=5)
    guides = payload["guides"]
    for index, guide in enumerate(guides, start=1):
        work_name = str(guide.get("work_name") or "").strip()
        if not work_name:
            source_file = Path(str(guide.get("source_file") or f"PDF {index}")).name
            raise HTTPException(status_code=400, detail=f"{source_file}의 작업명을 입력해야 저장할 수 있습니다.")
        guide["work_name"] = work_name
    return _save_pdf(
        records=guides,
        filename_prefix="management_guides",
        renderer=_draw_management_record,
    )


def _validate_final_records(records: Any, *, expected_modules: int) -> None:
    if not isinstance(records, list) or not records:
        raise HTTPException(status_code=400, detail="저장할 결과가 없습니다.")
    if len(records) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"결과는 최대 {MAX_FILES}개까지 저장할 수 있습니다.")
    for record in records:
        modules = record.get("modules") if isinstance(record, dict) else None
        fields = record.get("final_fields") if isinstance(record, dict) else None
        if not isinstance(modules, list) or len(modules) != expected_modules or not isinstance(fields, dict):
            raise HTTPException(status_code=400, detail="최종 편집 결과 형식이 올바르지 않습니다.")


@app.get("/api/saved/{filename}")
async def download_saved_result(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    pattern = r"(?:warning_labels|management_guides)_\d{8}_\d{6}_\d{6}\.pdf"
    if safe_name != filename or not re.fullmatch(pattern, safe_name):
        raise HTTPException(status_code=400, detail="잘못된 파일명입니다.")

    target = SAVE_DIR / safe_name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="저장 파일을 찾을 수 없습니다.")
    return FileResponse(target, media_type="application/pdf", filename=safe_name)


# 기존 다운로드 URL과의 호환성을 유지한다.
@app.get("/api/warning-labels/saved/{filename}")
async def download_saved_warning_labels(filename: str) -> FileResponse:
    return await download_saved_result(filename)

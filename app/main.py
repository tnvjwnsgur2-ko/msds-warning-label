import os
import re
import tempfile
import time
from collections import defaultdict, deque
from io import BytesIO
from pathlib import Path
from typing import List
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.parser import GHS_LABELS_BY_PREFIX, extract_fields, extract_text_from_pdf

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_UPLOAD_FILES = int(os.getenv("MAX_UPLOAD_FILES", "5"))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "50"))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
UPLOAD_CHUNK_SIZE = 1024 * 1024

allowed_origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "").split(",") if origin.strip()]
if not allowed_origins:
    allowed_origins = ["http://127.0.0.1:8000", "http://localhost:8000"]

app = FastAPI(title="MSDS Warning Label Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = PROJECT_DIR.parent / "msds"
STATIC_DIR = BASE_DIR / "static"
PACKAGED_GHS_PICTOGRAM_DIR = STATIC_DIR / "ghs_pictograms"
LOCAL_GHS_PICTOGRAM_DIR = DATA_DIR / "GHS_그림문자"
GHS_PICTOGRAM_DIR = PACKAGED_GHS_PICTOGRAM_DIR if PACKAGED_GHS_PICTOGRAM_DIR.exists() else LOCAL_GHS_PICTOGRAM_DIR
TEMPLATES_DIR = BASE_DIR / "templates"
FONT_REGULAR_CANDIDATES = [
    Path(r"C:\Windows\Fonts\malgun.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
]
FONT_BOLD_CANDIDATES = [
    Path(r"C:\Windows\Fonts\malgunbd.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
]
FONT_REGULAR = "KoreanRegular"
FONT_BOLD = "KoreanBold"

request_history: dict[str, deque[float]] = defaultdict(deque)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if GHS_PICTOGRAM_DIR.exists():
    app.mount("/ghs-pictograms", StaticFiles(directory=str(GHS_PICTOGRAM_DIR)), name="ghs-pictograms")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class ExtractRequest(BaseModel):
    results: List[dict]


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    client_ip = _client_ip(request)
    if request.method in {"POST", "PUT", "PATCH"}:
        _check_rate_limit(client_ip)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
    return response


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded_for:
        return forwarded_for
    return request.client.host if request.client else "unknown"


def _check_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    history = request_history[client_ip]
    while history and now - history[0] > RATE_LIMIT_WINDOW_SECONDS:
        history.popleft()
    if len(history) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")
    history.append(now)


def _first_existing_path(candidates: List[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _register_pdf_fonts() -> tuple[str, str]:
    regular_path = _first_existing_path(FONT_REGULAR_CANDIDATES)
    bold_path = _first_existing_path(FONT_BOLD_CANDIDATES) or regular_path
    try:
        if regular_path and FONT_REGULAR not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(regular_path)))
        if bold_path and FONT_BOLD not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold_path)))
        if regular_path and bold_path:
            return FONT_REGULAR, FONT_BOLD
    except Exception:
        pass
    return "Helvetica", "Helvetica-Bold"


def _pictogram_image_url(label: str) -> str:
    if not GHS_PICTOGRAM_DIR.exists():
        return ""

    for image_path in sorted(GHS_PICTOGRAM_DIR.glob("*")):
        if not image_path.is_file():
            continue
        prefix = image_path.stem.split(".", 1)[0]
        if GHS_LABELS_BY_PREFIX.get(prefix) == label:
            return f"/ghs-pictograms/{quote(image_path.name)}"
    return ""


def _pictogram_image_path(label: str) -> Path | None:
    if not GHS_PICTOGRAM_DIR.exists():
        return None

    for image_path in sorted(GHS_PICTOGRAM_DIR.glob("*")):
        if not image_path.is_file():
            continue
        prefix = image_path.stem.split(".", 1)[0]
        if GHS_LABELS_BY_PREFIX.get(prefix) == label:
            return image_path
    return None


def _pictogram_options() -> List[dict]:
    options = []
    if not GHS_PICTOGRAM_DIR.exists():
        return options

    for image_path in sorted(GHS_PICTOGRAM_DIR.glob("*")):
        if not image_path.is_file():
            continue
        prefix = image_path.stem.split(".", 1)[0]
        label = GHS_LABELS_BY_PREFIX.get(prefix)
        if label:
            options.append({"label": label, "url": f"/ghs-pictograms/{quote(image_path.name)}"})
    return options


def _attach_pictogram_images(result: dict) -> dict:
    result["pictogram_images"] = [
        {"label": label, "url": image_url}
        for label in result.get("pictograms", [])
        if (image_url := _pictogram_image_url(label))
    ]
    return result


def _safe_download_name(results: List[dict]) -> str:
    if not results:
        return "경고표지.pdf"

    first_name = Path(str(results[0].get("filename") or "MSDS")).stem
    first_name = re.sub(r'[\\/:*?"<>|]+', "_", first_name).strip() or "MSDS"
    if len(results) == 1:
        return f"경고표지_{first_name}.pdf"
    return f"경고표지_{first_name}_외{len(results) - 1}건.pdf"


def _content_disposition(filename: str) -> str:
    encoded = quote(filename)
    return f"attachment; filename=warning-label.pdf; filename*=UTF-8''{encoded}"


async def _save_limited_pdf_upload(upload: UploadFile) -> str:
    if not upload.filename or not upload.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드할 수 있습니다.")

    total_size = 0
    first_chunk = True
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_path = temp_file.name
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                if first_chunk:
                    first_chunk = False
                    if not chunk.startswith(b"%PDF-"):
                        raise HTTPException(status_code=400, detail=f"{upload.filename}: 올바른 PDF 파일이 아닙니다.")
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"{upload.filename}: 파일 크기는 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 이하만 허용됩니다.")
                temp_file.write(chunk)
    except Exception:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
        raise

    if total_size == 0:
        Path(temp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"{upload.filename}: 빈 파일입니다.")

    return temp_path

def _validate_pdf_page_count(file_path: str, filename: str) -> None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        if len(reader.pages) > MAX_PDF_PAGES:
            raise HTTPException(status_code=413, detail=f"{filename}: PDF는 최대 {MAX_PDF_PAGES}쪽까지만 처리합니다.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{filename}: PDF를 읽을 수 없습니다.") from exc


def _string_width(text: str, font_name: str, font_size: int) -> float:
    return pdfmetrics.stringWidth(text, font_name, font_size)


def _wrap_text(text: str, font_name: str, font_size: int, max_width: float) -> List[str]:
    text = str(text or "").strip()
    if not text:
        return []

    lines: List[str] = []
    for raw_line in text.splitlines():
        words = raw_line.split(" ")
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if _string_width(candidate, font_name, font_size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = word
            else:
                chunk = ""
                for char in word:
                    candidate_chunk = chunk + char
                    if _string_width(candidate_chunk, font_name, font_size) <= max_width:
                        chunk = candidate_chunk
                    else:
                        if chunk:
                            lines.append(chunk)
                        chunk = char
                current = chunk
        if current:
            lines.append(current)
    return lines


def _draw_wrapped_lines(pdf: canvas.Canvas, lines: List[str], x: float, y: float, max_width: float, font_name: str, font_size: int, line_height: int, bottom: float) -> float:
    pdf.setFont(font_name, font_size)
    for line in lines:
        if y < bottom:
            pdf.showPage()
            y = A4[1] - 48
            pdf.setFont(font_name, font_size)
        for wrapped in _wrap_text(line, font_name, font_size, max_width):
            if y < bottom:
                pdf.showPage()
                y = A4[1] - 48
                pdf.setFont(font_name, font_size)
            pdf.drawString(x, y, wrapped)
            y -= line_height
    return y


def _draw_section(pdf: canvas.Canvas, title: str, lines: List[str], x: float, y: float, max_width: float, regular_font: str, bold_font: str, bottom: float) -> float:
    if y < bottom + 36:
        pdf.showPage()
        y = A4[1] - 48
    pdf.setFont(bold_font, 12)
    pdf.drawString(x, y, title)
    y -= 16
    return _draw_wrapped_lines(pdf, [f"- {line}" for line in lines if line], x + 8, y, max_width - 8, regular_font, 9, 12, bottom) - 8


def _draw_warning_label_pdf(pdf: canvas.Canvas, result: dict, regular_font: str, bold_font: str) -> None:
    width, height = A4
    margin = 42
    bottom = 42
    content_width = width - margin * 2
    y = height - margin

    product_name = str(result.get("product_name") or result.get("filename") or "제품명")
    pdf.setFont(bold_font, 17)
    title_lines = _wrap_text(product_name, bold_font, 17, content_width - 24)
    for line in title_lines[:3]:
        pdf.drawCentredString(width / 2, y, line)
        y -= 22
    y -= 4
    y -= 22

    pictograms = result.get("pictograms", []) or []
    icon_size = 58
    gap = 8
    total_icon_width = len(pictograms) * icon_size + max(0, len(pictograms) - 1) * gap
    icon_x = margin + (content_width - total_icon_width) / 2
    for label in pictograms:
        image_path = _pictogram_image_path(str(label))
        if image_path:
            try:
                pdf.drawImage(ImageReader(str(image_path)), icon_x, y - icon_size + 8, icon_size, icon_size, preserveAspectRatio=True, mask="auto")
            except Exception:
                pass
        icon_x += icon_size + gap
    y -= icon_size + 8

    signal_word = str(result.get("signal_word") or "")
    if signal_word:
        pdf.setFont(bold_font, 16)
        pdf.drawCentredString(width / 2, y, signal_word)
        y -= 24

    y = _draw_section(pdf, "유해·위험 문구", result.get("hazard_phrases", []) or [], margin + 12, y, content_width - 24, regular_font, bold_font, bottom)

    sections = result.get("precaution_sections") or {}
    if sections:
        for section_name in ["예방", "대응", "저장", "폐기"]:
            values = sections.get(section_name) or []
            if values:
                y = _draw_section(pdf, f"예방조치 문구 - {section_name}", values, margin + 12, y, content_width - 24, regular_font, bold_font, bottom)
    else:
        y = _draw_section(pdf, "예방조치 문구", result.get("precaution_statements", []) or [], margin + 12, y, content_width - 24, regular_font, bold_font, bottom)

    supplier = result.get("supplier") or {}
    supplier_lines = [
        str(supplier.get("company_name") or ""),
        str(supplier.get("address") or ""),
        str(supplier.get("emergency_phone") or ""),
    ]
    _draw_section(pdf, "공급자 정보", [line for line in supplier_lines if line], margin + 12, y, content_width - 24, regular_font, bold_font, bottom)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/pictogram-options")
def pictogram_options():
    return {"options": _pictogram_options()}


@app.post("/extract")
async def extract(files: List[UploadFile] = File(default=[])):
    if not files:
        raise HTTPException(status_code=400, detail="PDF 파일을 하나 이상 업로드해주세요.")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"한 번에 최대 {MAX_UPLOAD_FILES}개 파일만 업로드할 수 있습니다.")

    results = []
    for upload in files:
        temp_path = await _save_limited_pdf_upload(upload)
        try:
            _validate_pdf_page_count(temp_path, upload.filename or "PDF")
            text = extract_text_from_pdf(temp_path)
            extracted = extract_fields(text, temp_path)
            extracted["filename"] = upload.filename
            results.append(_attach_pictogram_images(extracted))
        finally:
            Path(temp_path).unlink(missing_ok=True)
            await upload.close()

    return {"results": results}


@app.post("/download-pdf")
def download_pdf(payload: ExtractRequest):
    if len(payload.results) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"한 번에 최대 {MAX_UPLOAD_FILES}개 결과만 저장할 수 있습니다.")

    buffer = BytesIO()
    regular_font, bold_font = _register_pdf_fonts()
    pdf = canvas.Canvas(buffer, pagesize=A4)

    for index, result in enumerate(payload.results):
        if index:
            pdf.showPage()
        _draw_warning_label_pdf(pdf, result, regular_font, bold_font)

    pdf.save()
    buffer.seek(0)
    filename = _safe_download_name(payload.results)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


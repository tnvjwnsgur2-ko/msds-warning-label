from __future__ import annotations

import json
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


def _save_json(*, document_type: str, collection_key: str, payload: dict[str, Any], filename_prefix: str) -> dict[str, str]:
    records = payload.get(collection_key)
    if not isinstance(records, list) or not records:
        raise HTTPException(status_code=400, detail="저장할 결과가 없습니다.")

    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{filename_prefix}_{timestamp}.json"
    target = SAVE_DIR / filename

    saved_payload: dict[str, Any] = {
        "document_type": document_type,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "record_count": len(records),
        collection_key: records,
    }
    target.write_text(json.dumps(saved_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "message": "최종 내용을 저장했습니다.",
        "filename": filename,
        "download_url": f"/api/saved/{filename}",
    }


@app.post("/api/warning-labels/save")
async def save_warning_labels(payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    """사용자가 수정한 최종 W-1~W-6 통합 결과를 저장한다."""
    _validate_final_records(payload.get("labels"), expected_modules=6)
    return _save_json(
        document_type="warning_label",
        collection_key="labels",
        payload=payload,
        filename_prefix="warning_labels",
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
    return _save_json(
        document_type="management_guide",
        collection_key="guides",
        payload=payload,
        filename_prefix="management_guides",
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
    pattern = r"(?:warning_labels|management_guides)_\d{8}_\d{6}_\d{6}\.json"
    if safe_name != filename or not re.fullmatch(pattern, safe_name):
        raise HTTPException(status_code=400, detail="잘못된 파일명입니다.")

    target = SAVE_DIR / safe_name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="저장 파일을 찾을 수 없습니다.")
    return FileResponse(target, media_type="application/json", filename=safe_name)


# 기존 다운로드 URL과의 호환성을 유지한다.
@app.get("/api/warning-labels/saved/{filename}")
async def download_saved_warning_labels(filename: str) -> FileResponse:
    return await download_saved_result(filename)

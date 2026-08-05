import asyncio

import fitz
import httpx

from app import main as webapp


async def request(method: str, url: str, **kwargs):
    transport = httpx.ASGITransport(app=webapp.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, **kwargs)


def call(method: str, url: str, **kwargs):
    return asyncio.run(request(method, url, **kwargs))


def pdf_bytes(text: str = "sample") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def edited_record(module_prefix: str, count: int):
    fields_by_prefix = {
        "W": ["product_name", "pictograms", "signal_word", "hazard_statements", "precautionary_statements", "supplier_information"],
        "M": ["product_name", "hazard_risk_summary", "safe_handling_precautions", "personal_protective_equipment", "emergency_response"],
    }
    fields = fields_by_prefix[module_prefix]
    modules = [
        {"module_id": f"{module_prefix}-{index}", "label": "항목", "field": field, "text": f"EDITED-{field}"}
        for index, field in enumerate(fields, start=1)
    ]
    if module_prefix == "W":
        modules[1]["pictogram_assets"] = [{"id": "2", "label": "인화성", "url": "/api/pictograms/2"}]
    return {"source_file": "a.pdf", "modules": modules, "final_fields": {item["field"]: item["text"] for item in modules}}


def saved_pdf_text(path):
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document), sum(len(page.get_images()) for page in document)


def test_health_and_pictogram_asset():
    assert call("GET", "/api/health").status_code == 200
    catalog = call("GET", "/api/pictograms").json()["assets"]
    assert len(catalog) == 9
    assert webapp.pictogram_path(catalog[0]["id"]).is_file()


def test_ui_is_not_cached_and_uses_one_work_name_input_per_management_card():
    page_response = asyncio.run(webapp.index())
    script_response = asyncio.run(webapp.javascript())
    page = (webapp.BASE_DIR / "index.html").read_text(encoding="utf-8")
    script = (webapp.BASE_DIR / "app.js").read_text(encoding="utf-8")

    assert page_response.headers["cache-control"].startswith("no-store")
    assert script_response.headers["cache-control"].startswith("no-store")
    assert page.count('data-field="work_name"') == 1
    assert 'id="workNameInput"' not in page
    assert "각 PDF 카드마다 서로 다른 작업명을 입력할 수 있습니다" in page
    assert '>PDF 저장</button>' in page
    assert "관리요령 작업명 (파일별 필수)" in script
    assert "workNamesByFile.set(fileKey(file), workNameInput.value)" in script
    assert "app.js?v=20260805-5" in page


def test_netlify_frontend_calls_render_directly_to_avoid_proxy_timeout():
    project_dir = webapp.BASE_DIR.parent
    page = (project_dir / "netlify" / "index.html").read_text(encoding="utf-8")
    script = (project_dir / "netlify" / "app.js").read_text(encoding="utf-8")
    config = (project_dir / "netlify" / "config.js").read_text(encoding="utf-8")

    assert 'config.js?v=20260805-6' in page
    assert 'window.MSDS_API_BASE_URL = "https://msds-warning-label-api.onrender.com"' in config
    assert "window.MSDS_API_BASE_URL" in script
    assert "PDF 처리 시간이 길어 서버 응답이 지연됐습니다" in script
    assert "fetch(apiUrl('/api/pictograms'))" in script
    assert "image.src = apiUrl(asset.url)" in script
    assert "fetch(apiUrl(config.endpoint)" in script
    assert "fetch(apiUrl(config.saveEndpoint)" in script
    assert "anchor.href = apiUrl(saved.download_url)" in script


def test_upload_limit_and_non_pdf_validation():
    eleven = [("files", (f"{i}.pdf", pdf_bytes(), "application/pdf")) for i in range(11)]
    assert call("POST", "/api/warning-labels", files=eleven).status_code == 400

    response = call("POST", "/api/warning-labels", files={"files": ("bad.txt", b"bad", "text/plain")})
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "error"


def test_ten_pdfs_are_accepted_in_upload_order(monkeypatch):
    def service(path):
        modules = [{"module_id": f"W-{i}", "field": f"f{i}", "text": "ok"} for i in range(1, 7)]
        return {"page_count": 1, "implementation": "test", "modules": modules, "fields": {}, "missing_modules": []}

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(webapp, "run_warning_modules", service)
    monkeypatch.setattr(webapp, "run_in_threadpool", immediate)
    files = [("files", (f"{index}.pdf", pdf_bytes(), "application/pdf")) for index in range(10)]
    data = call("POST", "/api/warning-labels", files=files).json()
    assert data["success_count"] == 10
    assert [item["source_file"] for item in data["results"]] == [f"{index}.pdf" for index in range(10)]


def test_per_pdf_failure_does_not_stop_later_files(monkeypatch):
    calls = []

    def service(path):
        calls.append(path.name)
        if len(calls) == 1:
            raise RuntimeError("internal path should not leak")
        modules = [{"module_id": f"W-{i}", "field": f"f{i}", "text": "ok"} for i in range(1, 7)]
        return {"page_count": 1, "implementation": "test", "modules": modules, "fields": {}, "missing_modules": []}

    monkeypatch.setattr(webapp, "run_warning_modules", service)
    async def immediate(function, *args):
        return function(*args)
    monkeypatch.setattr(webapp, "run_in_threadpool", immediate)
    files = [("files", (name, pdf_bytes(), "application/pdf")) for name in ("first.pdf", "second.pdf")]
    response = call("POST", "/api/warning-labels", files=files)
    data = response.json()
    assert [item["status"] for item in data["results"]] == ["error", "success"]
    assert data["results"][0]["error"] == "자동 처리 중 오류가 발생했습니다."
    assert [item["source_file"] for item in data["results"]] == ["first.pdf", "second.pdf"]


def test_save_uses_edited_values_and_requires_work_name(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "SAVE_DIR", tmp_path)
    warning = call("POST", "/api/warning-labels/save", json={"labels": [edited_record("W", 6)]})
    assert warning.status_code == 200
    assert warning.json()["filename"].endswith(".pdf")
    warning_text, image_count = saved_pdf_text(tmp_path / warning.json()["filename"])
    assert "EDITED-product_name" in warning_text
    assert image_count == 1
    download = asyncio.run(webapp.download_saved_result(warning.json()["filename"]))
    assert download.media_type == "application/pdf"
    assert (tmp_path / warning.json()["filename"]).read_bytes().startswith(b"%PDF-")

    record = edited_record("M", 5)
    assert call("POST", "/api/management-guides/save", json={"guides": [record]}).status_code == 400
    record["work_name"] = " Paint task "
    valid = call("POST", "/api/management-guides/save", json={"guides": [record]})
    assert valid.status_code == 200
    management_text, _ = saved_pdf_text(tmp_path / valid.json()["filename"])
    assert "Paint task" in management_text
    assert "EDITED-emergency_response" in management_text


def test_management_requires_a_separate_work_name_for_every_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "SAVE_DIR", tmp_path)
    first = edited_record("M", 5)
    first.update({"source_file": "paint.pdf", "work_name": "Paint task"})
    second = edited_record("M", 5)
    second.update({"source_file": "cleaner.pdf", "work_name": " "})
    invalid = call("POST", "/api/management-guides/save", json={"guides": [first, second]})
    assert invalid.status_code == 400
    assert "cleaner.pdf" in invalid.json()["detail"]

    second["work_name"] = "Cleaning task"
    valid = call("POST", "/api/management-guides/save", json={"guides": [first, second]})
    assert valid.status_code == 200
    text, _ = saved_pdf_text(tmp_path / valid.json()["filename"])
    assert "Paint task" in text
    assert "Cleaning task" in text
    with fitz.open(tmp_path / valid.json()["filename"]) as document:
        assert len(document) >= 2

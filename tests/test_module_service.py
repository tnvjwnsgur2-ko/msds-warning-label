from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image

from app import module_service


def make_pdf(path: Path, text: str = "sample") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_warning_adapter_maps_legacy_contract_once(monkeypatch, tmp_path):
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    calls = {"text": 0, "fields": 0}

    def extract_text(path):
        calls["text"] += 1
        return "legacy text"

    def extract_fields(text, path):
        calls["fields"] += 1
        return {
            "product_name": "제품 A",
            "pictograms": ["인화성"],
            "signal_word": "위험",
            "hazard_phrases": ["H225 고인화성 액체 및 증기"],
            "precaution_statements": ["P210 열로부터 멀리하시오"],
            "supplier": {"company_name": "회사", "address": "주소", "emergency_phone": "119"},
        }

    monkeypatch.setattr(module_service.legacy_parser, "extract_text_from_pdf", extract_text)
    monkeypatch.setattr(module_service.legacy_parser, "extract_fields", extract_fields)
    monkeypatch.setattr(module_service, "extract_pictogram_assets", lambda path: [module_service.pictogram_catalog()[1]])
    result = module_service.run_warning_modules(pdf)

    assert calls == {"text": 1, "fields": 1}
    assert [item["module_id"] for item in result["modules"]] == [f"W-{i}" for i in range(1, 7)]
    assert result["fields"]["product_name"] == "제품 A"
    assert "전화번호: 119" in result["fields"]["supplier_information"]
    assert result["modules"][1]["pictogram_assets"][0]["url"].startswith("/api/pictograms/")


def test_code_statements_rejoin_wrapped_lines():
    text = """
    H373 장기간 또는 반복노출 되면 장기(간, 신경계, 청각, 청력
    기관)에 손상을 일으킬 수 있음 (흡입)
    -
    P303 + P361 + P353 피부에 묻으면 피부를 물로 씻으시오
    [또는 샤워하시오].
    -
    """
    assert module_service._code_statements(text, "H") == [
        "H373 장기간 또는 반복노출 되면 장기(간, 신경계, 청각, 청력 기관)에 손상을 일으킬 수 있음 (흡입)"
    ]
    assert module_service._code_statements(text, "P") == [
        "P303+P361+P353 피부에 묻으면 피부를 물로 씻으시오 [또는 샤워하시오]."
    ]


def test_embedded_pictograms_are_matched_by_image_not_keywords(tmp_path):
    pdf = tmp_path / "symbols.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    for index, asset_id in enumerate(("2", "4", "9")):
        source = module_service.pictogram_path(asset_id)
        with Image.open(source) as image:
            buffer = BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
        x = 60 + index * 150
        page.insert_image(fitz.Rect(x, 100, x + 100, 200), stream=buffer.getvalue())
    document.save(pdf)
    document.close()

    assert [asset["id"] for asset in module_service.extract_pictogram_assets(pdf)] == ["2", "4", "9"]


def test_signal_word_is_read_after_signal_label():
    text = "유해·위험성 분류\n○ 신호어\n경고\n○ 유해·위험 문구\nH315 피부 자극"
    assert module_service._signal_word(text, "위험") == "경고"


def test_signal_word_supports_not_applicable():
    text = "○ 신호어\n해당 없음\n○ 유해·위험 문구"
    assert module_service._signal_word(text, "경고") == "해당없음"


def test_signal_word_supports_inline_and_not_applicable_variants():
    assert module_service._signal_word("2) 신호어 : 경고", "위험") == "경고"
    assert module_service._signal_word("· 신호어\n- 해당사항없음.", "위험") == "해당없음"
    assert module_service._signal_word("해당사항없음. · -신호어", "위험") == "해당없음"
    assert module_service._signal_word("l                           -신호어\n해당사항없음.", "위험") == "해당없음"


def test_product_name_supports_separate_manufacturer_item_name():
    section = "1. 화학제품과 회사에 관한 정보\n가. 품명\n하절기 LPG (C3/C4 Mixture)\n나. 제품의 권고용도"
    assert module_service._product_name(section) == "하절기 LPG (C3/C4 Mixture)"


def test_supplier_includes_labelled_phone_and_excludes_fax():
    section = """1. 화학제품과 회사에 관한 정보
다. 공급자 정보
회사명: 예시화학
주소: 서울시
정보제공 및 긴급연락처: 031) 467-6114 기술팀
전화번호: 052-231-3653 FAX 번호: 052-231-2209
"""
    result = module_service._supplier(
        {"company_name": "예시화학", "address": "서울시", "emergency_phone": ""},
        section,
    )
    assert "전화번호: 031) 467-6114" in result
    assert "전화번호: 052-231-3653" in result
    assert "052-231-2209" not in result


def test_section_boundary_does_not_treat_decimal_product_as_heading():
    pages = ["""1. 화학제품과 회사에 관한 정보
가. 제품명
2.0% Sulfur B-A
다. 공급자 정보
2. 유해·위험성
가. 유해·위험성 분류
"""]
    section, page_numbers = module_service._section(pages, 1)
    assert "2.0% Sulfur B-A" in section
    assert "2. 유해·위험성" not in section
    assert page_numbers == [1]


def test_warning_adapter_marks_pictogram_not_applicable(monkeypatch, tmp_path):
    pdf = tmp_path / "not-applicable.pdf"
    make_pdf(pdf)
    monkeypatch.setattr(module_service.legacy_parser, "extract_text_from_pdf", lambda path: "")
    monkeypatch.setattr(module_service.legacy_parser, "extract_fields", lambda text, path: {
        "product_name": "제품", "signal_word": "", "supplier": {},
    })
    monkeypatch.setattr(module_service, "_sorted_page_texts", lambda path: [
        "2. 유해·위험성\n○ 그림문자\n- 해당없음\n○ 신호어\n- 해당없음\n○ 유해·위험 문구\n- 해당없음\n3. 구성성분"
    ])
    monkeypatch.setattr(module_service, "extract_pictogram_assets", lambda path: [])
    result = module_service.run_warning_modules(pdf)
    assert result["fields"]["pictograms"] == "해당없음"
    assert "W-2" not in result["missing_modules"]


def test_code_statements_find_old_inline_precaution_codes():
    text = "- 예방 : P201 사용 전 취급 설명서를 확보하시오.\n- 대응 : P308+P313 노출되면 의학적인 조치를 받으시오."
    assert module_service._code_statements(text, "P") == [
        "P201 사용 전 취급 설명서를 확보하시오.",
        "P308+P313 노출되면 의학적인 조치를 받으시오.",
    ]


def test_vector_pictogram_fallback_maps_hazard_code_families():
    assets = module_service.infer_pictogram_assets([
        "H226 인화성 액체 및 증기",
        "H304 삼켜서 기도로 유입되면 치명적일 수 있음",
        "H315 피부에 자극을 일으킴",
    ])
    assert [asset["id"] for asset in assets] == ["2", "4", "9"]
    assert all(asset["detected_by"] == "hazard_code_inference" for asset in assets)


def test_old_inline_classification_is_preserved():
    text = "가. 유해·위험성 분류 : 인화성 액체 구분 3\n- 급성 독성 구분 4"
    assert module_service._classification_summary(text) == "- 인화성 액체 구분 3\n- 급성 독성 구분 4"


def test_management_sections_do_not_leak_into_other_modules(monkeypatch, tmp_path):
    pdf = tmp_path / "management.pdf"
    make_pdf(pdf)
    monkeypatch.setattr(module_service.legacy_parser, "extract_fields", lambda text, path: {"product_name": "제품 A"})
    monkeypatch.setattr(module_service, "_sorted_page_texts", lambda path: ["""1. 화학제품과 회사에 관한 정보
가. 제품명: 제품 A
2. 유해·위험성
가. 유해·위험성 분류: 인화성 액체 구분 2
나. 예방조치 문구
H225 고인화성 액체 및 증기
3. 구성성분의 명칭 및 함유량
CAS 번호 123-45-6
4. 응급조치 요령
가. 눈에 들어갔을 때 물로 씻으시오.
5. 폭발·화재 시 대처방법
가. 분말소화제를 사용하시오.
6. 누출사고 시 대처방법
가. 누출물을 회수하시오.
7. 취급 및 저장방법
가. 안전취급요령 환기하시오.
8. 노출방지 및 개인보호구
다. 개인 보호구
방독마스크를 착용하시오.
보안경을 착용하시오.
내화학성 보호장갑을 착용하시오.
9. 물리·화학적 특성
가. 외관: 액체
"""])
    result = module_service.run_management_modules(pdf)
    assert result["implementation"] == "layout_sorted_section_adapter"
    assert [item["module_id"] for item in result["modules"]] == [f"M-{i}" for i in range(1, 6)]
    fields = result["fields"]
    assert "CAS 번호" not in fields["hazard_risk_summary"]
    assert "6. 누출" not in fields["safe_handling_precautions"]
    assert "9. 물리" not in fields["safe_handling_precautions"]
    assert "11. 독성" not in fields["personal_protective_equipment"]
    assert "보안경" in fields["personal_protective_equipment"].splitlines()
    assert "착용" not in fields["personal_protective_equipment"]
    assert "[응급조치]" in fields["emergency_response"]
    assert "[화재 시 조치]" in fields["emergency_response"]
    assert "[누출 시 조치]" in fields["emergency_response"]
    assert "7. 취급" not in fields["emergency_response"]


def test_m4_keeps_only_explicit_protective_equipment_names():
    section = """8. 노출 방지 및 개인 보호구
다. 개인 보호구
호흡기 보호: 인증 받은 방독마스크를 착용하시오.
눈 보호: 작업 시 보안경을 착용하시오.
손 보호: 적합한 내화학성 보호장갑을 착용하시오.
신체 보호: 내화학성 보호복을 착용하시오.
"""
    assert module_service._protective_equipment_names(section) == (
        "방독마스크\n보안경\n내화학성 보호장갑\n내화학성 보호복"
    )

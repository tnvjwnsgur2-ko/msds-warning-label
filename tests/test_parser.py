from app.parser import extract_fields


def test_extract_fields_basic():
    sample_text = """
    1. 화학제품과 회사에 관한 정보
    가. 제품명
    - 일신 Lacquer Spray (진녹색)

    다. 공급자 정보
    - 회사명
    : 주식회사 일신케미칼
    - 주소
    : 충청북도 진천군 덕산읍 신척산단 1로 2
    - 긴급 전화번호
    : TEL : 043)536-0161

    2. 유해성·위험성
    나. 예방조치 문구를 포함한 경고 표지 항목
    ○ 신호어
    - 위험
    ○ 유해·위험 문구
    - H220 극인화성 가스
    - H225 고인화성 액체 및 증기
    ○ 예방조치문구
    1) 예방
    - P201 사용 전 취급 설명서를 확보하시오.
    - P210 열·스파크·화염·고열로부터 멀리하시오.
    2) 대응
    - P301+P310 삼켰다면 즉시 의료기관의 진찰을 받으시오.
    3) 저장
    - P403+P235 환기가 잘 되는 곳에 보관하고 저온으로 유지하시오.
    4) 폐기
    - P501 MSDS의 13.폐기 시 주의사항을 참고하여 내용물과 용기를 폐기하시오.
    """

    result = extract_fields(sample_text)

    assert result["product_name"] == "일신 Lacquer Spray (진녹색)"
    assert result["signal_word"] == "위험"
    assert any("H220" in entry for entry in result["hazard_phrases"])
    assert result["supplier"]["company_name"] == "주식회사 일신케미칼"
    assert len(result["precaution_statements"]) >= 2
    assert result["precaution_sections"]["예방"]
    assert result["precaution_sections"]["대응"]
    assert result["precaution_sections"]["저장"]
    assert result["precaution_sections"]["폐기"]

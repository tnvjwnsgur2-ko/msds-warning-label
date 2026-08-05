"""Application service adapting the repository's existing MSDS parser to the web API.

The repository has one legacy warning-label parser rather than separately named
W-1 ... W-6 functions.  This module deliberately keeps that parser unchanged,
parses a PDF once, and normalises its dictionary result into the module-2 API.
Management modules do not exist in the repository, so a replaceable adapter uses
layout-sorted PDF text and numbered MSDS section boundaries.
"""
from __future__ import annotations

import importlib.util
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

BASE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = BASE_DIR.parent
LEGACY_PARSER_PATH = BASE_DIR / "legacy_parser.py"
PICTOGRAM_DIR = BASE_DIR / "static" / "ghs_pictograms"

WARNING_MODULES = (
    ("W-1", "제품명", "product_name"),
    ("W-2", "그림문자", "pictograms"),
    ("W-3", "신호어", "signal_word"),
    ("W-4", "유해·위험 문구", "hazard_statements"),
    ("W-5", "예방조치 문구", "precautionary_statements"),
    ("W-6", "공급자 정보", "supplier_information"),
)

SECTION_TITLES = {
    1: r"화학제품과\s*회사",
    2: r"유해성?[·ㆍ\- ]?위험성",
    3: r"구성성분",
    4: r"응급조치",
    5: r"(?:폭발\s*[·ㆍ\-]?\s*)?화재\s*시(?:의)?\s*(?:대처방법|조치)",
    6: r"누출\s*(?:사고\s*)?시\s*(?:대처방법|조치)",
    7: r"취급\s*및\s*저장",
    8: r"노출\s*방지\s*및\s*개인\s*보호구",
    9: r"물리[·ㆍ\- ]?화학적\s*특성",
    10: r"안정성\s*및\s*반응성",
    11: r"독성에\s*관한\s*정보",
    12: r"환경에\s*미치는\s*영향",
    13: r"폐기\s*시\s*주의사항",
    14: r"운송에\s*필요한\s*정보",
    15: r"법적\s*규제현황",
    16: r"(?:그\s*밖의|기타)\s*참고사항",
}

SECTION_ANCHORS = {
    1: r"화학제품",
    2: r"유해",
    3: r"구성성분",
    4: r"응급조치",
    5: r"(?:화재|폭발)",
    6: r"누출",
    7: r"취급",
    8: r"노출",
    9: r"물리",
    10: r"안정성",
    11: r"독성",
    12: r"환경",
    13: r"폐기",
    14: r"운송",
    15: r"규제",
    16: r"참고",
}


def _load_legacy_parser() -> Any:
    if not LEGACY_PARSER_PATH.is_file():
        raise RuntimeError("기존 MSDS 경고표지 파서를 찾을 수 없습니다.")
    spec = importlib.util.spec_from_file_location("msds_legacy_parser", LEGACY_PARSER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("기존 MSDS 경고표지 파서를 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy_parser = _load_legacy_parser()


def pictogram_catalog() -> list[dict[str, str]]:
    assets = []
    if PICTOGRAM_DIR.is_dir():
        for path in sorted(PICTOGRAM_DIR.glob("*.gif")):
            asset_id = path.name.split(".", 1)[0]
            assets.append({
                "id": asset_id,
                "label": path.stem.split(".", 1)[-1].replace("_", " · "),
                "url": f"/api/pictograms/{asset_id}",
            })
    return assets


def pictogram_path(asset_id: str) -> Path | None:
    return next((path for path in PICTOGRAM_DIR.glob(f"{asset_id}.*.gif") if path.is_file()), None)


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+82[-\s]?)?(?:\(?0\d{1,2}\)?)[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)"
    r"|(?<!\d)\d{3,4}-\d{4}(?!\d)"
)


def _supplier_phone_numbers(section_1: str) -> list[str]:
    """Extract labelled supplier/emergency phone numbers, excluding fax numbers."""
    numbers: list[str] = []
    phone_label = re.compile(
        r"(?:정보\s*제공.*긴급\s*연락처|긴급.*(?:전화|연락)|전화\s*번호|연락처|\bTEL\b)",
        re.IGNORECASE,
    )
    for raw_line in section_1.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not phone_label.search(line):
            continue
        # A phone and fax often share a line. Only the content before FAX is
        # part of W-6's supplier telephone number.
        phone_part = re.split(r"(?:FAX|팩스)\s*(?:번호)?", line, maxsplit=1, flags=re.IGNORECASE)[0]
        numbers.extend(match.group(0).strip() for match in PHONE_PATTERN.finditer(phone_part))
    return list(dict.fromkeys(numbers))


def _supplier(value: Any, section_1: str = "") -> str:
    if not isinstance(value, dict):
        return _text(value)
    company = _text(value.get("company_name"))
    address = _text(value.get("address"))
    legacy_phone = _text(value.get("emergency_phone"))
    phones = _supplier_phone_numbers(section_1)
    if legacy_phone:
        legacy_numbers = [match.group(0).strip() for match in PHONE_PATTERN.finditer(legacy_phone)]
        if legacy_numbers:
            phones = list(dict.fromkeys([*legacy_numbers, *phones]))
        elif not phones:
            phones = [legacy_phone]
    return "\n".join(part for part in (
        company,
        address,
        *(f"전화번호: {phone}" for phone in phones),
    ) if part)


def _symbol_mask(image: Image.Image, size: int = 64) -> Image.Image | None:
    """Return a size-normalised mask of only the dark symbol, excluding red borders."""
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        image = white.convert("RGB")
    else:
        image = image.convert("RGB")
    dark = image.convert("L").point(lambda value: 255 if value < 100 else 0)
    bbox = dark.getbbox()
    if bbox is None:
        return None
    cropped = dark.crop(bbox)
    width, height = cropped.size
    if width < 5 or height < 5:
        return None
    side = max(width, height)
    canvas = Image.new("L", (side, side), 0)
    canvas.paste(cropped, ((side - width) // 2, (side - height) // 2))
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def _mask_mse(left: Image.Image, right: Image.Image) -> float:
    difference = ImageChops.difference(left, right)
    rms = ImageStat.Stat(difference).rms[0]
    return (rms / 255.0) ** 2


def extract_pictogram_assets(pdf_path: str | Path) -> list[dict[str, str]]:
    """Identify embedded GHS symbols by image content, never by whole-document keywords."""
    references: list[tuple[dict[str, str], Image.Image]] = []
    for asset in pictogram_catalog():
        path = pictogram_path(asset["id"])
        if path is None:
            continue
        with Image.open(path) as image:
            mask = _symbol_mask(image)
        if mask is not None:
            references.append((asset, mask))

    matches: dict[str, dict[str, str]] = {}
    with fitz.open(pdf_path) as document:
        # Korean MSDS warning-label symbols normally occur before section 3.
        for page in list(document)[:3]:
            for image_info in page.get_images(full=True):
                try:
                    xref, smask = image_info[0], image_info[1]
                    if smask:
                        base_pixmap = fitz.Pixmap(document, xref)
                        mask_pixmap = fitz.Pixmap(document, smask)
                        if base_pixmap.alpha:
                            base_pixmap = fitz.Pixmap(base_pixmap, 0)
                        pixmap = fitz.Pixmap(base_pixmap, mask_pixmap)
                        image_bytes = pixmap.tobytes("png")
                    else:
                        image_bytes = document.extract_image(xref)["image"]
                    with Image.open(BytesIO(image_bytes)) as image:
                        width, height = image.size
                        ratio = width / max(height, 1)
                        if not 0.65 <= ratio <= 1.35:
                            continue
                        candidate = _symbol_mask(image)
                except (KeyError, ValueError, UnidentifiedImageError):
                    continue
                if candidate is None or not references:
                    continue
                scores = sorted((_mask_mse(candidate, mask), asset) for asset, mask in references)
                best_score, best_asset = scores[0]
                second_score = scores[1][0] if len(scores) > 1 else 1.0
                if best_score <= 0.11 and second_score - best_score >= 0.02:
                    matches[best_asset["id"]] = best_asset
    return [asset for asset in pictogram_catalog() if asset["id"] in matches]


def infer_pictogram_assets(hazard_statements: list[str]) -> list[dict[str, str]]:
    """Fallback to standard H-code families when a PDF uses vector-only symbols."""
    selected: set[str] = set()
    for statement in hazard_statements:
        match = re.match(r"H(\d{3})", statement)
        if not match:
            continue
        number = int(match.group(1))
        if number in {200, 201, 202, 203, 204, 205, 240, 241}:
            selected.add("1")
        if number in {220, 221, 222, 223, 224, 225, 226, 228, 242, 250, 251, 252, 260, 261}:
            selected.add("2")
        if number in {300, 310, 330}:
            selected.add("3")
        if number in {304, 334, 340, 341, 350, 351, 360, 361, 370, 371, 372, 373}:
            selected.add("4")
        if number in {400, 410, 411}:
            selected.add("5")
        if number in {270, 271, 272}:
            selected.add("6")
        if number in {280, 281}:
            selected.add("7")
        if number in {290, 314, 318}:
            selected.add("8")
        if number in {302, 312, 315, 317, 319, 332, 335, 336}:
            selected.add("9")
    return [{**asset, "detected_by": "hazard_code_inference"} for asset in pictogram_catalog() if asset["id"] in selected]


def _code_statements(text: str, prefix: str) -> list[str]:
    """Extract H/P statements and repair continuations split by PDF layout."""
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    code = re.compile(rf"\b({prefix}\d{{3}}(?:\s*\+\s*P?\d{{3}})*)\s*(.*)$", re.IGNORECASE)
    statements: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = code.search(line)
        if not match:
            continue
        normalised_code = re.sub(r"\s*\+\s*", "+", match.group(1).upper())
        value = f"{normalised_code} {match.group(2)}".strip()
        value = value.rstrip(" -")
        next_index = index + 1
        while next_index < len(lines):
            continuation = lines[next_index].strip()
            if not continuation or continuation == "-" or code.search(continuation):
                break
            unclosed_parenthesis = value.count("(") > value.count(")")
            explicit_continuation = continuation.startswith(("[", "기관)"))
            if not unclosed_parenthesis and not explicit_continuation:
                break
            value = f"{value} {continuation}".strip().rstrip(" -")
            next_index += 1
        if value not in seen:
            statements.append(value)
            seen.add(value)
    return statements


def _signal_word(text: str, fallback: str = "") -> str:
    inline = re.search(
        r"(?mi)^\s*(?:\d+\)\s*)?[·•○q-]*\s*신호어\s*[:：]\s*(위험|경고|해당(?:사항)?\s*없음)",
        text,
    )
    if inline:
        value = re.sub(r"\s+", "", inline.group(1))
        return "해당없음" if "해당" in value else value

    # A few table-based PDFs extract the value before the label on the same
    # line: ``해당사항없음 · -신호어``.
    reversed_inline = re.search(
        r"(?mi)^\s*(위험|경고|해당(?:사항)?\s*없음)[.\s·•-]{0,40}신호어\s*$",
        text,
    )
    if reversed_inline:
        value = re.sub(r"\s+", "", reversed_inline.group(1))
        return "해당없음" if "해당" in value else value

    label = re.search(
        r"(?mi)^\s*(?:\d+\)\s*)?(?:[lq]\s*)?[^가-힣A-Za-z0-9\n]{0,50}신호어\s*[:：]?\s*$",
        text,
    )
    if label:
        section_end = re.search(r"(?m)^\s*○?\s*유해[·ㆍ\- ]?위험\s*문구\s*$", text[label.end():])
        search_end = label.end() + section_end.start() if section_end else min(len(text), label.end() + 2500)
        # Some multi-column PDFs extract the label before the value and the next
        # heading before the value. Search a bounded warning-label area.
        if section_end:
            search_end = min(len(text), label.end() + 2500)
        match = re.search(
            r"(?mi)^\s*[-·•]?\s*(위험|경고|해당(?:사항)?\s*없음)[.\s]*$",
            text[label.end():search_end],
        )
        if match:
            value = re.sub(r"\s+", "", match.group(1))
            return "해당없음" if "해당" in value else value
    return fallback


def _product_name(section_1: str, fallback: str = "") -> str:
    """Read either ``제품명`` or manufacturer variants such as ``품명``."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in section_1.splitlines()]
    for index, line in enumerate(lines):
        match = re.match(r"^가\.\s*(?:제품명|품명)\s*[:：]?\s*(.*)$", line)
        if not match:
            continue
        inline = match.group(1).strip(" -:：")
        if inline:
            return inline
        for candidate in lines[index + 1:index + 5]:
            if candidate and not re.match(r"^[나다라]\.\s*", candidate):
                return candidate.strip(" -:：")
    return fallback


def _sorted_page_texts(pdf_path: str | Path) -> list[str]:
    with fitz.open(pdf_path) as document:
        return [page.get_text("text", sort=True).replace("\r", "\n") for page in document]


def _section(page_texts: list[str], number: int) -> tuple[str, list[int]]:
    title = SECTION_TITLES.get(number, r"[^\n]+")
    next_title = SECTION_TITLES.get(number + 1, r"[^\n]+")
    anchor = SECTION_ANCHORS.get(number, title)
    next_anchor = SECTION_ANCHORS.get(number + 1, next_title)
    # Some older PDFs duplicate heading glyphs (for example
    # ``8.노출8.8.8.노출...``). The anchor alternative accepts that damaged
    # representation while still requiring the correct section keyword.
    heading = re.compile(
        rf"(?mi)^\s*{number}\s*[\.．](?:\s*{title}|[^\n]{{0,160}}?{anchor})[^\n]*$"
    )
    next_heading = re.compile(
        rf"(?mi)^\s*{number + 1}\s*[\.．](?:\s*{next_title}|[^\n]{{0,160}}?{next_anchor})[^\n]*$"
    )
    chunks: list[str] = []
    pages: list[int] = []
    active = False
    for page_number, text in enumerate(page_texts, start=1):
        start = heading.search(text)
        if start:
            active = True
            text = text[start.start():]
        if not active:
            continue
        end = next_heading.search(text)
        if end:
            text = text[:end.start()]
        if text.strip():
            chunks.append(text)
            pages.append(page_number)
        if end:
            break
    return "\n".join(chunks), pages


def _subsection(text: str, start_pattern: str, end_pattern: str | None = None) -> str:
    start = re.search(start_pattern, text, re.MULTILINE | re.IGNORECASE)
    if not start:
        return ""
    end = re.search(end_pattern, text[start.end():], re.MULTILINE | re.IGNORECASE) if end_pattern else None
    stop = start.end() + end.start() if end else len(text)
    return text[start.start():stop]


def _clean_section(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line == "-":
            continue
        if re.fullmatch(r"(?:Page\s+)?\d+\s*(?:of|/)\s*\d+", line, re.IGNORECASE):
            continue
        inline_item = re.match(r"^((?:[가나다라마바사아자차카타파하]\.[^-]+|○\s*[^-]+))\s+-\s+(.+)$", line)
        if inline_item:
            cleaned_lines.append(inline_item.group(1).strip())
            line = f"- {inline_item.group(2).strip()}"
        is_new_item = bool(re.match(
            r"^(?:\d+\s*[\.．]|[가나다라마바사아자차카타파하]\.|[○*]|[-·•]|\[[^]]+\]|H\d{3}|P\d{3})",
            line,
        ))
        if cleaned_lines and not is_new_item:
            previous = cleaned_lines[-1]
            previous_is_heading = bool(re.match(r"^(?:\d+\s*[\.．]|[가나다라마바사아자차카타파하]\.|[○*]|\[[^]]+\])", previous))
            if not previous_is_heading:
                previous_word = re.search(r"([가-힣]+)$", previous)
                current_word = re.match(r"^([가-힣]+)", line)
                split_inside_word = bool(
                    previous_word and current_word
                    and (len(previous_word.group(1)) == 1 or len(current_word.group(1)) == 1)
                )
                separator = "" if split_inside_word else " "
                cleaned_lines[-1] = f"{previous}{separator}{line}"
                continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _classification_summary(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    if lines and re.match(r"^가\.\s*유해", lines[0]):
        heading = lines.pop(0)
        inline = re.split(r"분류\s*[:：]?\s*", heading, maxsplit=1)
        if len(inline) == 2 and inline[1].strip():
            lines.insert(0, inline[1].strip())
    return "\n".join(line if line.startswith("-") else f"- {line}" for line in lines)


def _protective_equipment_names(text: str) -> str:
    """Return only PPE names explicitly present in section 8, without instructions."""
    candidates = (
        ("방독마스크", r"방독\s*마스크"),
        ("방진마스크", r"방진\s*마스크"),
        ("송기마스크", r"송기\s*마스크"),
        ("자급식 공기호흡기", r"자급식(?:\s*공기)?\s*호흡기"),
        ("공기호흡기", r"공기\s*호흡기"),
        ("공기여과식 호흡보호구", r"공기\s*여과식\s*호흡\s*보호구"),
        ("유기용제용 호흡용 보호구", r"유기\s*용제용\s*호흡용\s*보호구"),
        ("호흡용 보호구", r"호흡용\s*보호구"),
        ("보안경", r"보안경"),
        ("안전안경", r"안전\s*안경"),
        ("고글", r"고글"),
        ("보안면", r"보안면"),
        ("안면 보호구", r"안면\s*보호구"),
        ("내화학성 보호장갑", r"내화학성\s*보호\s*장갑"),
        ("내화학성 장갑", r"내화학성\s*장갑"),
        ("불투과성 보호장갑", r"불투과성\s*보호\s*장갑"),
        ("고무장갑", r"고무\s*장갑"),
        ("보호장갑", r"보호\s*장갑"),
        ("보호앞치마", r"보호\s*앞치마"),
        ("내화학성 보호복", r"내화학성\s*보호복"),
        ("내화학성 보호의", r"내화학성\s*보호의"),
        ("전신 보호복", r"전신\s*보호복"),
        ("보호복", r"보호복"),
        ("안전화", r"안전화"),
    )
    found = [name for name, pattern in candidates if re.search(pattern, text, re.IGNORECASE)]

    # Do not repeat a generic name when the same PPE category has a specific name.
    specific_respirators = {"방독마스크", "방진마스크", "송기마스크", "자급식 공기호흡기", "공기호흡기", "공기여과식 호흡보호구", "유기용제용 호흡용 보호구"}
    specific_gloves = {"내화학성 보호장갑", "내화학성 장갑", "불투과성 보호장갑", "고무장갑"}
    specific_clothing = {"내화학성 보호복", "내화학성 보호의", "전신 보호복"}
    if specific_respirators.intersection(found):
        found = [name for name in found if name != "호흡용 보호구"]
    if specific_gloves.intersection(found):
        found = [name for name in found if name != "보호장갑"]
    if specific_clothing.intersection(found):
        found = [name for name in found if name != "보호복"]
    return "\n".join(dict.fromkeys(found))


def _management_module(module_id: str, label: str, field: str, text: str, pages: list[int]) -> dict[str, Any]:
    return {
        "module_id": module_id,
        "label": label,
        "field": field,
        "text": text,
        "matched": bool(text),
        "pages": pages,
    }


def _module(module_id: str, label: str, field: str, value: Any, **extra: Any) -> dict[str, Any]:
    text = _text(value)
    return {
        "module_id": module_id,
        "label": label,
        "field": field,
        "text": text,
        "matched": bool(text),
        "pages": [],
        **extra,
    }


def run_warning_modules(pdf_path: str | Path) -> dict[str, Any]:
    """Call the existing parser once and adapt each returned field to W-1...W-6."""
    path = Path(pdf_path)
    extracted_text = legacy_parser.extract_text_from_pdf(str(path))
    values = legacy_parser.extract_fields(extracted_text, str(path))
    sorted_pages = _sorted_page_texts(path)
    section_1, _ = _section(sorted_pages, 1)
    warning_section, _ = _section(sorted_pages, 2)
    structured_text = warning_section or extracted_text
    pictogram_assets = extract_pictogram_assets(path)
    hazard_statements = _code_statements(structured_text, "H")
    if not pictogram_assets:
        pictogram_assets = infer_pictogram_assets(hazard_statements)
    pictogram_names = [asset["label"] for asset in pictogram_assets]
    precautionary_statements = _code_statements(structured_text, "P")
    signal_word = _signal_word(structured_text, _text(values.get("signal_word")))
    pictogram_value: Any = pictogram_names
    if not pictogram_assets and signal_word == "해당없음" and not hazard_statements:
        pictogram_value = "해당없음"
    mapped = {
        "W-1": _product_name(section_1, _text(values.get("product_name"))),
        "W-2": pictogram_value,
        "W-3": signal_word,
        "W-4": hazard_statements,
        "W-5": precautionary_statements,
        "W-6": _supplier(values.get("supplier"), section_1),
    }
    modules = []
    for module_id, label, field in WARNING_MODULES:
        extra = {"pictogram_assets": pictogram_assets} if module_id == "W-2" else {}
        modules.append(_module(module_id, label, field, mapped[module_id], **extra))
    with fitz.open(path) as document:
        page_count = len(document)
    return {
        "page_count": page_count,
        "implementation": "app.legacy_parser_with_web_adapter",
        "modules": modules,
        "fields": {item["field"]: item["text"] for item in modules},
        "missing_modules": [item["module_id"] for item in modules if not item["text"]],
    }


def run_management_modules(pdf_path: str | Path) -> dict[str, Any]:
    """Extract management fields from numbered MSDS sections using layout-sorted text."""
    path = Path(pdf_path)
    page_texts = _sorted_page_texts(path)
    full_text = "\n".join(page_texts)
    legacy_values = legacy_parser.extract_fields(full_text, str(path))

    section_2, pages_2 = _section(page_texts, 2)
    section_1, pages_1 = _section(page_texts, 1)
    classification = _subsection(
        section_2,
        r"^\s*가\.\s*유해성?[·ㆍ\- ]?위험성\s*분류.*$",
        r"^\s*나\.\s*예방조치",
    )
    hazards = _code_statements(section_2 or full_text, "H")
    m2_parts = []
    cleaned_classification = _classification_summary(classification)
    if cleaned_classification:
        m2_parts.append(f"[유해성·위험성 분류]\n{cleaned_classification}")
    if hazards:
        m2_parts.append(f"[유해·위험 문구]\n" + "\n".join(hazards))

    section_7, pages_7 = _section(page_texts, 7)
    section_8, pages_8 = _section(page_texts, 8)
    protection = _protective_equipment_names(section_8)
    section_parts: list[tuple[str, str, list[int]]] = []
    for number, heading in ((4, "응급조치"), (5, "화재 시 조치"), (6, "누출 시 조치")):
        section_text, section_pages = _section(page_texts, number)
        cleaned = _clean_section(section_text)
        if cleaned:
            section_parts.append((heading, cleaned, section_pages))
    m5_pages = sorted({page for _, _, pages in section_parts for page in pages})
    m5_text = "\n\n".join(f"[{heading}]\n{text}" for heading, text, _ in section_parts)

    modules = [
        _management_module("M-1", "제품명", "product_name", _product_name(section_1, _text(legacy_values.get("product_name"))), pages_1 or [1]),
        _management_module("M-2", "건강 및 환경 유해성·물리적 위험성", "hazard_risk_summary", "\n\n".join(m2_parts), pages_2),
        _management_module("M-3", "안전 및 보건상의 취급주의 사항", "safe_handling_precautions", _clean_section(section_7), pages_7),
        _management_module("M-4", "적절한 보호구", "personal_protective_equipment", protection, pages_8),
        _management_module("M-5", "응급조치 요령 및 사고 시 대처방법", "emergency_response", m5_text, m5_pages),
    ]
    return {
        "page_count": len(page_texts),
        "implementation": "layout_sorted_section_adapter",
        "modules": modules,
        "fields": {item["field"]: item["text"] for item in modules},
        "missing_modules": [item["module_id"] for item in modules if not item["text"]],
    }

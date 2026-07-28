import re
from io import BytesIO
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageChops, ImageOps, ImageStat, UnidentifiedImageError


GHS_LABELS_BY_PREFIX = {
    "1": "폭발성",
    "2": "인화성",
    "3": "급성독성",
    "4": "건강유해성",
    "5": "수생환경유해성",
    "6": "산화성",
    "7": "고압가스",
    "8": "부식성",
    "9": "경고",
}

PICTOGRAM_IMAGE_SUFFIXES = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}
PICTOGRAM_SIZE = 96
PICTOGRAM_MATCH_THRESHOLD = 45.0


def extract_text_from_pdf(file_path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(file_path)
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n".join(pages)


def _clean_value(text: str) -> str:
    return text.strip().lstrip("-: ").strip()


def _extract_hazard_phrases(text: str) -> List[str]:
    phrases: List[str] = []
    matches = re.finditer(r"H\d{3}[^\n]*", text)
    seen = set()
    for match in matches:
        phrase = _clean_value(match.group(0))
        if phrase and phrase not in seen:
            phrases.append(phrase)
            seen.add(phrase)
    return phrases


def _extract_precaution_sections(text: str) -> Dict[str, List[str]]:
    sections = {"예방": [], "대응": [], "저장": [], "폐기": []}

    all_phrases = list(re.finditer(r"P(\d{3})(?:\+P?\d{3})?[^\n]*", text))

    seen = set()
    for phrase_match in all_phrases:
        phrase_text = _clean_value(phrase_match.group(0))
        if phrase_text in seen:
            continue
        seen.add(phrase_text)

        p_num = int(phrase_match.group(1))

        if 200 <= p_num <= 299:
            sections["예방"].append(phrase_text)
        elif 300 <= p_num <= 399:
            sections["대응"].append(phrase_text)
        elif 400 <= p_num <= 499:
            sections["저장"].append(phrase_text)
        elif p_num >= 500:
            sections["폐기"].append(phrase_text)
        else:
            sections["예방"].append(phrase_text)

    return sections


def _extract_numbered_section(text: str, section_number: int) -> List[str]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines()]
    start_pattern = re.compile(rf"^\s*{section_number}\s*\.\s*")
    next_pattern = re.compile(r"^\s*\d{1,2}\s*\.\s+[가-힣]")
    section_lines: List[str] = []
    collecting = False

    for line in lines:
        if not line:
            continue
        if start_pattern.match(line):
            collecting = True
            section_lines.append(_clean_value(start_pattern.sub("", line)))
            continue
        if collecting and next_pattern.match(line):
            break
        if collecting:
            section_lines.append(_clean_value(line))

    return _dedupe_lines([line for line in section_lines if line])


def _extract_protective_equipment(section_lines: List[str]) -> List[str]:
    if not section_lines:
        return []

    start_index = -1
    for index, line in enumerate(section_lines):
        compact = re.sub(r"\s+", "", line)
        if compact.startswith("다.") and ("개인보호구" in compact or "개인보호장비" in compact):
            start_index = index
            break
        if compact.startswith("다") and ("개인보호구" in compact or "개인보호장비" in compact):
            start_index = index
            break

    if start_index < 0:
        for index, line in enumerate(section_lines):
            compact = re.sub(r"\s+", "", line)
            if "개인보호구" in compact or "개인보호장비" in compact:
                start_index = index
                break

    if start_index < 0:
        return []

    return _extract_subsection_lines(section_lines[start_index:])


def _extract_hazard_classification(section_lines: List[str]) -> List[str]:
    if not section_lines:
        return []

    start_index = -1
    for index, line in enumerate(section_lines):
        compact = re.sub(r"\s+", "", line)
        compact_key = re.sub(r"[\s·ㆍ.]+", "", line)
        if compact.startswith("가.") and "유해성위험성분류" in compact_key:
            start_index = index
            break
        if "유해성위험성분류" in compact_key:
            start_index = index
            break

    if start_index < 0:
        return section_lines

    return _extract_subsection_lines(section_lines[start_index:])


def _extract_management_hazard_classification(text: str, section_lines: List[str]) -> List[str]:
    classification = _extract_hazard_classification(section_lines)
    if len(classification) > 1:
        return classification

    leading_lines: List[str] = []
    for line in text.replace("\r\n", "\n").splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue
        if "○ 그림문자" in clean_line or "○ 신호어" in clean_line:
            break
        if clean_line.startswith("-"):
            leading_lines.append(clean_line)

    if leading_lines:
        return _dedupe_lines(classification + leading_lines)
    return classification


def _extract_subsection_lines(lines: List[str]) -> List[str]:
    selected: List[str] = []
    first_line = True
    subsection_pattern = re.compile(r"^[가-힣]\.\s*")

    for line in lines:
        if not first_line and subsection_pattern.match(line):
            break
        selected.append(line)
        first_line = False

    return _dedupe_lines(selected)


def _dedupe_lines(lines: List[str]) -> List[str]:
    clean_lines: List[str] = []
    seen = set()
    skip_prefixes = ("자료없음", "해당없음", "해당 없음")
    for line in lines:
        clean_line = _clean_value(line)
        if not clean_line or clean_line in seen:
            continue
        if clean_line.startswith(skip_prefixes):
            continue
        clean_lines.append(clean_line)
        seen.add(clean_line)
    return clean_lines


def _collect_pictograms_from_text(normalized_text: str) -> List[str]:
    pictograms: List[str] = []
    keyword_labels = {
        "폭발": "폭발성",
        "인화성": "인화성",
        "급성 독성": "급성독성",
        "급성독성": "급성독성",
        "건강": "건강유해성",
        "수생환경": "수생환경유해성",
        "환경": "수생환경유해성",
        "산화성": "산화성",
        "고압가스": "고압가스",
        "부식성": "부식성",
        "경고": "경고",
    }
    for keyword, label in keyword_labels.items():
        if keyword in normalized_text and label not in pictograms:
            pictograms.append(label)
    return pictograms


def _default_pictogram_package_dir() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path(__file__).resolve().parent / "static" / "ghs_pictograms",
        Path(__file__).resolve().parent / "static" / "pictograms",
        project_root / "msds" / "GHS_그림문자",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _label_from_reference_path(image_path: Path) -> str:
    prefix = image_path.stem.split(".", 1)[0]
    if prefix in GHS_LABELS_BY_PREFIX:
        return GHS_LABELS_BY_PREFIX[prefix]
    return image_path.stem.split(".", 1)[-1]


def _open_image_on_white(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _prepare_pictogram_image(image: Image.Image) -> Image.Image:
    prepared = _open_image_on_white(image)
    grayscale = ImageOps.grayscale(prepared)
    content_mask = grayscale.point(lambda value: 255 if value < 245 else 0)
    bbox = content_mask.getbbox()
    if bbox:
        prepared = prepared.crop(bbox)

    contained = ImageOps.contain(
        prepared,
        (PICTOGRAM_SIZE, PICTOGRAM_SIZE),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", (PICTOGRAM_SIZE, PICTOGRAM_SIZE), "white")
    canvas.paste(contained, ((PICTOGRAM_SIZE - contained.width) // 2, (PICTOGRAM_SIZE - contained.height) // 2))
    return canvas


def _image_difference_score(left: Image.Image, right: Image.Image) -> float:
    difference = ImageChops.difference(left, right)
    channel_means = ImageStat.Stat(difference).mean
    return sum(channel_means) / len(channel_means)


def _load_reference_pictograms(package_dir_path: Path) -> List[tuple[str, Image.Image]]:
    reference_images: List[tuple[str, Image.Image]] = []
    if not package_dir_path.exists():
        return reference_images

    for image_path in sorted(package_dir_path.glob("*")):
        if not image_path.is_file() or image_path.suffix.lower() not in PICTOGRAM_IMAGE_SUFFIXES:
            continue
        try:
            with Image.open(image_path) as image:
                reference_images.append((_label_from_reference_path(image_path), _prepare_pictogram_image(image)))
        except UnidentifiedImageError:
            continue

    return reference_images


def _is_probable_pictogram_image(image: Image.Image) -> bool:
    if image.width < 30 or image.height < 30:
        return False
    aspect_ratio = image.width / image.height
    if not 0.75 <= aspect_ratio <= 1.33:
        return False
    return True


def _is_masked_gas_pictogram(image: Image.Image) -> bool:
    if image.width < 100 or image.height < 100:
        return False

    rgb_image = image.convert("RGB")
    corner = rgb_image.getpixel((0, 0))
    if max(corner) > 20:
        return False

    sample = ImageOps.contain(rgb_image, (64, 64), Image.Resampling.NEAREST)
    red_pixels = 0
    for red, green, blue in sample.getdata():
        if red > 150 and green < 90 and blue < 90:
            red_pixels += 1

    return red_pixels > 20


def match_pictograms_from_pdf(pdf_path: str, package_dir: str | None = None) -> List[str]:
    package_dir_path = Path(package_dir) if package_dir else _default_pictogram_package_dir()
    reference_images = _load_reference_pictograms(package_dir_path)
    if not reference_images:
        return []

    try:
        import fitz
    except ImportError:
        return []

    matched_names: List[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            for img in page.get_images(full=True):
                try:
                    xref = img[0]
                    image_bytes = doc.extract_image(xref)["image"]
                    with Image.open(BytesIO(image_bytes)) as current_image:
                        if not _is_probable_pictogram_image(current_image):
                            continue
                        if _is_masked_gas_pictogram(current_image):
                            if "고압가스" not in matched_names:
                                matched_names.append("고압가스")
                            continue
                        prepared_current = _prepare_pictogram_image(current_image)
                except (KeyError, UnidentifiedImageError, ValueError):
                    continue

                best_name = ""
                best_score: float | None = None
                for name, reference_image in reference_images:
                    score = _image_difference_score(prepared_current, reference_image)
                    if best_score is None or score < best_score:
                        best_score = score
                        best_name = name

                if best_name and best_score is not None and best_score <= PICTOGRAM_MATCH_THRESHOLD:
                    if best_name not in matched_names:
                        matched_names.append(best_name)

    return matched_names


def extract_fields(text: str, file_path: str | None = None) -> Dict[str, object]:
    normalized = text.replace("\r\n", "\n")
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]

    def find_value(labels: List[str]) -> str:
        for index, line in enumerate(lines):
            for label in labels:
                if label in line:
                    remainder = line.split(label, 1)[1].strip()
                    if remainder:
                        return _clean_value(remainder)
                    for next_line in lines[index + 1:index + 4]:
                        if next_line.startswith("-") or next_line.startswith(":"):
                            value = _clean_value(next_line)
                            if value:
                                return value
                        if next_line:
                            return _clean_value(next_line)
        return ""

    product_name = find_value(["제품명", "제품의 명칭"]) or ""
    company_name = find_value(["회사명"])
    address = find_value(["주소"])
    emergency = find_value(["긴급 전화번호", "긴급전화번호"])

    signal_word = ""
    if "위험" in normalized:
        signal_word = "위험"
    elif "경고" in normalized:
        signal_word = "경고"

    hazard_phrases = _extract_hazard_phrases(normalized)
    precaution_sections = _extract_precaution_sections(normalized)
    precaution_statements = [statement for values in precaution_sections.values() for statement in values]
    hazard_section = _extract_numbered_section(normalized, 2)
    first_aid_section = _extract_numbered_section(normalized, 4)
    handling_section = _extract_numbered_section(normalized, 7)
    exposure_section = _extract_numbered_section(normalized, 8)

    pictograms = match_pictograms_from_pdf(file_path) if file_path else []
    if not pictograms:
        pictograms = _collect_pictograms_from_text(normalized)

    if not pictograms:
        pictograms = ["이미지 기반 심볼 확인 필요"]

    return {
        "product_name": product_name,
        "pictograms": pictograms,
        "signal_word": signal_word,
        "hazard_phrases": hazard_phrases,
        "precaution_sections": precaution_sections,
        "precaution_statements": precaution_statements,
        "supplier": {
            "company_name": company_name,
            "address": address,
            "emergency_phone": emergency,
        },
        "management": {
            "product_name": product_name,
            "hazard_risk": _extract_management_hazard_classification(normalized, hazard_section),
            "handling_precautions": handling_section or precaution_sections.get("예방", []),
            "protective_equipment": _extract_protective_equipment(exposure_section),
            "first_aid": first_aid_section,
        },
    }



import re
from io import BytesIO
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError


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
    # Find all H-codes with their descriptions using regex
    phrases: List[str] = []
    matches = re.finditer(r'H\d{3}[^\n]*', text)
    seen = set()
    for match in matches:
        phrase = _clean_value(match.group(0))
        if phrase and phrase not in seen:
            phrases.append(phrase)
            seen.add(phrase)
    return phrases


def _extract_precaution_sections(text: str) -> Dict[str, List[str]]:
    sections = {"예방": [], "대응": [], "저장": [], "폐기": []}
    
    # Find all P-phrases
    all_phrases = list(re.finditer(r'P(\d{3})(?:\+P?\d{3})?[^\n]*', text))
    
    # Categorize P-codes based on their number ranges (GHS standard)
    # This is more reliable than text position since PDF layout varies
    seen = set()
    for phrase_match in all_phrases:
        phrase_text = _clean_value(phrase_match.group(0))
        if phrase_text in seen:
            continue
        seen.add(phrase_text)
        
        # Extract the first P-code number
        p_num = int(phrase_match.group(1))
        
        # Categorize based on P-code number range
        if 200 <= p_num <= 299:
            sections["예방"].append(phrase_text)
        elif 300 <= p_num <= 399:
            sections["대응"].append(phrase_text)
        elif 400 <= p_num <= 499:
            sections["저장"].append(phrase_text)
        elif p_num >= 500:
            sections["폐기"].append(phrase_text)
        else:
            sections["예방"].append(phrase_text)  # Default to prevention for P1xx
    
    return sections


def _collect_pictograms_from_text(normalized_text: str) -> List[str]:
    pictograms: List[str] = []
    for keyword in ["불꽃", "해골", "부식성", "환경", "건강", "가스", "폭발", "인화성", "독성"]:
        if keyword in normalized_text:
            pictograms.append(keyword)
    return pictograms


def _get_image_signature(image: Image.Image) -> List[float]:
    grayscale = ImageOps.grayscale(image)
    resized = grayscale.resize((64, 64), Image.Resampling.LANCZOS)
    return list(ImageStat.Stat(resized).mean)


def match_pictograms_from_pdf(pdf_path: str, package_dir: str | None = None) -> List[str]:
    package_dir_path = Path(package_dir) if package_dir else Path(__file__).resolve().parent / "static" / "pictograms"
    if not package_dir_path.exists():
        return []

    reference_images: List[tuple[str, Image.Image]] = []
    for image_path in sorted(package_dir_path.glob("*")):
        if image_path.is_file() and image_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            try:
                reference_images.append((image_path.stem, Image.open(image_path).convert("RGB")))
            except UnidentifiedImageError:
                continue

    if not reference_images:
        return []

    try:
        import fitz
    except ImportError:
        return []

    doc = fitz.open(pdf_path)
    matched_names: List[str] = []
    for page in doc:
        for img in page.get_images(full=True):
            try:
                xref = img[0]
                image_bytes = doc.extract_image(xref)["image"]
                current_image = Image.open(BytesIO(image_bytes)).convert("RGB")
            except (KeyError, UnidentifiedImageError, ValueError):
                continue

            if current_image.width < 20 or current_image.height < 20:
                continue

            best_name = None
            best_score = None
            current_signature = _get_image_signature(current_image)
            for name, reference_image in reference_images:
                reference_signature = _get_image_signature(reference_image.resize(current_image.size, Image.Resampling.LANCZOS))
                score = sum(abs(a - b) for a, b in zip(current_signature, reference_signature))
                if best_score is None or score < best_score:
                    best_score = score
                    best_name = name

            if best_name and (best_score is not None and best_score < 40000):
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

    # Pass the full text to the extraction functions (not lines)
    hazard_phrases = _extract_hazard_phrases(normalized)
    precaution_sections = _extract_precaution_sections(normalized)
    precaution_statements = [statement for values in precaution_sections.values() for statement in values]

    pictograms = match_pictograms_from_pdf(file_path, None) if file_path else []
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
    }

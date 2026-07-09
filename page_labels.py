import re
from functools import lru_cache
from pathlib import Path

import pdfplumber

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT_ROOT / "data"

_ISOLATED_LABEL_PATTERN = re.compile(r"^\s*([IVXLCDM]+|\d{1,4})\s*$", re.IGNORECASE)
_NUM_TOKEN_PATTERN = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
_ROMAN_TOKEN_PATTERN = re.compile(r"\b[IVXLCDM]{1,8}\b", re.IGNORECASE)
_ALPHA_OR_CJK_PATTERN = re.compile(r"[A-Za-z\u4e00-\u9fff]")


def _roman_to_int(value: str) -> int | None:
    mapping = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    value = (value or "").upper().strip()
    if not value or any(ch not in mapping for ch in value):
        return None
    total = 0
    previous = 0
    for ch in reversed(value):
        current = mapping[ch]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total


def _normalize_label_token(token: str) -> tuple[str, str, int] | None:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        value = int(token)
        if 0 < value <= 999:
            return token, "arabic", value
        return None
    roman_value = _roman_to_int(token)
    if roman_value and roman_value <= 9999:
        return token.upper(), "roman", roman_value
    return None


def _int_to_roman(value: int) -> str:
    pairs = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    remaining = max(int(value), 1)
    parts: list[str] = []
    for number, token in pairs:
        while remaining >= number:
            parts.append(token)
            remaining -= number
    return "".join(parts)


@lru_cache(maxsize=128)
def _pdf_page_texts(relative_path: str) -> tuple[str, ...]:
    path = DATA_ROOT / relative_path
    if not path.is_file() or path.suffix.lower() != ".pdf":
        return tuple()
    texts = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                texts.append((page.extract_text() or "").strip())
    except Exception:
        return tuple()
    return tuple(texts)


def _line_priority(line_index: int, total_lines: int) -> int:
    if total_lines <= 0:
        return 0
    if line_index <= 1 or line_index >= total_lines - 2:
        return 3
    if line_index <= 4 or line_index >= total_lines - 5:
        return 2
    return 1


def _line_has_text_payload(line: str) -> bool:
    return bool(_ALPHA_OR_CJK_PATTERN.search(line or ""))


def _line_candidates(line: str, line_index: int, total_lines: int) -> list[tuple[str, str, int, int]]:
    candidates: list[tuple[str, str, int, int]] = []
    priority = _line_priority(line_index, total_lines)

    isolated = _ISOLATED_LABEL_PATTERN.match(line)
    if isolated:
        normalized = _normalize_label_token(isolated.group(1))
        if normalized:
            score = 140 if priority >= 3 else 88 if priority == 2 else 72
            candidates.append((*normalized, score))
        return candidates

    if _line_has_text_payload(line):
        return candidates

    valid_tokens = []
    for token in _NUM_TOKEN_PATTERN.findall(line):
        normalized = _normalize_label_token(token)
        if normalized:
            valid_tokens.append(normalized)
    for token in _ROMAN_TOKEN_PATTERN.findall(line):
        normalized = _normalize_label_token(token)
        if normalized and normalized not in valid_tokens:
            valid_tokens.append(normalized)

    if len(valid_tokens) == 1:
        candidates.append((*valid_tokens[0], 80 + priority * 10))
    elif len(valid_tokens) == 2:
        for item in valid_tokens:
            candidates.append((*item, 52 + priority * 6))
    return candidates


def _extract_page_candidates(page_text: str) -> list[tuple[str, str, int, int]]:
    lines = [line.strip() for line in (page_text or "").splitlines() if line.strip()]
    if not lines:
        return []

    total_lines = len(lines)
    sampled_positions = list(range(min(6, total_lines)))
    tail_start = max(total_lines - 6, 0)
    sampled_positions.extend(range(tail_start, total_lines))
    candidates: list[tuple[str, str, int, int]] = []
    seen: set[tuple[str, str, int]] = set()
    for position in sampled_positions:
        for label, style, numeric_value, score in _line_candidates(lines[position], position, total_lines):
            if (label, style, score) in seen:
                continue
            seen.add((label, style, score))
            candidates.append((label, style, numeric_value, score))
    candidates.sort(key=lambda item: (item[3], item[2]), reverse=True)
    return candidates


@lru_cache(maxsize=256)
def _pdf_page_labels(relative_path: str) -> tuple[str, ...]:
    path = DATA_ROOT / relative_path
    if not path.is_file() or path.suffix.lower() != ".pdf":
        return tuple()
    try:
        from pypdf import PdfReader

        labels = getattr(PdfReader(str(path)), "page_labels", None) or []
        return tuple(str(label) for label in labels)
    except Exception:
        return tuple()


def _format_label(style: str, value: int) -> str:
    if style == "roman":
        return _int_to_roman(value)
    return str(value)


@lru_cache(maxsize=256)
def _document_page_labels(relative_path: str) -> dict[int, str]:
    labels: dict[int, str] = {}
    pdf_labels = _pdf_page_labels(relative_path)
    total_pages = len(pdf_labels)
    pdf_texts = _pdf_page_texts(relative_path)
    total_pages = max(total_pages, len(pdf_texts))

    explicit_candidates: dict[int, tuple[str, str, int, int] | None] = {}
    for physical_page in range(1, total_pages + 1):
        page_text = pdf_texts[physical_page - 1] if physical_page - 1 < len(pdf_texts) else ""
        candidates = _extract_page_candidates(page_text)
        explicit_candidates[physical_page] = candidates[0] if candidates else None

    prev_style: str | None = None
    prev_value: int | None = None

    for physical_page in range(1, total_pages + 1):
        chosen = explicit_candidates.get(physical_page)
        pdf_label = pdf_labels[physical_page - 1] if physical_page - 1 < len(pdf_labels) else ""
        pdf_normalized = _normalize_label_token(pdf_label) if pdf_label else None

        if prev_style and prev_value is not None:
            expected_value = prev_value + 1
            if chosen:
                _, chosen_style, chosen_value, chosen_score = chosen
                if chosen_style == prev_style and chosen_value == expected_value:
                    pass
                elif chosen_style != prev_style and chosen_score >= 140:
                    pass
                else:
                    chosen = (_format_label(prev_style, expected_value), prev_style, expected_value, 95)
            else:
                chosen = (_format_label(prev_style, expected_value), prev_style, expected_value, 95)

        if not chosen and pdf_normalized:
            chosen = (*pdf_normalized, 1)

        if chosen:
            labels[physical_page] = chosen[0]
            prev_style = chosen[1]
            prev_value = chosen[2]
        elif pdf_label:
            labels[physical_page] = pdf_label
        else:
            labels[physical_page] = str(physical_page)

    return labels


def resolve_page_label(relative_path: str | None, physical_page: int | None) -> str | None:
    if not relative_path or not physical_page:
        return None
    try:
        return _document_page_labels(relative_path).get(int(physical_page))
    except Exception:
        return None

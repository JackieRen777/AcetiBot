import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import pdfplumber
import fitz

DATA_ROOT = Path("./data")
PARSED_DIR = Path("./parsed_docs")

_DOC_TYPE_LABELS = {
    "文献": "文献",
    "风味文献": "风味文献",
    "专利": "专利",
    "标准": "标准",
    "消费者评价": "消费者评价",
}

_PATENT_TAG_PATTERN = re.compile(r"^\(\d{2}\)")
_DATE_PATTERN = re.compile(r"(20\d{2}[.\-/年]\s*\d{1,2}(?:[.\-/月]\s*\d{1,2})?)")
_YEAR_PATTERN = re.compile(r"(20\d{2})")
_STANDARD_CODE_PATTERN = re.compile(r"\b(?:GB|GB/T|QB|Q/[\w-]+|DB\d*/T|T/[\w-]+)\s*[\w.-]*\d[\w.-]*", re.IGNORECASE)
_PAPER_FILENAME_PATTERN = re.compile(r"^(?P<author>.+?)\s*[-_]\s*(?P<year>20\d{2})\s*[-_]\s*(?P<title>.+)$")
_PATENT_FILENAME_PATTERN = re.compile(r"^(?P<patent_no>[A-Z]{0,2}\d[\dA-Z.]+?)[-_](?P<title>.+)$", re.IGNORECASE)


def _normalize_text(value: str | None) -> str:
    text = (value or "").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return text


def _safe_stem(stem: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", stem).strip("._")
    return safe[:80] or "document"


def _artifact_dir_for_relative_path(relative_path: str) -> Path:
    file_path = DATA_ROOT / relative_path
    digest = hashlib.sha1(str(file_path).encode("utf-8")).hexdigest()[:8]
    return PARSED_DIR / f"{_safe_stem(file_path.stem)}__{digest}"


@lru_cache(maxsize=512)
def _manifest_for(relative_path: str) -> dict:
    manifest_path = _artifact_dir_for_relative_path(relative_path) / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=512)
def _page_one_text(relative_path: str) -> str:
    page_one_path = _artifact_dir_for_relative_path(relative_path) / "pages" / "page_001.md"
    if page_one_path.is_file():
        try:
            return page_one_path.read_text(encoding="utf-8")
        except Exception:
            return ""
    combined_path = _artifact_dir_for_relative_path(relative_path) / "combined.md"
    if combined_path.is_file():
        try:
            return combined_path.read_text(encoding="utf-8")
        except Exception:
            return ""
    file_path = DATA_ROOT / relative_path
    if not file_path.is_file() or file_path.suffix.lower() != ".pdf":
        return ""
    try:
        with pdfplumber.open(file_path) as pdf:
            if pdf.pages:
                text = (pdf.pages[0].extract_text() or "").strip()
                if text:
                    return text
    except Exception:
        pass
    try:
        doc = fitz.open(file_path)
        if doc.page_count:
            text = (doc[0].get_text("text") or "").strip()
            if text:
                return text
    except Exception:
        return ""
    return ""


def _source_name(relative_path: str | None, metadata: dict | None = None) -> str:
    if metadata and metadata.get("source"):
        return str(metadata.get("source"))
    if relative_path:
        return Path(relative_path).name
    return "未知来源"


def _source_stem(relative_path: str | None, metadata: dict | None = None) -> str:
    return Path(_source_name(relative_path, metadata)).stem


def _display_category(metadata: dict | None, relative_path: str | None) -> str:
    if metadata and metadata.get("doc_type"):
        return _DOC_TYPE_LABELS.get(str(metadata["doc_type"]), str(metadata["doc_type"]))
    if relative_path:
        top_level = Path(relative_path).parts[0] if Path(relative_path).parts else ""
        return {
            "papers": "文献",
            "flavor": "风味文献",
            "patents": "专利",
            "standards": "标准",
            "consumer": "消费者评价",
        }.get(top_level, "资料")
    return "资料"


def _compact_lines(text: str) -> list[str]:
    return [_normalize_text(line) for line in text.splitlines() if _normalize_text(line)]


def _collect_block(lines: list[str], start_index: int, stop_predicate) -> str:
    values: list[str] = []
    for line in lines[start_index + 1:]:
        if stop_predicate(line):
            break
        values.append(line)
    return _normalize_text(" ".join(values))


def _parse_patent_metadata(relative_path: str, metadata: dict | None, page_text: str) -> dict:
    lines = _compact_lines(page_text)
    title = ""
    inventors = ""
    publish_date = ""
    publication_no = ""
    applicant = ""

    for index, line in enumerate(lines):
        if line.startswith("(54)发明名称"):
            inline = _normalize_text(line.replace("(54)发明名称", ""))
            title = inline or _collect_block(lines, index, lambda item: _PATENT_TAG_PATTERN.match(item))
        elif line.startswith("(43)申请公布日"):
            publish_date = _normalize_text(line.replace("(43)申请公布日", ""))
        elif line.startswith("(10)申请公布号"):
            publication_no = _normalize_text(line.replace("(10)申请公布号", ""))
        elif line.startswith("(71)申请人"):
            applicant = _normalize_text(line.replace("(71)申请人", ""))

    inventors_block_match = re.search(r"\(72\)发明人\s*(.*?)(?:\(\d{2}\)|$)", page_text, re.S)
    if inventors_block_match:
        inventor_tokens = re.findall(r"[\u4e00-\u9fff·]{2,8}", inventors_block_match.group(1))
        inventors = "、".join(token for token in inventor_tokens if len(token) >= 2)
    elif inventors:
        inventors = "、".join(re.findall(r"[\u4e00-\u9fff·]{2,8}", inventors))

    fallback_stem = _source_stem(relative_path, metadata)
    fallback_title = re.sub(r"_FullTextImage$", "", fallback_stem, flags=re.IGNORECASE)
    fallback_patent_no = re.sub(r"_FullTextImage$", "", Path(fallback_stem).name, flags=re.IGNORECASE)
    filename_match = _PATENT_FILENAME_PATTERN.match(fallback_title)
    if filename_match:
        fallback_patent_no = filename_match.group("patent_no")
        fallback_title = filename_match.group("title").replace("_", " ").strip()
    title = title or fallback_title

    return {
        "display_title": title,
        "display_author": inventors or "发明人未标注",
        "display_time": publish_date or "时间未标注",
        "display_category": "专利",
        "inventors": inventors,
        "assignee": applicant,
        "patent_no": publication_no or fallback_patent_no,
    }


def _extract_standard_title(lines: list[str]) -> str:
    banned = {"前言", "目次", "目录", "范围", "术语和定义", "术语定义"}
    for index, line in enumerate(lines[:12]):
        if "标准" in line and index + 1 < len(lines):
            candidate = lines[index + 1]
            if (
                candidate
                and candidate not in banned
                and len(candidate) <= 30
                and not _STANDARD_CODE_PATTERN.search(candidate)
            ):
                return candidate
    for line in lines[:12]:
        if (
            line not in banned
            and re.fullmatch(r"[\u4e00-\u9fffA-Za-z（）()·《》\- ]{2,40}", line)
            and "标准" not in line
        ):
            return line
    return ""


def _extract_standard_issuer(lines: list[str]) -> str:
    for index, line in enumerate(lines):
        if line.endswith("发布"):
            previous = [
                item for item in lines[max(0, index - 2):index]
                if item and "标准" not in item and not re.search(r"\d{4}-\d{2}-\d{2}", item)
            ]
            issuer = " / ".join(previous)
            if issuer:
                return issuer
    for line in lines:
        if "委员会" in line or "管理总局" in line or "市场监督" in line:
            return line
    return ""


def _parse_standard_metadata(relative_path: str, metadata: dict | None, page_text: str) -> dict:
    lines = _compact_lines(page_text)
    title = _extract_standard_title(lines)
    issuer = _extract_standard_issuer(lines)
    stem = _source_stem(relative_path, metadata)
    standard_no_match = _STANDARD_CODE_PATTERN.search(" ".join(lines[:4])) or _STANDARD_CODE_PATTERN.search(stem)
    standard_no = _normalize_text(standard_no_match.group(0)) if standard_no_match else stem
    date_match = re.search(r"(\d{4}-\d{2}-\d{2}).*?(\d{4}-\d{2}-\d{2})?", " ".join(lines[:8]))
    if date_match:
        display_time = date_match.group(1)
    else:
        year_match = _YEAR_PATTERN.search(" ".join(lines[:12])) or _YEAR_PATTERN.search(stem)
        display_time = year_match.group(1) if year_match else "时间未标注"

    return {
        "display_title": title or standard_no,
        "display_author": issuer or "发布机构未标注",
        "display_time": display_time,
        "display_category": "标准",
        "issuer": issuer,
        "standard_no": standard_no,
    }


def _is_generic_paper_line(line: str) -> bool:
    generic_terms = (
        "abstract", "review", "foods", "citation", "published", "received", "copyright",
        "研究生学位论文", "天津科技大学", "专题报道", "栏目主持人", "zhuantibaodao",
        "schoolof", "university", "correspondence",
    )
    lowered = line.lower()
    return any(term in lowered for term in generic_terms)


def _extract_paper_title(lines: list[str]) -> str:
    for index, line in enumerate(lines[:20]):
        if _is_generic_paper_line(line):
            continue
        if len(line) < 6:
            continue
        if re.search(r"(研究生姓名|指导教师|专 业 名 称|professional|keywords)", line, re.IGNORECASE):
            continue
        if re.search(r"^[A-Za-z][A-Za-z .:;,'()/-]{10,}$", line) or re.search(r"[\u4e00-\u9fff]{6,}", line):
            if index + 1 < len(lines):
                next_line = lines[index + 1]
                if re.search(r"[\u4e00-\u9fff]{4,}", line) and re.search(r"[\u4e00-\u9fff]{2,}", next_line) and len(line) + len(next_line) <= 40:
                    return _normalize_text(f"{line}{next_line}")
            return line
    return ""


def _extract_paper_author(lines: list[str], page_text: str, relative_path: str, metadata: dict | None) -> str:
    direct_patterns = (
        r"研究生姓名[:：]\s*([^\s]+)",
        r"作者[:：]\s*([^\s]+)",
        r"□\s*[^ ]+\s+([^ ]+)$",
    )
    for pattern in direct_patterns:
        match = re.search(pattern, page_text, re.MULTILINE)
        if match:
            return _normalize_text(match.group(1))

    for line in lines[:20]:
        compact = re.sub(r"\d", "", line)
        if compact.count(",") >= 1 and len(compact) >= 8 and not _is_generic_paper_line(compact):
            compact = compact.replace("*", "")
            compact = re.sub(r"\s+", " ", compact).strip(" ,;")
            return compact

    stem = _source_stem(relative_path, metadata)
    filename_match = _PAPER_FILENAME_PATTERN.match(stem)
    if filename_match:
        return _normalize_text(filename_match.group("author").replace("_", " ").replace("等", "等"))
    if "_" in stem:
        tail = stem.rsplit("_", 1)[-1]
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z·]{2,12}", tail):
            return tail
    return "作者未标注"


def _extract_paper_time(lines: list[str], page_text: str, relative_path: str, metadata: dict | None) -> str:
    date_match = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*月)", page_text)
    if date_match:
        return _normalize_text(date_match.group(1))
    published_match = re.search(r"Published[:：]?\s*([0-9A-Za-z .-]+)", page_text)
    if published_match:
        return _normalize_text(published_match.group(1))
    generic_date = _DATE_PATTERN.search(page_text)
    if generic_date:
        return _normalize_text(generic_date.group(1))
    filename_match = _PAPER_FILENAME_PATTERN.match(_source_stem(relative_path, metadata))
    if filename_match:
        return filename_match.group("year")
    year_match = _YEAR_PATTERN.search(page_text)
    if year_match:
        return year_match.group(1)
    return "时间未标注"


def _extract_paper_journal(lines: list[str]) -> str:
    for line in lines[:8]:
        if len(line) <= 28 and re.fullmatch(r"[A-Za-z][A-Za-z .&-]{2,}", line) and not _is_generic_paper_line(line):
            return line
    for line in lines[:8]:
        if len(line) <= 20 and re.fullmatch(r"[A-Za-z][A-Za-z .&-]{2,}", line):
            return line
    return ""


_THESIS_PREFIX_RE = re.compile(
    r"^(硕士学位论文题目[：:]\s*|博士学位论文题目[：:]\s*|申请硕士学位论文[：:]\s*|申请博士学位论文[：:]\s*|学位论文[：:]\s*|题目[：:]\s*)",
    re.UNICODE,
)


def _clean_title_prefix(title: str) -> str:
    """去除学位论文等冗余前缀，返回清洗后的标题。"""
    if not title:
        return title
    return _THESIS_PREFIX_RE.sub("", title).strip()


def _parse_paper_like_metadata(relative_path: str, metadata: dict | None, page_text: str) -> dict:
    lines = _compact_lines(page_text)
    stem = _source_stem(relative_path, metadata)
    filename_match = _PAPER_FILENAME_PATTERN.match(stem)
    title = _extract_paper_title(lines)
    title = re.sub(r"^\(申请[^)]*\)", "", title).strip()
    title = _clean_title_prefix(title)
    if not title and filename_match:
        title = _normalize_text(filename_match.group("title").replace("_", " "))
    author = _extract_paper_author(lines, page_text, relative_path, metadata)
    display_time = _extract_paper_time(lines, page_text, relative_path, metadata)
    journal = _extract_paper_journal(lines)

    return {
        "display_title": title or stem,
        "display_author": author or "作者未标注",
        "display_time": display_time,
        "display_category": _display_category(metadata, relative_path),
        "journal": journal,
    }


def _parse_consumer_metadata(relative_path: str, metadata: dict | None, page_text: str) -> dict:
    suffix = Path(relative_path).suffix.lower() if relative_path else ""
    stem = _source_stem(relative_path, metadata)
    if suffix in {".csv", ".tsv", ".xlsx", ".xls"}:
        title_map = {
            "comment_details": "消费者评论明细",
            "product_ranking": "产品排名统计",
            "parameter_statistics": "消费者关注参数统计",
        }
        title = title_map.get(stem, stem.replace("_", " "))
        time_match = re.search(r"(20\d{2}-\d{2}-\d{2})", page_text)
        return {
            "display_title": title,
            "display_author": "未标注",
            "display_time": time_match.group(1) if time_match else "时间未标注",
            "display_category": "消费者评价",
        }

    parsed = _parse_paper_like_metadata(relative_path, metadata, page_text)
    parsed["display_category"] = "消费者评价"
    return parsed


def _fallback_metadata(relative_path: str | None, metadata: dict | None) -> dict:
    stem = _source_stem(relative_path, metadata)
    return {
        "display_title": stem or "未知资料",
        "display_author": "未标注",
        "display_time": "时间未标注",
        "display_category": _display_category(metadata, relative_path),
    }


@lru_cache(maxsize=512)
def _resolved_document_metadata(relative_path: str, doc_type: str, source_name: str) -> dict:
    metadata = {"doc_type": doc_type, "source": source_name}
    page_text = _page_one_text(relative_path)
    if doc_type == "专利":
        resolved = _parse_patent_metadata(relative_path, metadata, page_text)
    elif doc_type == "标准":
        resolved = _parse_standard_metadata(relative_path, metadata, page_text)
    elif doc_type == "消费者评价":
        resolved = _parse_consumer_metadata(relative_path, metadata, page_text)
    else:
        resolved = _parse_paper_like_metadata(relative_path, metadata, page_text)

    fallback = _fallback_metadata(relative_path, metadata)
    merged = {**fallback, **{key: value for key, value in resolved.items() if value}}
    merged["document_key"] = relative_path
    merged["relative_path"] = relative_path
    merged["source"] = source_name
    merged["doc_type"] = doc_type
    return merged


def resolve_source_metadata(relative_path: str | None, metadata: dict | None = None) -> dict:
    source_name = _source_name(relative_path, metadata)
    doc_type = str((metadata or {}).get("doc_type") or _display_category(metadata, relative_path))
    if relative_path:
        resolved = dict(_resolved_document_metadata(relative_path, doc_type, source_name))
    else:
        resolved = _fallback_metadata(relative_path, metadata)
        resolved["document_key"] = source_name
        resolved["source"] = source_name
        resolved["doc_type"] = doc_type

    manifest = _manifest_for(relative_path) if relative_path else {}
    if manifest.get("report", {}).get("source"):
        resolved.setdefault("source", manifest["report"]["source"])
    if metadata:
        for key, value in metadata.items():
            if value is not None and key not in {"page", "page_label", "citation_id"}:
                resolved.setdefault(key, value)
        resolved["doc_type"] = doc_type
    return resolved

"""文档入库脚本：先解析验收，再写入向量库。

能力：
- PDF / Excel / TXT / Markdown 解析
- 可选 OCR 兜底
- 解析中间产物落盘到 parsed_docs/
- 生成 parse_report.csv 供人工验收
- 仅将通过质量门禁的文档写入知识库
"""

import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import chromadb
import numpy as np
import pandas as pd
import pdfplumber
from dotenv import load_dotenv
from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex
from llama_index.vector_stores.chroma import ChromaVectorStore

from embeddings import SiliconFlowEmbedding

load_dotenv()
Settings.embed_model = SiliconFlowEmbedding(api_key=os.getenv("SILICONFLOW_API_KEY"))

DATA_DIR = Path(os.getenv("INGEST_DATA_DIR", "./data"))
CHROMA_DIR = Path(os.getenv("INGEST_CHROMA_DIR", "./chroma_db"))
PARSED_DIR = Path(os.getenv("INGEST_PARSED_DIR", "./parsed_docs"))
REPORT_PATH = Path(os.getenv("INGEST_REPORT_PATH", "./parse_report.csv"))

ENABLE_OCR = os.getenv("INGEST_ENABLE_OCR", "1").strip().lower() not in {"0", "false", "no"}
PARSE_EXISTING = os.getenv("INGEST_PARSE_EXISTING", "0").strip().lower() in {"1", "true", "yes"}
RESET_COLLECTION = os.getenv("INGEST_RESET_COLLECTION", "0").strip().lower() in {"1", "true", "yes"}
INCLUDE_DIRS = {
    item.strip()
    for item in os.getenv("INGEST_INCLUDE_DIRS", "").split(",")
    if item.strip()
}

MIN_DOC_CHARS = int(os.getenv("INGEST_MIN_DOC_CHARS", "120"))
MIN_VALID_CHAR_RATIO = float(os.getenv("INGEST_MIN_VALID_CHAR_RATIO", "0.72"))
MAX_GARBLE_SCORE = float(os.getenv("INGEST_MAX_GARBLE_SCORE", "0.18"))
PAGE_OCR_VALID_RATIO = float(os.getenv("INGEST_PAGE_OCR_VALID_RATIO", "0.84"))
PAGE_OCR_GARBLE_SCORE = float(os.getenv("INGEST_PAGE_OCR_GARBLE_SCORE", "0.12"))
PAGE_OCR_MIN_CHARS = int(os.getenv("INGEST_PAGE_OCR_MIN_CHARS", "80"))

FOLDER_TYPE = {
    "standards": "标准",
    "patents": "专利",
    "papers": "文献",
    "consumer": "消费者评价",
    "flavor": "风味文献",
    "sensor": "风味文献",
}

REPORT_FIELDS = [
    "source",
    "relative_path",
    "doc_type",
    "parser",
    "parse_status",
    "quality_status",
    "ingest_status",
    "pages_total",
    "pages_parsed",
    "ocr_pages",
    "table_pages",
    "empty_pages",
    "chars_total",
    "avg_chars_per_page",
    "valid_char_ratio",
    "garble_score",
    "table_count",
    "table_fact_count",
    "table_review_count",
    "artifact_dir",
    "notes",
]

PDF_TABLE_SETTINGS = [
    ("default", {}),
    ("lines", {"vertical_strategy": "lines", "horizontal_strategy": "lines"}),
    ("text", {"vertical_strategy": "text", "horizontal_strategy": "text"}),
    ("mixed_text_vertical", {"vertical_strategy": "text", "horizontal_strategy": "lines"}),
    ("mixed_text_horizontal", {"vertical_strategy": "lines", "horizontal_strategy": "text"}),
]

TABLE_KEYWORDS = (
    "样品",
    "项目",
    "指标",
    "参数",
    "含量",
    "总量",
    "提及次数",
    "占比",
    "编号",
    "类别",
    "名称",
    "得分",
    "评分",
    "喜好度",
    "口感",
    "香气",
    "风味",
    "产地",
    "品类",
    "总酸",
    "总酯",
    "还原糖",
    "有机酸",
    "物质",
)

TABLE_TITLE_MARKERS = ("表", "table", "tab.", "tab ", "fig.", "fig ", "图")

_ocr = None
_ocr_unavailable = False


def get_ocr():
    global _ocr, _ocr_unavailable
    if _ocr_unavailable:
        return None
    if _ocr is None:
        try:
            from paddleocr import PaddleOCR

            print("  [OCR] 初始化 PaddleOCR…")
            _ocr = PaddleOCR()
        except Exception as exc:
            _ocr_unavailable = True
            print(f"  [OCR] 初始化失败，已关闭 OCR 兜底: {exc}")
            return None
    return _ocr


def ocr_page(page) -> str:
    if not ENABLE_OCR:
        return ""
    try:
        img = page.to_image(resolution=200).original
        arr = np.array(img)
        ocr_engine = get_ocr()
        if ocr_engine is None:
            return ""
        result = ocr_engine.predict(arr)
        if not result or not result[0]:
            return ""
        texts = result[0].get("rec_texts", [])
        return "\n".join(t for t in texts if t)
    except Exception:
        return ""


def classify_page(page) -> str:
    text_len = len((page.extract_text() or "").strip())
    img_count = len(page.images)
    if text_len > 100:
        return "text"
    if text_len > 20:
        return "mixed"
    if img_count > 0:
        return "image"
    return "scanned"


def table_to_md(table: list) -> str:
    rows = [[str(c or "").strip() for c in row] for row in table]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return ""
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(part for part in (header, sep, body) if part)


def normalize_table_rows(table: list) -> list[list[str]]:
    rows = [[str(cell or "").strip() for cell in row] for row in table if row]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return []
    max_cols = max(len(row) for row in rows)
    return [row + [""] * (max_cols - len(row)) for row in rows]


def default_headers(col_count: int) -> list[str]:
    return [f"col_{idx + 1}" for idx in range(col_count)]


def compact_cell_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def count_table_keywords(text: str) -> int:
    compact = compact_cell_text(text)
    return sum(1 for keyword in TABLE_KEYWORDS if keyword in compact)


def is_numeric_like_cell(text: str) -> bool:
    compact = compact_cell_text(text)
    if not compact:
        return False
    if re.search(r"\d", compact) and re.fullmatch(r"[\dA-Za-z.%％\-+/±~～()（）μmgL·:,]+", compact):
        return True
    digit_count = sum(1 for ch in compact if ch.isdigit())
    return digit_count > 0 and digit_count / max(len(compact), 1) >= 0.3


def is_sentence_like_cell(text: str) -> bool:
    compact = compact_cell_text(text)
    if len(compact) < 14:
        return False
    if re.search(r"[。！？；]", compact):
        return True
    if (compact.count("，") + compact.count(",")) >= 2 and not is_numeric_like_cell(compact):
        return True
    if re.search(r"\[[0-9]{1,3}\]", compact):
        return True
    return False


def is_long_cell(text: str) -> bool:
    return len(compact_cell_text(text)) >= 24


def contains_table_title_marker(text: str) -> bool:
    compact = compact_cell_text(text).lower()
    if not compact:
        return False
    return any(marker in compact for marker in TABLE_TITLE_MARKERS)


def header_short_cell_ratio(headers: list[str]) -> float:
    nonempty = [compact_cell_text(item) for item in headers if compact_cell_text(item)]
    if not nonempty:
        return 0.0
    short_count = sum(1 for item in nonempty if len(item) <= 4)
    return round(short_count / len(nonempty), 4)


def title_marker_cell_count(rows: list[list[str]], sample_rows: int = 4) -> int:
    count = 0
    for row in rows[:sample_rows]:
        for cell in row:
            if contains_table_title_marker(cell or ""):
                count += 1
    return count


def is_low_quality_header(text: str) -> bool:
    compact = compact_cell_text(text)
    if not compact:
        return True
    if compact.startswith("col_") or compact.startswith("Unnamed:"):
        return True
    if re.fullmatch(r"[\d.%％\-+/]+", compact):
        return True
    if len(compact) <= 2 and not re.search(r"[\u4e00-\u9fffA-Za-z]", compact):
        return True
    return False


def header_candidate_score(row: list[str]) -> int:
    nonempty = [cell for cell in row if cell]
    sentence_like_count = 0
    long_cell_count = 0
    keyword_hits = 0
    score = 0
    for cell in row:
        compact = compact_cell_text(cell)
        if not compact:
            continue
        if compact.startswith("Unnamed:"):
            score -= 2
            continue
        if re.fullmatch(r"[\d.%％\-+/]+", compact):
            score -= 2
            continue
        if re.search(r"[\u4e00-\u9fffA-Za-z]", compact):
            score += 3
        else:
            score += 1
        if any(keyword in compact for keyword in ("提及次数", "占比", "指标", "参数", "名称", "样品", "项目")):
            score += 2
        keyword_hits += count_table_keywords(compact)
        if is_sentence_like_cell(compact):
            sentence_like_count += 1
        if is_long_cell(compact):
            long_cell_count += 1

    score += len(nonempty) * 2
    score += min(keyword_hits, 6) * 2
    score -= sentence_like_count * 4
    score -= long_cell_count * 2
    return score


def pick_header_row(rows: list[list[str]]) -> tuple[int, list[str]]:
    if not rows:
        return 0, []

    best_idx = 0
    best_score = -10**9
    for idx, row in enumerate(rows[:6]):
        nonempty = [cell for cell in row if cell]
        if len(nonempty) < max(2, len(row) // 3):
            continue
        score = header_candidate_score(row)
        if idx > 0:
            score += 1
        if score > best_score:
            best_idx = idx
            best_score = score

    header_row = rows[best_idx]
    headers = []
    seen = set()
    for col_idx, cell in enumerate(header_row):
        header = cell or f"col_{col_idx + 1}"
        if header in seen:
            header = f"{header}_{col_idx + 1}"
        seen.add(header)
        headers.append(header)
    if headers:
        return best_idx, headers

    return 0, default_headers(len(rows[0]))


def evaluate_table_rows(rows: list[list[str]]) -> dict:
    if not rows:
        return {
            "status": "blocked",
            "note": "空表格",
            "row_count": 0,
            "col_count": 0,
            "nonempty_ratio": 0.0,
            "header_row_index": 0,
            "headers": [],
            "avg_data_density": 0.0,
        }

    header_row_index, headers = pick_header_row(rows)
    trimmed_rows = rows[header_row_index:] if header_row_index < len(rows) else rows
    row_count = len(trimmed_rows)
    col_count = max(len(row) for row in trimmed_rows)
    nonempty_cells = sum(1 for row in trimmed_rows for cell in row if cell)
    nonempty_ratio = round(nonempty_cells / max(row_count * col_count, 1), 4)
    header_nonempty = sum(1 for item in headers if item and not item.startswith("col_"))
    low_quality_header_count = sum(1 for item in headers if is_low_quality_header(item))
    header_keyword_count = sum(count_table_keywords(item) for item in headers if item)
    header_sentence_like_count = sum(1 for item in headers if is_sentence_like_cell(item))
    header_short_ratio = header_short_cell_ratio(headers)
    header_lengths = [len(compact_cell_text(item)) for item in headers if item]
    header_max_length = max(header_lengths) if header_lengths else 0
    header_avg_length = round(sum(header_lengths) / max(len(header_lengths), 1), 2)
    data_rows = trimmed_rows[1:]
    title_marker_count = title_marker_cell_count(trimmed_rows)
    avg_data_density = round(
        sum(sum(1 for cell in row if cell) for row in data_rows) / max(len(data_rows), 1),
        2,
    ) if data_rows else 0.0
    nonempty_data_cells = [cell for row in data_rows for cell in row if cell]
    sentence_like_ratio = round(
        sum(1 for cell in nonempty_data_cells if is_sentence_like_cell(cell)) / max(len(nonempty_data_cells), 1),
        4,
    )
    long_cell_ratio = round(
        sum(1 for cell in nonempty_data_cells if is_long_cell(cell)) / max(len(nonempty_data_cells), 1),
        4,
    )
    numeric_like_ratio = round(
        sum(1 for cell in nonempty_data_cells if is_numeric_like_cell(cell)) / max(len(nonempty_data_cells), 1),
        4,
    )
    multiline_cell_ratio = round(
        sum(1 for cell in nonempty_data_cells if "\n" in (cell or "")) / max(len(nonempty_data_cells), 1),
        4,
    )

    if row_count < 2 or col_count < 2:
        return {
            "status": "blocked",
            "note": "行列过少，无法形成可用表格",
            "row_count": row_count,
            "col_count": col_count,
            "nonempty_ratio": nonempty_ratio,
            "header_row_index": header_row_index,
            "headers": headers,
            "avg_data_density": avg_data_density,
            "trimmed_rows": trimmed_rows,
            "header_keyword_count": header_keyword_count,
            "header_sentence_like_count": header_sentence_like_count,
            "header_short_ratio": header_short_ratio,
            "header_max_length": header_max_length,
            "header_avg_length": header_avg_length,
            "title_marker_count": title_marker_count,
            "sentence_like_ratio": sentence_like_ratio,
            "long_cell_ratio": long_cell_ratio,
            "numeric_like_ratio": numeric_like_ratio,
            "multiline_cell_ratio": multiline_cell_ratio,
        }

    if (
        col_count <= 3
        and row_count >= 8
        and sentence_like_ratio >= 0.38
        and numeric_like_ratio <= 0.12
    ):
        status = "blocked"
        note = "疑似双栏正文或段落被误识别为表格"
    elif (
        col_count <= 3
        and row_count >= 8
        and long_cell_ratio >= 0.45
        and numeric_like_ratio <= 0.15
    ):
        status = "blocked"
        note = "单元格过长且缺少结构化数值，疑似正文误判"
    elif (
        header_sentence_like_count >= max(1, len(headers) // 2)
        and header_keyword_count == 0
        and col_count <= 4
    ):
        status = "review"
        note = "表头更像正文句子，建议人工抽查"
    elif header_keyword_count == 0 and row_count >= 8 and header_short_ratio >= 0.55:
        status = "review"
        note = "表头呈碎片化短语，疑似正文或表题被拆分"
    elif title_marker_count >= 1 and header_keyword_count <= 1 and header_short_ratio >= 0.35:
        status = "review"
        note = "前几行混入表题或图题，建议人工抽查"
    elif title_marker_count >= 1 and low_quality_header_count >= 1 and row_count <= 4:
        status = "review"
        note = "表题与表头混合，暂不自动转为事实"
    elif header_max_length >= 160:
        status = "review"
        note = "表头过长，疑似混入正文段落"
    elif header_avg_length >= 36 and header_sentence_like_count >= 1:
        status = "review"
        note = "表头平均长度过长，建议人工抽查"
    elif nonempty_ratio < 0.12:
        status = "review"
        note = "表格非空单元格占比偏低"
    elif low_quality_header_count >= max(1, len(headers) // 2):
        status = "review"
        note = "表头疑似错位或被数值污染"
    elif avg_data_density < 1.5:
        status = "review"
        note = "表格数据密度偏低，建议人工抽查"
    elif header_nonempty < max(1, col_count // 3):
        status = "review"
        note = "表头信息不足，建议人工抽查"
    else:
        status = "pass"
        note = ""

    return {
        "status": status,
        "note": note,
        "row_count": row_count,
        "col_count": col_count,
        "nonempty_ratio": nonempty_ratio,
        "header_row_index": header_row_index,
        "headers": headers,
        "avg_data_density": avg_data_density,
        "trimmed_rows": trimmed_rows,
        "header_keyword_count": header_keyword_count,
        "header_sentence_like_count": header_sentence_like_count,
        "header_short_ratio": header_short_ratio,
        "header_max_length": header_max_length,
        "header_avg_length": header_avg_length,
        "title_marker_count": title_marker_count,
        "sentence_like_ratio": sentence_like_ratio,
        "long_cell_ratio": long_cell_ratio,
        "numeric_like_ratio": numeric_like_ratio,
        "multiline_cell_ratio": multiline_cell_ratio,
    }


def row_to_fact_text(headers: list[str], row: list[str], page_no: int, table_id: str, row_index: int) -> str:
    cells = []
    for header, value in zip(headers, row):
        if value:
            cells.append(f"{header}: {value}")
    if not cells:
        return ""
    return f"第{page_no}页 {table_id} 第{row_index}行 | " + " | ".join(cells)


def build_table_payload(rows: list[list[str]], page_no: int, table_index: int, parser: str) -> dict:
    normalized_rows = normalize_table_rows(rows)
    assessment = evaluate_table_rows(normalized_rows)
    table_id = f"T{page_no:03d}_{table_index:02d}"
    headers = assessment["headers"] or default_headers(assessment["col_count"])
    trimmed_rows = assessment.get("trimmed_rows") or normalized_rows
    data_rows = trimmed_rows[1:] if trimmed_rows else []

    facts = []
    fact_eligible = assessment["status"] == "pass" and not (
        assessment.get("multiline_cell_ratio", 0.0) >= 0.3 and assessment["row_count"] <= 4
    )
    if (
        fact_eligible
        and assessment.get("header_keyword_count", 0) == 0
        and assessment.get("header_short_ratio", 0.0) >= 0.45
        and assessment["row_count"] >= 8
    ):
        fact_eligible = False
    if (
        fact_eligible
        and assessment.get("title_marker_count", 0) >= 1
        and assessment.get("header_keyword_count", 0) <= 1
        and assessment.get("header_short_ratio", 0.0) >= 0.3
    ):
        fact_eligible = False
    if fact_eligible:
        for idx, row in enumerate(data_rows, start=1):
            fact_text = row_to_fact_text(headers, row, page_no, table_id, idx)
            if fact_text:
                facts.append(
                    {
                        "table_id": table_id,
                        "page": page_no,
                        "row_index": idx,
                        "text": fact_text,
                    }
                )

    summary_fields = [header for header in headers if header][: min(6, len(headers))]
    summary_text = (
        f"第{page_no}页 {table_id} 表格摘要 | "
        f"共{assessment['row_count']}行{assessment['col_count']}列 | "
        f"主要字段：{'、'.join(summary_fields) if summary_fields else '未识别'}"
    )

    return {
        "table_id": table_id,
        "page": page_no,
        "parser": parser,
        "status": assessment["status"],
        "note": assessment["note"],
        "row_count": assessment["row_count"],
        "col_count": assessment["col_count"],
        "nonempty_ratio": assessment["nonempty_ratio"],
        "header_row_index": assessment["header_row_index"],
        "avg_data_density": assessment["avg_data_density"],
        "header_keyword_count": assessment.get("header_keyword_count", 0),
        "header_sentence_like_count": assessment.get("header_sentence_like_count", 0),
        "header_short_ratio": assessment.get("header_short_ratio", 0.0),
        "header_max_length": assessment.get("header_max_length", 0),
        "header_avg_length": assessment.get("header_avg_length", 0.0),
        "title_marker_count": assessment.get("title_marker_count", 0),
        "sentence_like_ratio": assessment.get("sentence_like_ratio", 0.0),
        "long_cell_ratio": assessment.get("long_cell_ratio", 0.0),
        "numeric_like_ratio": assessment.get("numeric_like_ratio", 0.0),
        "multiline_cell_ratio": assessment.get("multiline_cell_ratio", 0.0),
        "fact_eligible": fact_eligible,
        "headers": headers,
        "rows": trimmed_rows,
        "markdown": table_to_md(trimmed_rows),
        "summary_text": summary_text,
        "facts": facts,
    }


def table_signature(payload: dict) -> str:
    rows = payload.get("rows", [])
    head_rows = rows[: min(5, len(rows))]
    flattened = []
    for row in head_rows:
        flattened.append(" | ".join(row[: min(8, len(row))]))
    return "\n".join(flattened)


def score_table_payload(payload: dict, page_text: str) -> int:
    score = 0
    row_count = payload["row_count"]
    col_count = payload["col_count"]
    nonempty_ratio = payload["nonempty_ratio"]
    status = payload["status"]
    avg_data_density = payload.get("avg_data_density", 0.0)
    sentence_like_ratio = payload.get("sentence_like_ratio", 0.0)
    long_cell_ratio = payload.get("long_cell_ratio", 0.0)
    numeric_like_ratio = payload.get("numeric_like_ratio", 0.0)
    header_keyword_count = payload.get("header_keyword_count", 0)
    header_sentence_like_count = payload.get("header_sentence_like_count", 0)
    header_short_ratio = payload.get("header_short_ratio", 0.0)
    header_max_length = payload.get("header_max_length", 0)
    header_avg_length = payload.get("header_avg_length", 0.0)
    title_marker_count = payload.get("title_marker_count", 0)
    multiline_cell_ratio = payload.get("multiline_cell_ratio", 0.0)

    if status == "pass":
        score += 6
    elif status == "review":
        score += 1
    else:
        score -= 8

    if 3 <= row_count <= 35:
        score += 4
    elif row_count <= 60:
        score += 1
    else:
        score -= 4

    if 3 <= col_count <= 15:
        score += 4
    elif col_count == 2:
        score -= 2
    elif col_count > 20:
        score -= 2

    if 0.22 <= nonempty_ratio <= 0.95:
        score += 2
    elif nonempty_ratio < 0.12:
        score -= 3

    if avg_data_density >= 2:
        score += 2
    elif avg_data_density < 1:
        score -= 2

    if 0.12 <= numeric_like_ratio <= 0.9:
        score += 2
    elif numeric_like_ratio < 0.06 and row_count >= 6:
        score -= 3

    if sentence_like_ratio >= 0.35:
        score -= 7
    elif sentence_like_ratio >= 0.2:
        score -= 3

    if long_cell_ratio >= 0.45:
        score -= 6
    elif long_cell_ratio >= 0.25:
        score -= 2

    if multiline_cell_ratio >= 0.4 and row_count <= 4:
        score -= 3
    if header_keyword_count == 0 and row_count >= 8 and header_short_ratio >= 0.55:
        score -= 8
    elif header_keyword_count == 0 and header_short_ratio >= 0.4:
        score -= 3
    if title_marker_count >= 1 and header_keyword_count <= 1:
        score -= 5

    header_text = " ".join(payload.get("headers", []))
    if "表" in page_text or "table" in page_text.lower():
        score += 1
    if re.search(r"(样品|项目|指标|参数|含量|总量|提及次数|占比)", header_text):
        score += 2
    score += min(header_keyword_count, 4)
    if header_sentence_like_count >= max(1, len(payload.get("headers", [])) // 2):
        score -= 5
    if header_max_length >= 160:
        score -= 8
    elif header_avg_length >= 36:
        score -= 4
    if len(header_text) > 180:
        score -= 2
    if row_count > 50 and col_count <= 3:
        score -= 4
    if col_count <= 3 and row_count >= 8 and sentence_like_ratio >= 0.28 and numeric_like_ratio <= 0.12:
        score -= 8

    return score


def extract_page_tables(page, page_no: int) -> list[dict]:
    page_text = page.extract_text() or ""
    lowered_page_text = page_text.lower()
    has_table_marker = bool(
        re.search(r"(表\s*\d+|table\s*\d+|表\d+)", page_text, flags=re.IGNORECASE)
    )
    candidates = []

    for strategy_name, settings in PDF_TABLE_SETTINGS:
        if strategy_name in {"text", "mixed_text_vertical", "mixed_text_horizontal"} and not has_table_marker:
            continue
        try:
            raw_tables = page.extract_tables(settings) if settings else page.extract_tables()
        except Exception:
            continue
        for table_index, table in enumerate(raw_tables, start=1):
            payload = build_table_payload(table, page_no, table_index, "pdfplumber_table")
            if payload["status"] == "blocked":
                continue
            payload["extraction_strategy"] = strategy_name
            payload["candidate_score"] = score_table_payload(payload, lowered_page_text)
            candidates.append(payload)

    if not candidates:
        return []

    selected = []
    seen = set()
    for payload in sorted(candidates, key=lambda item: item.get("candidate_score", 0), reverse=True):
        if payload.get("candidate_score", 0) < 4:
            continue
        signature = table_signature(payload)
        if not signature or signature in seen:
            continue
        seen.add(signature)
        selected.append(payload)

    non_text_selected = [item for item in selected if item.get("extraction_strategy") != "text"]
    if non_text_selected:
        text_selected = []
        for item in selected:
            if item.get("extraction_strategy") != "text":
                continue
            if (
                item.get("header_row_index", 0) <= 1
                and item.get("header_keyword_count", 0) >= 4
                and item.get("sentence_like_ratio", 0.0) <= 0.08
                and item.get("long_cell_ratio", 0.0) <= 0.15
            ):
                text_selected.append(item)
        selected = non_text_selected + text_selected

    return selected


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def artifact_dir_for(file_path: Path) -> Path:
    digest = hashlib.sha1(str(file_path).encode("utf-8")).hexdigest()[:8]
    safe_stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", file_path.stem).strip("._")
    safe_stem = safe_stem[:80] or "document"
    return PARSED_DIR / f"{safe_stem}__{digest}"


def write_text(path: Path, content: str) -> None:
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_text_metrics(text: str) -> tuple[int, float, float]:
    compact = "".join((text or "").split())
    chars_total = len(compact)
    if chars_total == 0:
        return 0, 0.0, 1.0

    allowed = re.findall(
        r"[\u4e00-\u9fffA-Za-z0-9\uFF01-\uFF5E，。！？；：、“”‘’（）()【】《》—\-_/%,.:;+\s]",
        text,
    )
    valid_chars = len(re.sub(r"\s+", "", "".join(allowed)))
    valid_ratio = min(1.0, valid_chars / chars_total)

    garble_matches = re.findall(
        r"[�□◻◼◆◇■�]|[^\u4e00-\u9fffA-Za-z0-9\uFF01-\uFF5E，。！？；：、“”‘’（）()【】《》—\-_/%,.:;\n\t ]",
        text,
    )
    garble_score = min(1.0, len(garble_matches) / max(chars_total, 1))
    return chars_total, round(valid_ratio, 4), round(garble_score, 4)


def summarize_quality(report: dict) -> tuple[str, str]:
    if report["pages_parsed"] == 0 or report["chars_total"] < MIN_DOC_CHARS:
        return "blocked", "提取文本过少，暂不入库"

    if report["valid_char_ratio"] < MIN_VALID_CHAR_RATIO:
        return "review", "有效字符占比偏低，建议人工抽查"

    if report["garble_score"] > MAX_GARBLE_SCORE:
        return "review", "疑似乱码字符占比偏高，建议人工抽查"

    return "pass", ""


def should_try_ocr_for_text_page(text: str) -> bool:
    chars_total, valid_ratio, garble_score = collect_text_metrics(text)
    if chars_total == 0:
        return True
    if chars_total < PAGE_OCR_MIN_CHARS:
        return False
    return valid_ratio < PAGE_OCR_VALID_RATIO or garble_score > PAGE_OCR_GARBLE_SCORE


def should_prefer_ocr_text(native_text: str, ocr_text: str) -> bool:
    if not ocr_text.strip():
        return False

    native_chars, native_valid_ratio, native_garble = collect_text_metrics(native_text)
    ocr_chars, ocr_valid_ratio, ocr_garble = collect_text_metrics(ocr_text)

    if native_chars == 0:
        return True
    if ocr_chars < max(40, int(native_chars * 0.4)):
        return False

    native_quality_score = native_valid_ratio - native_garble
    ocr_quality_score = ocr_valid_ratio - ocr_garble
    return (
        ocr_quality_score >= native_quality_score + 0.08
        or (
            ocr_valid_ratio >= native_valid_ratio + 0.08
            and ocr_garble <= max(0.0, native_garble - 0.05)
        )
    )


def build_documents(chunks: list[dict], file_path: Path, doc_type: str) -> list[Document]:
    relative_path = str(file_path.relative_to(DATA_DIR))
    docs = []
    for chunk in chunks:
        text = chunk["text"].strip()
        if not text:
            continue
        docs.append(
            Document(
                text=text,
                metadata={
                    "source": file_path.name,
                    "relative_path": relative_path,
                    "doc_type": doc_type,
                    "page": chunk.get("page"),
                    "page_type": chunk.get("page_type"),
                    "modality": chunk.get("modality", "text"),
                    "parser": chunk.get("parser", "native"),
                    "chunk_kind": chunk.get("chunk_kind", "paragraph"),
                    "table_id": chunk.get("table_id"),
                    "row_index": chunk.get("row_index"),
                    "table_status": chunk.get("table_status"),
                },
            )
        )
    return docs


def render_combined_markdown(file_path: Path, chunks: list[dict]) -> str:
    parts = [f"# {file_path.name}", ""]
    for chunk in chunks:
        title = f"## Page {chunk.get('page', '?')} [{chunk.get('page_type', 'unknown')}]"
        parts.extend([title, "", chunk["text"].strip(), ""])
    return "\n".join(parts).strip() + "\n"


def persist_artifacts(file_path: Path, report: dict, chunks: list[dict], table_payloads: list[dict] | None = None) -> None:
    artifact_dir = artifact_dir_for(file_path)
    pages_dir = artifact_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    for chunk in chunks:
        page_no = chunk.get("page", 0)
        page_path = pages_dir / f"page_{int(page_no):03d}.md"
        write_text(page_path, chunk["text"].strip() + "\n")

    write_text(artifact_dir / "combined.md", render_combined_markdown(file_path, chunks))
    write_json(artifact_dir / "tables.json", {"tables": table_payloads or []})
    facts_path = artifact_dir / "table_facts.jsonl"
    ensure_parent(facts_path)
    with facts_path.open("w", encoding="utf-8") as handle:
        for table in table_payloads or []:
            for fact in table.get("facts", []):
                handle.write(json.dumps(fact, ensure_ascii=False) + "\n")
    write_json(
        artifact_dir / "manifest.json",
        {
            "source": file_path.name,
            "relative_path": str(file_path.relative_to(DATA_DIR)),
            "report": report,
            "page_count": len(chunks),
            "table_count": len(table_payloads or []),
            "table_fact_count": sum(len(table.get("facts", [])) for table in (table_payloads or [])),
        },
    )


def parse_pdf(file_path: Path, doc_type: str) -> tuple[list[Document], dict]:
    chunks = []
    table_payloads = []
    pages_total = 0
    ocr_pages = 0
    table_pages = 0
    empty_pages = 0
    parser = "pdfplumber"

    with pdfplumber.open(file_path) as pdf:
        pages_total = len(pdf.pages)
        for idx, page in enumerate(pdf.pages, start=1):
            page_type = classify_page(page)
            parts = []
            text = ""
            chunk_modality = "text"
            chunk_parser = "pdfplumber"
            page_tables = []

            if page_type in ("text", "mixed"):
                text = page.extract_text() or ""
                if should_try_ocr_for_text_page(text):
                    ocr_text = ocr_page(page)
                    if should_prefer_ocr_text(text, ocr_text):
                        text = ocr_text
                        ocr_pages += 1
                        parser = "paddleocr"
                        chunk_modality = "ocr"
                        chunk_parser = "paddleocr"
                if text.strip():
                    parts.append(text.strip())
                page_tables = extract_page_tables(page, idx)
                table_payloads.extend(page_tables)
                if page_tables:
                    table_pages += 1
                    parts.append("\n\n".join(f"【表格】\n{item['markdown']}" for item in page_tables if item["markdown"]))
            else:
                text = ocr_page(page)
                if text.strip():
                    ocr_pages += 1
                    parser = "paddleocr"
                    chunk_modality = "ocr"
                    chunk_parser = "paddleocr"
                    parts.append(text.strip())

            combined = "\n\n".join(part for part in parts if part.strip()).strip()
            if not combined:
                empty_pages += 1
                continue

            chunks.append(
                {
                    "page": idx,
                    "page_type": page_type,
                    "modality": chunk_modality if page_type in ("text", "mixed") else "ocr",
                    "parser": chunk_parser if page_type in ("text", "mixed") else "paddleocr",
                    "text": combined,
                }
            )

            for table in page_tables if page_type in ("text", "mixed") else []:
                chunks.append(
                    {
                        "page": idx,
                        "page_type": "table",
                        "modality": "table",
                        "parser": table["parser"],
                        "chunk_kind": "table_summary",
                        "table_id": table["table_id"],
                        "table_status": table["status"],
                        "text": table["summary_text"],
                    }
                )
                for fact in table["facts"]:
                    chunks.append(
                        {
                            "page": idx,
                            "page_type": "table",
                            "modality": "table",
                            "parser": table["parser"],
                            "chunk_kind": "table_fact",
                            "table_id": table["table_id"],
                            "row_index": fact["row_index"],
                            "table_status": table["status"],
                            "text": fact["text"],
                        }
                    )

    combined_text = "\n".join(chunk["text"] for chunk in chunks)
    chars_total, valid_ratio, garble_score = collect_text_metrics(combined_text)
    pages_parsed = len(chunks)
    avg_chars = round(chars_total / max(pages_parsed, 1), 2)
    parse_status = "empty" if not chunks else "ocr" if ocr_pages else "ok"

    report = {
        "source": file_path.name,
        "relative_path": str(file_path.relative_to(DATA_DIR)),
        "doc_type": doc_type,
        "parser": parser,
        "parse_status": parse_status,
        "pages_total": pages_total,
        "pages_parsed": pages_parsed,
        "ocr_pages": ocr_pages,
        "table_pages": table_pages,
        "empty_pages": empty_pages,
        "chars_total": chars_total,
        "avg_chars_per_page": avg_chars,
        "valid_char_ratio": valid_ratio,
        "garble_score": garble_score,
        "table_count": len(table_payloads),
        "table_fact_count": sum(len(table["facts"]) for table in table_payloads),
        "table_review_count": sum(1 for table in table_payloads if table["status"] != "pass"),
        "artifact_dir": str(artifact_dir_for(file_path)),
        "notes": "",
    }
    report["quality_status"], note = summarize_quality(report)
    if note:
        report["notes"] = note

    persist_artifacts(file_path, report, chunks, table_payloads=table_payloads)
    return build_documents(chunks, file_path, doc_type), report


def parse_excel(file_path: Path, doc_type: str) -> tuple[list[Document], dict]:
    df = pd.read_excel(file_path)
    chunks = []
    table_payloads = []
    if not df.empty:
        rows = [list(df.columns)] + df.fillna("").astype(str).values.tolist()
        table = build_table_payload(rows, 1, 1, "pandas")
        table_payloads.append(table)
        chunks.append(
            {
                "page": 1,
                "page_type": "table",
                "modality": "table",
                "parser": "pandas",
                "chunk_kind": "table_summary",
                "table_id": table["table_id"],
                "table_status": table["status"],
                "text": table["summary_text"],
            }
        )
        for fact in table["facts"]:
            chunks.append(
                {
                    "page": 1,
                    "page_type": "table",
                    "modality": "table",
                    "parser": "pandas",
                    "chunk_kind": "table_fact",
                    "table_id": table["table_id"],
                    "row_index": fact["row_index"],
                    "table_status": table["status"],
                    "text": fact["text"],
                }
            )

    preview = df.head(20).fillna("").to_csv(index=False) if not df.empty else ""
    chars_total, valid_ratio, garble_score = collect_text_metrics(preview)
    report = {
        "source": file_path.name,
        "relative_path": str(file_path.relative_to(DATA_DIR)),
        "doc_type": doc_type,
        "parser": "pandas",
        "parse_status": "empty" if not chunks else "ok",
        "pages_total": len(df.index),
        "pages_parsed": len(chunks),
        "ocr_pages": 0,
        "table_pages": 1 if chunks else 0,
        "empty_pages": 0 if chunks else max(len(df.index), 1),
        "chars_total": chars_total,
        "avg_chars_per_page": round(chars_total / max(len(chunks), 1), 2),
        "valid_char_ratio": valid_ratio,
        "garble_score": garble_score,
        "table_count": len(table_payloads),
        "table_fact_count": sum(len(table["facts"]) for table in table_payloads),
        "table_review_count": sum(1 for table in table_payloads if table["status"] != "pass"),
        "artifact_dir": str(artifact_dir_for(file_path)),
        "notes": "",
    }
    report["quality_status"], note = summarize_quality(report)
    if note:
        report["notes"] = note

    artifact_dir = artifact_dir_for(file_path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_text(artifact_dir / "combined.md", preview or "")
    write_json(artifact_dir / "tables.json", {"tables": table_payloads})
    facts_path = artifact_dir / "table_facts.jsonl"
    with facts_path.open("w", encoding="utf-8") as handle:
        for table in table_payloads:
            for fact in table["facts"]:
                handle.write(json.dumps(fact, ensure_ascii=False) + "\n")
    write_json(
        artifact_dir / "manifest.json",
        {
            "source": file_path.name,
            "report": report,
            "table_count": len(table_payloads),
            "table_fact_count": sum(len(table["facts"]) for table in table_payloads),
        },
    )
    return build_documents(chunks, file_path, doc_type), report


def read_delimited_file(file_path: Path) -> pd.DataFrame:
    read_kwargs = {"sep": "\t"} if file_path.suffix.lower() == ".tsv" else {"sep": None, "engine": "python"}
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return pd.read_csv(file_path, encoding=encoding, **read_kwargs)
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError(f"无法读取文件: {file_path}")


def parse_delimited_file(file_path: Path, doc_type: str) -> tuple[list[Document], dict]:
    df = read_delimited_file(file_path)
    chunks = []
    table_payloads = []
    if not df.empty:
        rows = [list(df.columns)] + df.fillna("").astype(str).values.tolist()
        table = build_table_payload(rows, 1, 1, "pandas_csv")
        table_payloads.append(table)
        chunks.append(
            {
                "page": 1,
                "page_type": "table",
                "modality": "table",
                "parser": "pandas_csv",
                "chunk_kind": "table_summary",
                "table_id": table["table_id"],
                "table_status": table["status"],
                "text": table["summary_text"],
            }
        )
        for fact in table["facts"]:
            chunks.append(
                {
                    "page": 1,
                    "page_type": "table",
                    "modality": "table",
                    "parser": "pandas_csv",
                    "chunk_kind": "table_fact",
                    "table_id": table["table_id"],
                    "row_index": fact["row_index"],
                    "table_status": table["status"],
                    "text": fact["text"],
                }
            )

    preview = df.head(20).fillna("").to_csv(index=False) if not df.empty else ""
    chars_total, valid_ratio, garble_score = collect_text_metrics(preview)
    report = {
        "source": file_path.name,
        "relative_path": str(file_path.relative_to(DATA_DIR)),
        "doc_type": doc_type,
        "parser": "pandas_csv",
        "parse_status": "empty" if not chunks else "ok",
        "pages_total": len(df.index),
        "pages_parsed": len(chunks),
        "ocr_pages": 0,
        "table_pages": 1 if chunks else 0,
        "empty_pages": 0 if chunks else max(len(df.index), 1),
        "chars_total": chars_total,
        "avg_chars_per_page": round(chars_total / max(len(chunks), 1), 2),
        "valid_char_ratio": valid_ratio,
        "garble_score": garble_score,
        "table_count": len(table_payloads),
        "table_fact_count": sum(len(table["facts"]) for table in table_payloads),
        "table_review_count": sum(1 for table in table_payloads if table["status"] != "pass"),
        "artifact_dir": str(artifact_dir_for(file_path)),
        "notes": "",
    }
    report["quality_status"], note = summarize_quality(report)
    if note:
        report["notes"] = note

    artifact_dir = artifact_dir_for(file_path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_text(artifact_dir / "combined.md", preview or "")
    write_json(artifact_dir / "tables.json", {"tables": table_payloads})
    facts_path = artifact_dir / "table_facts.jsonl"
    with facts_path.open("w", encoding="utf-8") as handle:
        for table in table_payloads:
            for fact in table["facts"]:
                handle.write(json.dumps(fact, ensure_ascii=False) + "\n")
    write_json(
        artifact_dir / "manifest.json",
        {
            "source": file_path.name,
            "report": report,
            "table_count": len(table_payloads),
            "table_fact_count": sum(len(table["facts"]) for table in table_payloads),
        },
    )
    return build_documents(chunks, file_path, doc_type), report


def parse_text_file(file_path: Path, doc_type: str) -> tuple[list[Document], dict]:
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    chunks = []
    if text.strip():
        chunks.append(
            {
                "page": 1,
                "page_type": "text",
                "modality": "text",
                "parser": "native",
                "text": text.strip(),
            }
        )

    chars_total, valid_ratio, garble_score = collect_text_metrics(text)
    report = {
        "source": file_path.name,
        "relative_path": str(file_path.relative_to(DATA_DIR)),
        "doc_type": doc_type,
        "parser": "native",
        "parse_status": "empty" if not chunks else "ok",
        "pages_total": 1,
        "pages_parsed": len(chunks),
        "ocr_pages": 0,
        "table_pages": 0,
        "empty_pages": 0 if chunks else 1,
        "chars_total": chars_total,
        "avg_chars_per_page": float(chars_total if chunks else 0),
        "valid_char_ratio": valid_ratio,
        "garble_score": garble_score,
        "table_count": 0,
        "table_fact_count": 0,
        "table_review_count": 0,
        "artifact_dir": str(artifact_dir_for(file_path)),
        "notes": "",
    }
    report["quality_status"], note = summarize_quality(report)
    if note:
        report["notes"] = note

    persist_artifacts(file_path, report, chunks, table_payloads=[])
    return build_documents(chunks, file_path, doc_type), report


def parse_file(file_path: Path, doc_type: str) -> tuple[list[Document], dict]:
    suffix = file_path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return parse_excel(file_path, doc_type)
    if suffix in {".csv", ".tsv"}:
        return parse_delimited_file(file_path, doc_type)
    if suffix == ".pdf":
        return parse_pdf(file_path, doc_type)
    return parse_text_file(file_path, doc_type)


def get_existing_sources(collection) -> set:
    try:
        result = collection.get(include=["metadatas"])
        existing = set()
        for meta in result["metadatas"]:
            relative_path = meta.get("relative_path", "")
            if relative_path:
                existing.add(relative_path)
                continue
            source = meta.get("source", "")
            if source:
                existing.add(source)
        return existing
    except Exception:
        return set()


def discover_files(data_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in data_dir.rglob("*")
            if path.suffix.lower() in {".pdf", ".xlsx", ".xls", ".csv", ".tsv", ".txt", ".md"}
            and (not INCLUDE_DIRS or path.parent.name in INCLUDE_DIRS)
        ]
    )


def write_report(rows: list[dict]) -> None:
    ensure_parent(REPORT_PATH)
    with REPORT_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in REPORT_FIELDS})


def backup_existing_chroma_dir() -> Path | None:
    if not CHROMA_DIR.exists():
        return None
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = CHROMA_DIR.parent / f"{CHROMA_DIR.name}_backup_{timestamp}"
    shutil.move(str(CHROMA_DIR), str(backup_dir))
    return backup_dir


def build_index(data_dir: str = "./data") -> None:
    data_dir_path = Path(data_dir)
    backup_dir = None
    if RESET_COLLECTION:
        backup_dir = backup_existing_chroma_dir()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection("vinegar_kb")
    vector_store = ChromaVectorStore(chroma_collection=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    existing_sources = get_existing_sources(collection)
    all_files = discover_files(data_dir_path)
    target_files = all_files if (PARSE_EXISTING or RESET_COLLECTION) else [
        path
        for path in all_files
        if str(path.relative_to(data_dir_path)) not in existing_sources
        and path.name not in existing_sources
    ]

    if not target_files:
        print(f"✅ 无需处理的新文档（已存在 {len(existing_sources)} 个 source）")
        return

    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    scope_text = f"（目录过滤：{', '.join(sorted(INCLUDE_DIRS))}）" if INCLUDE_DIRS else ""
    ocr_text = "开启" if ENABLE_OCR else "关闭"
    parse_existing_text = "是" if (PARSE_EXISTING or RESET_COLLECTION) else "否"
    print(
        f"📥 发现 {len(target_files)} 个待处理文档，开始解析…{scope_text} OCR={ocr_text} "
        f"复检已有={parse_existing_text} 重建库={'是' if RESET_COLLECTION else '否'}\n"
    )
    if backup_dir:
        print(f"🗂️  已备份旧向量库到: {backup_dir}")

    report_rows = []
    ingest_docs = []
    log_counts = {"ingested": 0, "review": 0, "blocked": 0, "failed": 0, "skipped_existing": 0}

    for file_path in target_files:
        doc_type = FOLDER_TYPE.get(file_path.parent.name, "其他")
        try:
            docs, report = parse_file(file_path, doc_type)
            relative_key = str(file_path.relative_to(data_dir_path))
            already_exists = relative_key in existing_sources or file_path.name in existing_sources

            if already_exists and not PARSE_EXISTING:
                report["ingest_status"] = "skipped_existing"
                log_counts["skipped_existing"] += 1
            elif already_exists and PARSE_EXISTING:
                report["ingest_status"] = "existing_verified"
                log_counts["skipped_existing"] += 1
            elif report["quality_status"] == "pass":
                ingest_docs.extend(docs)
                report["ingest_status"] = "ingested"
                log_counts["ingested"] += 1
            elif report["quality_status"] == "review":
                report["ingest_status"] = "needs_review"
                log_counts["review"] += 1
            else:
                report["ingest_status"] = "blocked_quality"
                log_counts["blocked"] += 1

            report_rows.append(report)
            print(
                f"  {file_path.name} | parse={report['parse_status']} | "
                f"quality={report['quality_status']} | ingest={report['ingest_status']} | "
                f"chars={report['chars_total']}"
            )
        except Exception as exc:
            report = {
                "source": file_path.name,
                "relative_path": str(file_path.relative_to(data_dir_path)),
                "doc_type": doc_type,
                "parser": "unknown",
                "parse_status": "fail",
                "quality_status": "blocked",
                "ingest_status": "failed",
                "pages_total": 0,
                "pages_parsed": 0,
                "ocr_pages": 0,
                "table_pages": 0,
                "empty_pages": 0,
                "chars_total": 0,
                "avg_chars_per_page": 0,
                "valid_char_ratio": 0,
                "garble_score": 1,
                "table_count": 0,
                "table_fact_count": 0,
                "table_review_count": 0,
                "artifact_dir": "",
                "notes": str(exc),
            }
            report_rows.append(report)
            log_counts["failed"] += 1
            print(f"  ❌ {file_path.name}  错误: {exc}")

    write_report(report_rows)

    if ingest_docs:
        print(f"\n向量化并写入数据库…")
        VectorStoreIndex.from_documents(ingest_docs, storage_context=storage_context)

    print(
        f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━
解析验收报告
  ✅  已入库      : {log_counts['ingested']} 个
  👀  待复核      : {log_counts['review']} 个
  ⛔  质量拦截    : {log_counts['blocked']} 个
  ❌  解析失败    : {log_counts['failed']} 个
  ⏭️   已存在校验  : {log_counts['skipped_existing']} 个
  📄  中间产物目录: {PARSED_DIR}
  🧾  质量报告文件: {REPORT_PATH}
━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    )


if __name__ == "__main__":
    build_index(str(DATA_DIR))

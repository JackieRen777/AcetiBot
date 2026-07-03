"""上传文件解析工具。"""
import csv
from io import BytesIO, StringIO
from pathlib import Path


MAX_CONTEXT_CHARS = 4000
MAX_PDF_PAGES = 5
MAX_TABULAR_ROWS = 20

SENSOR_ALIASES = {
    "acid": "酸",
    "sour": "酸",
    "酸": "酸",
    "sweet": "甜",
    "甜": "甜",
    "bitter": "苦",
    "苦": "苦",
    "umami": "鲜",
    "鲜": "鲜",
    "salt": "咸",
    "salty": "咸",
    "咸": "咸",
}


def _limit_text(text: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}..."


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _summarize_sensor_rows(rows: list[dict]) -> str:
    if not rows:
        return ""

    first_row = rows[0]
    values = []
    for key, value in first_row.items():
        normalized = key.strip().lower()
        axis = next((label for alias, label in SENSOR_ALIASES.items() if alias in normalized), None)
        if not axis:
            continue
        value_str = str(value).strip()
        if value_str:
            values.append(f"{axis}={value_str}")

    sample_id = first_row.get("sample_id") or first_row.get("id") or first_row.get("样本编号")
    label = first_row.get("label") or first_row.get("名称") or first_row.get("样品名称")
    parts = ["用户上传了感官检测 CSV。"]
    if sample_id:
        parts.append(f"样本编号：{sample_id}。")
    if label:
        parts.append(f"样品标签：{label}。")
    if values:
        parts.append(f"五维感官值：{'，'.join(values)}。")
    return "".join(parts)


def _extract_csv_context(data: bytes) -> str:
    text = _decode_bytes(data)
    rows = list(csv.DictReader(StringIO(text)))
    summary = _summarize_sensor_rows(rows)
    preview_lines = text.splitlines()[: min(len(text.splitlines()), MAX_TABULAR_ROWS + 1)]
    preview = "\n".join(preview_lines)
    if summary:
        return _limit_text(f"{summary}\nCSV 预览：\n{preview}")
    return _limit_text(f"用户上传了 CSV 文件，内容预览如下：\n{preview}")


def _extract_excel_context(data: bytes) -> str:
    import pandas as pd

    df = pd.read_excel(BytesIO(data))
    if df.empty:
        return ""
    preview = df.head(MAX_TABULAR_ROWS).fillna("").to_csv(index=False)
    return _limit_text(f"用户上传了 Excel 表格，前 {min(len(df), MAX_TABULAR_ROWS)} 行如下：\n{preview}")


def _extract_pdf_context(data: bytes) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(BytesIO(data)) as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            page_parts = []
            text = page.extract_text() or ""
            if text.strip():
                page_parts.append(text)

            for table in page.extract_tables():
                rows = [
                    " | ".join(str(cell or "").strip() for cell in row)
                    for row in table
                    if any((cell or "").strip() for cell in row)
                ]
                if rows:
                    page_parts.append("\n".join(rows))

            if page_parts:
                parts.append("\n".join(page_parts))
            if len(" ".join(parts)) >= MAX_CONTEXT_CHARS:
                break
    return _limit_text("\n\n".join(parts))


def _extract_text_context(data: bytes) -> str:
    return _limit_text(_decode_bytes(data))


async def extract_uploaded_context(upload) -> str:
    """将上传文件提炼为补充上下文，供查询改写和回答生成使用。"""
    data = await upload.read()
    if not data:
        return ""

    suffix = Path(upload.filename or "").suffix.lower()
    if suffix == ".csv":
        context = _extract_csv_context(data)
    elif suffix in {".xlsx", ".xls"}:
        context = _extract_excel_context(data)
    elif suffix == ".pdf":
        context = _extract_pdf_context(data)
    elif suffix in {".txt", ".md"}:
        context = _extract_text_context(data)
    else:
        raise ValueError(f"暂不支持解析 {suffix or '该类型'} 文件。")

    if not context:
        raise ValueError(f"上传文件 {upload.filename} 未解析出有效内容。")
    return f"上传文件：{upload.filename}\n{context}"

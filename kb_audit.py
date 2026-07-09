"""知识库自查脚本：盘点磁盘、解析报告与向量库状态。"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import chromadb

DATA_DIR = Path("./data")
REPORT_PATH = Path("./parse_report.csv")
CHROMA_DIR = Path("./chroma_db")

FOLDERS = ["standards", "patents", "papers", "consumer", "flavor"]


def disk_inventory() -> dict:
    summary = {}
    for folder in FOLDERS:
        files = [
            path for path in (DATA_DIR / folder).rglob("*")
            if path.is_file() and path.suffix.lower() in {".pdf", ".xlsx", ".xls", ".csv", ".tsv", ".txt", ".md"}
        ]
        summary[folder] = {
            "files": len(files),
            "samples": [str(path.relative_to(DATA_DIR)) for path in files[:5]],
        }
    return summary


def report_inventory() -> dict:
    if not REPORT_PATH.is_file():
        return {"exists": False}

    rows = list(csv.DictReader(REPORT_PATH.open(encoding="utf-8")))
    by_type = Counter(row.get("doc_type", "") for row in rows)
    by_status = Counter(row.get("ingest_status", "") for row in rows)
    by_folder = defaultdict(int)
    for row in rows:
        relative_path = row.get("relative_path", "")
        folder = relative_path.split("/", 1)[0] if "/" in relative_path else "<unknown>"
        by_folder[folder] += 1

    return {
        "exists": True,
        "rows": len(rows),
        "by_type": dict(by_type),
        "by_status": dict(by_status),
        "by_folder": dict(by_folder),
        "samples": rows[:5],
    }


def chroma_inventory() -> dict:
    if not CHROMA_DIR.exists():
        return {"exists": False}
    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collection = client.get_or_create_collection("vinegar_kb")
        result = collection.get(include=["metadatas"])
        metadatas = result.get("metadatas") or []
        by_type = Counter((meta or {}).get("doc_type", "<none>") for meta in metadatas)
        by_folder = Counter(
            ((meta or {}).get("relative_path", "") or "").split("/", 1)[0] if "/" in ((meta or {}).get("relative_path", "") or "") else "<unknown>"
            for meta in metadatas
        )
        return {
            "exists": True,
            "chunks": len(metadatas),
            "by_type": dict(by_type),
            "by_folder": dict(by_folder),
            "samples": metadatas[:5],
        }
    except BaseException as exc:
        return {
            "exists": True,
            "error": str(exc),
        }


def main() -> None:
    payload = {
        "disk": disk_inventory(),
        "report": report_inventory(),
        "chroma": chroma_inventory(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

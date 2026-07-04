"""文档入库脚本 — PDF(含扫描件OCR) / Excel / TXT
特性：增量入库（跳过已处理文件）+ 入库日志"""
import os, sys, numpy as np
import chromadb, pandas as pd, pdfplumber
from pathlib import Path
from dotenv import load_dotenv
from llama_index.core import VectorStoreIndex, StorageContext, Settings, Document
from llama_index.vector_stores.chroma import ChromaVectorStore
from embeddings import SiliconFlowEmbedding

load_dotenv()
Settings.embed_model = SiliconFlowEmbedding(api_key=os.getenv("SILICONFLOW_API_KEY"))

FOLDER_TYPE = {
    "standards": "标准", "patents": "专利", "papers": "文献",
    "consumer": "消费者评价",
    "flavor": "风味文献",
    "sensor": "风味文献",
}

# ---------- OCR ----------
_ocr = None

def get_ocr():
    global _ocr
    if _ocr is None:
        from paddleocr import PaddleOCR
        print("  [OCR] 初始化 PaddleOCR…")
        _ocr = PaddleOCR()   # 3.x 无需 lang/show_log 参数
    return _ocr


def ocr_page(page) -> str:
    """对单个 pdfplumber 页面做 OCR，返回文本（PaddleOCR 3.x）"""
    try:
        img = page.to_image(resolution=200).original
        arr = np.array(img)
        result = get_ocr().predict(arr)
        if not result or not result[0]:
            return ""
        texts = result[0].get("rec_texts", [])
        return "\n".join(t for t in texts if t)
    except Exception as e:
        return ""


# ---------- 页面分类 ----------
def classify_page(page) -> str:
    """判断 PDF 页面类型：text / mixed / image / scanned"""
    text_len = len((page.extract_text() or "").strip())
    img_count = len(page.images)
    if text_len > 100:
        return "text"
    elif text_len > 20:
        return "mixed"
    elif img_count > 0:
        return "image"
    else:
        return "scanned"


def table_to_md(table: list) -> str:
    rows = [[str(c or "") for c in row] for row in table]
    if not rows:
        return ""
    header = "| " + " | ".join(rows[0]) + " |"
    sep    = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    body   = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join([header, sep, body])


# ---------- 加载 ----------
def load_pdf(path: str, doc_type: str) -> tuple[list[Document], str]:
    """先分类页面类型，再选对应处理路径；返回 (docs, status)"""
    docs = []
    status_counts = {"text": 0, "mixed": 0, "image": 0, "scanned": 0}

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            page_type = classify_page(page)
            status_counts[page_type] += 1
            parts = []

            if page_type in ("text", "mixed"):
                # 文字页 / 混合页：提取文字 + 表格
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text)
                for tbl in page.extract_tables():
                    md = table_to_md(tbl)
                    if md:
                        parts.append(f"\n【表格】\n{md}")

            elif page_type in ("image", "scanned"):
                # 图片页 / 扫描页：整页 OCR
                ocr_text = ocr_page(page)
                if ocr_text.strip():
                    parts.append(ocr_text)

            if parts:
                docs.append(Document(
                    text="\n".join(parts),
                    metadata={
                        "source": Path(path).name,
                        "doc_type": doc_type,
                        "page": i + 1,
                        "page_type": page_type,
                    },
                ))

    if not docs:
        return [], "empty"

    # 汇总状态：有 OCR 参与就标 ocr，否则 ok
    ocr_used = status_counts["image"] + status_counts["scanned"] > 0
    return docs, "ocr" if ocr_used else "ok"


def load_excel(path: str) -> list[Document]:
    df = pd.read_excel(path)
    docs = []
    for _, row in df.iterrows():
        text = " | ".join(f"{k}: {v}" for k, v in row.items() if pd.notna(v))
        meta = {k: str(v) for k, v in row.items() if pd.notna(v)}
        meta.update({"doc_type": "配方数据", "source": Path(path).name})
        docs.append(Document(text=text, metadata=meta))
    return docs


# ---------- 增量检查 ----------
def get_existing_sources(col) -> set:
    """返回知识库中已存在的 source 文件名集合"""
    try:
        result = col.get(include=["metadatas"])
        return {m.get("source", "") for m in result["metadatas"]}
    except Exception:
        return set()


# ---------- 主入库 ----------
def build_index(data_dir: str = "./data"):
    client = chromadb.PersistentClient(path="./chroma_db")
    col    = client.get_or_create_collection("vinegar_kb")
    store  = ChromaVectorStore(chroma_collection=col)
    ctx    = StorageContext.from_defaults(vector_store=store)

    existing   = get_existing_sources(col)
    all_files  = [f for f in Path(data_dir).rglob("*") if f.suffix in
                  (".pdf", ".xlsx", ".xls", ".txt", ".md")]
    new_files  = [f for f in all_files if f.name not in existing]

    log = {"ok": [], "ocr": [], "empty": [], "skip": [], "fail": []}
    log["skip"] = [f.name for f in all_files if f.name in existing]

    if not new_files:
        print(f"✅ 无新文档，知识库已是最新（跳过 {len(log['skip'])} 个文件）")
        return

    print(f"📥 发现 {len(new_files)} 个新文档，开始入库…\n")
    all_docs = []

    for f in new_files:
        doc_type = FOLDER_TYPE.get(f.parent.name, "其他")
        try:
            if f.suffix in (".xlsx", ".xls"):
                docs = load_excel(str(f))
                status = "ok" if docs else "empty"
            elif f.suffix == ".pdf":
                docs, status = load_pdf(str(f), doc_type)
            else:
                text = f.read_text(errors="ignore")
                docs  = [Document(text=text, metadata={"source": f.name, "doc_type": doc_type})]
                status = "ok" if text.strip() else "empty"

            all_docs.extend(docs)
            log[status].append(f.name)
            icon = "✅" if status == "ok" else "🔍 OCR" if status == "ocr" else "⚠️ 空"
            print(f"  {icon}  {f.name}  ({len(docs)} 片段)")
        except Exception as e:
            log["fail"].append(f.name)
            print(f"  ❌  {f.name}  错误: {e}")

    if all_docs:
        print(f"\n向量化并写入数据库…")
        VectorStoreIndex.from_documents(all_docs, storage_context=ctx)

    # 汇总报告
    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━
入库完成报告
  ✅  正常提取  : {len(log['ok'])} 个
  🔍  OCR识别  : {len(log['ocr'])} 个
  ⚠️   空文档   : {len(log['empty'])} 个（未入库）
  ❌  失败      : {len(log['fail'])} 个
  ⏭️   已存在跳过: {len(log['skip'])} 个
  📄  新增片段  : {len(all_docs)} 个
━━━━━━━━━━━━━━━━━━━━━━━━━━""")

    if log["empty"]:
        print("空文档列表:", ", ".join(log["empty"]))
    if log["fail"]:
        print("失败文档列表:", ", ".join(log["fail"]))


if __name__ == "__main__":
    build_index()

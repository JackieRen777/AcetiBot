import json
import logging
import re
import traceback
from pathlib import Path

logging.basicConfig(level=logging.ERROR, format="%(asctime)s %(levelname)s %(message)s")

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from models import AnalysisCard, QueryRequest, QueryResponse, Source
from page_labels import resolve_page_label
from query import (
    query_with_rewrite,
    prepare_query,
    stream_answer,
    build_refusal_answer,
    build_citation_analysis_card,
    build_consistency_analysis_card,
)
from source_metadata import resolve_source_metadata
from uploads import extract_uploaded_context

app = FastAPI(title="AcetiBot API", description="食醋配方优化智能体")

_SOURCE_EXCERPT_LIMIT = 320
_DATA_ROOT = (Path(__file__).resolve().parent / "data").resolve()
_KEYWORD_STOPWORDS = {
    "什么", "哪些", "如何", "怎么", "是不是", "可以", "需要", "以及", "相关", "区别", "差异",
    "标准", "要求", "依据", "关于", "进行", "用于", "适合", "产品", "配方", "工艺", "问题",
    "一个", "一种", "这个", "那个", "我们", "你们", "他们", "用户", "建议", "说明", "分析",
    "食醋", "食品", "国标", "国家标准",
}
_DATA_TERMS = (
    "csv", "excel", "xlsx", "xls",
    "电子舌", "电子鼻", "感官数据", "实验数据", "检测数据",
    "样本数据", "测定数据", "上传数据", "表格数据",
)
_DATA_ACTION_TERMS = (
    "结合", "基于", "根据", "按", "参照", "利用", "用", "依据",
    "分析", "判断", "评估", "诊断", "推荐", "建议", "优化", "改良",
    "测算", "计算", "对比",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 绑定正式域名后改为明确白名单
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/document")
def open_document(path: str = Query(..., description="资料相对路径"), page: int | None = Query(default=None)):
    relative_path = (path or "").lstrip("/").strip()
    if not relative_path:
        raise HTTPException(status_code=400, detail="缺少资料路径。")

    target = (_DATA_ROOT / relative_path).resolve()
    try:
        target.relative_to(_DATA_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法资料路径。") from exc

    if not target.is_file():
        raise HTTPException(status_code=404, detail="未找到对应资料文件。")

    headers = {"Content-Disposition": "inline"}
    if page and page > 0:
        headers["X-Document-Page"] = str(page)
    # 按文件后缀自动选择 media_type
    suffix = target.suffix.lower()
    media_type_map = {
        ".pdf": "application/pdf",
        ".csv": "text/csv; charset=utf-8",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".txt": "text/plain; charset=utf-8",
    }
    media_type = media_type_map.get(suffix, "application/octet-stream")
    return FileResponse(target, media_type=media_type, headers=headers)


async def parse_query_request(request: Request) -> tuple[str, dict | None, str | None, list | None]:
    content_type = request.headers.get("content-type", "")
    question = ""
    filters = None
    extra_context = None
    conversation_history = None

    if "application/json" in content_type:
        payload = QueryRequest.model_validate(await request.json())
        question = payload.question.strip()
        filters = payload.filters
        conversation_history = payload.conversation_history or None
    elif "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        question = str(form.get("question", "")).strip()

        if not question and getattr(upload, "filename", ""):
            question = "请结合我上传的资料，提炼关键信息并给出可执行建议。"

        filters_raw = form.get("filters")
        if filters_raw:
            filters = json.loads(filters_raw)

        if getattr(upload, "filename", ""):
            extra_context = await extract_uploaded_context(upload)
    else:
        raise ValueError("仅支持 JSON 或表单文件上传请求。")

    if not question:
        raise ValueError("请输入问题，或至少上传一个可解析的文件。")

    _validate_required_uploads(question, extra_context)
    return question, filters, extra_context, conversation_history


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _requires_uploaded_data(question: str) -> bool:
    normalized = question.strip().lower()
    if not normalized:
        return False

    mentions_data = _contains_any(normalized, _DATA_TERMS)
    mentions_action = _contains_any(normalized, _DATA_ACTION_TERMS)
    mentions_upload = "上传" in normalized

    if not mentions_data:
        return False

    if mentions_upload:
        return True

    return mentions_action


def _validate_required_uploads(question: str, extra_context: str | None) -> None:
    if extra_context:
        return

    if _requires_uploaded_data(question):
        raise ValueError(
            "当前问题明确要求结合电子舌/CSV/Excel等实测数据，但本次请求未检测到上传文件。"
            "为保证可溯源和准确性，系统不会假设不存在的数据。"
            "请先上传对应文件，或改为“仅基于知识库给出通用建议”。"
        )


def _normalize_source_text(text: str) -> str:
    cleaned = (text or "").replace("\u3000", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"(?:[、，,；;：:]\s*){2,}", "、", cleaned)
    cleaned = re.sub(r"(?<=\s)\.\s*", "。", cleaned)
    cleaned = re.sub(r"(?:[、，,；;：:]\s*)+。", "。", cleaned)
    cleaned = re.sub(r"。{2,}", "。", cleaned)
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = re.sub(r"(?<!\d)(\d)\s+(\d)(?=\s*[\u4e00-\u9fff])", r"\1.\2", cleaned)
    cleaned = re.sub(r"(^|[。；])\s*(\d+(?:\.\d+)*)\s+(?=[\u4e00-\u9fffA-Za-z])", r"\1\n\2 ", cleaned)
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(\d+(?:\.\d+)*)\s+(?=[\u4e00-\u9fffA-Za-z])", r"\n\1 ", cleaned)
    cleaned = re.sub(r"(^|[。；])\s*(表\d+)\s*", r"\1\n\2 ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    return cleaned.strip()


def _clean_source_text(text: str) -> str:
    return _normalize_source_text(text)[:_SOURCE_EXCERPT_LIMIT].strip()


def _extract_keywords(*parts: str) -> list[str]:
    keywords = []
    seen = set()

    for part in parts:
        if not part:
            continue

        normalized = part.replace("GB/T", "GBT").replace("gb/t", "GBT")
        raw_tokens = re.findall(r"GBT?\d+(?:[-—]\d+)*(?:[-—][A-Za-z0-9]+)?|[A-Za-z]{2,}(?:[-_][A-Za-z0-9]+)*|[\u4e00-\u9fff]{2,}", normalized)

        for token in raw_tokens:
            compact = re.sub(r"\s+", "", token)
            if not compact:
                continue

            candidates = [compact]
            if re.fullmatch(r"[\u4e00-\u9fff]{5,}", compact):
                for width in (4, 3, 2):
                    candidates.extend(compact[i:i + width] for i in range(len(compact) - width + 1))

            for candidate in candidates:
                word = candidate.strip()
                if len(word) < 2:
                    continue
                lowered = word.lower()
                if lowered in _KEYWORD_STOPWORDS:
                    continue
                if word in seen:
                    continue
                seen.add(word)
                keywords.append(word)

    return keywords[:40]


def _truncate_excerpt(text: str, limit: int = _SOURCE_EXCERPT_LIMIT) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit - 1].rstrip()}…"


def _score_excerpt(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    score = 0
    for keyword in keywords:
        count = lowered.count(keyword.lower())
        if not count:
            continue
        weight = 5 if re.search(r"\d", keyword) else 3 if len(keyword) >= 4 else 2
        score += count * weight
    return score


def _build_source_excerpt(text: str, *query_parts: str) -> str:
    normalized = _normalize_source_text(text)
    if not normalized:
        return ""

    keywords = _extract_keywords(*query_parts)
    if not keywords:
        return _truncate_excerpt(normalized)

    segments = []
    for block in normalized.split("\n"):
        block = block.strip()
        if not block:
            continue
        pieces = re.split(r"(?<=[。！？；])\s*", block)
        for piece in pieces:
            piece = piece.strip()
            if piece:
                segments.append(piece)

    if not segments:
        return _truncate_excerpt(normalized)

    best_text = ""
    best_score = -1
    for start in range(len(segments)):
        excerpt = ""
        for end in range(start, min(start + 3, len(segments))):
            excerpt = f"{excerpt}\n{segments[end]}".strip() if excerpt else segments[end]
            if len(excerpt) > _SOURCE_EXCERPT_LIMIT:
                break
            score = _score_excerpt(excerpt, keywords)
            if score > best_score or (score == best_score and score > 0 and len(excerpt) < len(best_text or excerpt + 'x')):
                best_score = score
                best_text = excerpt

    if best_score <= 0:
        return _truncate_excerpt(normalized)

    if (
        len(best_text) < 24
        and best_text.endswith("。")
        and any(marker in best_text for marker in ("要求", "定义", "范围", "标准", "方法"))
    ):
        try:
            idx = segments.index(best_text.split("\n")[0])
        except ValueError:
            idx = -1
        if idx >= 0 and idx + 1 < len(segments):
            expanded = f"{best_text}\n{segments[idx + 1]}".strip()
            if len(expanded) <= _SOURCE_EXCERPT_LIMIT:
                best_text = expanded

    return _truncate_excerpt(best_text)


def serialize_sources(nodes, *query_parts: str):
    return [
        {
            "text": _build_source_excerpt(node.text, *query_parts),
            "metadata": node.metadata,
        }
        for node in nodes
    ]


def _encode_answer_citations(answer: str) -> str:
    return re.sub(r"\[(\d+)\]", lambda match: f"@@CITE:{match.group(1)}@@", answer or "")


def _extract_citation_ids(answer: str) -> list[int]:
    seen = set()
    ordered_ids = []
    for match in re.findall(r"\[(\d+)\]", answer or ""):
        citation_id = int(match)
        if citation_id in seen:
            continue
        seen.add(citation_id)
        ordered_ids.append(citation_id)
    return ordered_ids


def _serialize_node_metadata(node, raw_citation_id: int | None = None) -> dict:
    metadata = dict(node.metadata or {})
    relative_path = metadata.get("relative_path")
    page_label = resolve_page_label(relative_path, metadata.get("page"))
    if page_label:
        metadata["page_label"] = page_label
    metadata.update(resolve_source_metadata(relative_path, metadata))
    if raw_citation_id is not None:
        metadata["raw_citation_id"] = raw_citation_id
        metadata["citation_id"] = raw_citation_id
    return metadata


def _document_key_for_metadata(metadata: dict, fallback: int) -> str:
    return str(metadata.get("document_key") or metadata.get("relative_path") or f"raw:{fallback}")


def _build_display_id_map(answer: str, nodes) -> dict[int, int]:
    display_map: dict[int, int] = {}
    document_order: dict[str, int] = {}

    for raw_citation_id in _extract_citation_ids(answer):
        index = raw_citation_id - 1
        if index < 0 or index >= len(nodes):
            continue
        metadata = _serialize_node_metadata(nodes[index], raw_citation_id=raw_citation_id)
        document_key = _document_key_for_metadata(metadata, raw_citation_id)
        if document_key not in document_order:
            document_order[document_key] = len(document_order) + 1
        display_map[raw_citation_id] = document_order[document_key]

    return display_map


def serialize_citation_catalog(nodes, answer: str | None = None):
    display_id_map = _build_display_id_map(answer or "", nodes) if answer else {}
    serialized = []
    for index, node in enumerate(nodes or [], start=1):
        metadata = _serialize_node_metadata(node, raw_citation_id=index)
        display_id = display_id_map.get(index, index)
        metadata["display_id"] = display_id
        metadata["citation_id"] = display_id
        metadata["raw_citation_ids"] = [index]
        metadata["cited_pages"] = [metadata.get("page")] if metadata.get("page") else []
        metadata["cited_page_labels"] = [metadata.get("page_label")] if metadata.get("page_label") else []
        serialized.append(
            {
                "text": "",
                "metadata": metadata,
            }
        )
    return serialized


def serialize_cited_sources(answer: str, nodes):
    citation_ids = _extract_citation_ids(answer)
    if not citation_ids:
        return []

    display_id_map = _build_display_id_map(answer, nodes)
    article_records: dict[str, dict] = {}
    article_order: list[str] = []

    for raw_citation_id in citation_ids:
        index = raw_citation_id - 1
        if index < 0 or index >= len(nodes):
            continue

        metadata = _serialize_node_metadata(nodes[index], raw_citation_id=raw_citation_id)
        display_id = display_id_map.get(raw_citation_id, raw_citation_id)
        document_key = _document_key_for_metadata(metadata, raw_citation_id)
        if document_key not in article_records:
            article_order.append(document_key)
            article_records[document_key] = {
                "text": "",
                "metadata": {
                    **metadata,
                    "citation_id": display_id,
                    "display_id": display_id,
                    "article_id": display_id,
                    "raw_citation_ids": [raw_citation_id],
                    "cited_pages": [metadata.get("page")] if metadata.get("page") else [],
                    "cited_page_labels": [metadata.get("page_label")] if metadata.get("page_label") else [],
                },
            }
            continue

        record_metadata = article_records[document_key]["metadata"]
        if raw_citation_id not in record_metadata["raw_citation_ids"]:
            record_metadata["raw_citation_ids"].append(raw_citation_id)
        if metadata.get("page") and metadata.get("page") not in record_metadata["cited_pages"]:
            record_metadata["cited_pages"].append(metadata["page"])
        if metadata.get("page_label") and metadata.get("page_label") not in record_metadata["cited_page_labels"]:
            record_metadata["cited_page_labels"].append(metadata["page_label"])

    return [article_records[key] for key in article_order]


def serialize_analysis(cards):
    return [
        AnalysisCard(
            title=card["title"],
            body=card["body"],
            items=card.get("items", []),
            tone=card.get("tone", "neutral"),
        )
        for card in cards
    ]


@app.post("/query", response_model=QueryResponse)
async def query_formula(request: Request):
    try:
        question, filters, extra_context, conversation_history = await parse_query_request(request)
        result = query_with_rewrite(question, filters=filters, extra_context=extra_context, conversation_history=conversation_history)
        formatted_answer = _encode_answer_citations(result["answer"])
        sources = [
            Source(text=item["text"], metadata=item["metadata"])
            for item in serialize_cited_sources(result["answer"], result["sources"])
        ]
        citation_catalog = [
            Source(text=item["text"], metadata=item["metadata"])
            for item in serialize_citation_catalog(result["sources"], answer=result["answer"])
        ]
        return QueryResponse(
            answer=formatted_answer,
            sources=sources,
            citation_catalog=citation_catalog,
            analysis=serialize_analysis(result.get("analysis", [])),
        )
    except FileNotFoundError:
        return QueryResponse(answer="当前知识库暂未就绪，请稍后再试。", sources=[])
    except ValueError as e:
        return QueryResponse(answer=str(e), sources=[])
    except Exception as e:
        return QueryResponse(answer="当前问答服务暂时不可用，请稍后重试。", sources=[])


@app.post("/query/stream")
async def query_formula_stream(request: Request):
    try:
        question, filters, extra_context, conversation_history = await parse_query_request(request)
        prepared = prepare_query(question, filters=filters, extra_context=extra_context, conversation_history=conversation_history)
    except FileNotFoundError:
        payload = {"type": "error", "message": "当前知识库暂未就绪，请稍后再试。"}
        return StreamingResponse(iter([json.dumps(payload, ensure_ascii=False) + "\n"]), media_type="application/x-ndjson; charset=utf-8")
    except ValueError as e:
        payload = {"type": "error", "message": str(e)}
        return StreamingResponse(iter([json.dumps(payload, ensure_ascii=False) + "\n"]), media_type="application/x-ndjson; charset=utf-8")
    except Exception as e:
        logging.error("prepare_query exception:\n%s", traceback.format_exc())
        payload = {"type": "error", "message": "当前问答服务暂时不可用，请稍后重试。"}
        return StreamingResponse(iter([json.dumps(payload, ensure_ascii=False) + "\n"]), media_type="application/x-ndjson; charset=utf-8")

    def event_stream():
        try:
            analysis = list(prepared.get("analysis", []))
            final_answer = ""
            yield json.dumps({
                "type": "catalog",
                "sources": serialize_citation_catalog(prepared["nodes"]),
            }, ensure_ascii=False) + "\n"
            if prepared.get("gate", {}).get("passed"):
                for event in stream_answer(
                    question,
                    prepared["nodes"],
                    prepared["route"],
                    prepared["answer_profile"],
                    prepared.get("extra_context"),
                    table_aggregation=prepared.get("table_aggregation"),
                    standard_metric_aggregation=prepared.get("standard_metric_aggregation"),
                    conversation_history=prepared.get("conversation_history"),
                ):
                    if event.get("type") == "checks":
                        analysis.append(build_citation_analysis_card(event.get("citation_check", {})))
                        analysis.append(build_consistency_analysis_card(event.get("consistency_check", {})))
                        continue
                    if event.get("type") == "delta":
                        final_answer += event.get("content", "")
                    elif event.get("type") == "replace":
                        final_answer = event.get("content", "")
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            else:
                refusal = build_refusal_answer(question, prepared["route"], prepared["gate"])
                final_answer = refusal
                yield json.dumps({"type": "delta", "content": refusal}, ensure_ascii=False) + "\n"
                analysis.append(
                    {
                        "title": "回答引用检查",
                        "body": "本轮已在证据门禁阶段拒答，因此未进入回答级引用检查。",
                        "items": ["状态：skipped"],
                        "tone": "neutral",
                    }
                )
                analysis.append(
                    {
                        "title": "回答一致性检查",
                        "body": "本轮已在证据门禁阶段拒答，因此未进入回答级一致性检查。",
                        "items": ["状态：skipped"],
                        "tone": "neutral",
                    }
                )
            yield json.dumps({
                "type": "done",
                "answer": _encode_answer_citations(final_answer),
                "sources": serialize_cited_sources(final_answer, prepared["nodes"]),
                "citation_catalog": serialize_citation_catalog(prepared["nodes"], answer=final_answer),
                "analysis": analysis,
                "rewritten": prepared["rewritten"],
            }, ensure_ascii=False) + "\n"
        except Exception as e:
            logging.error("event_stream exception:\n%s", traceback.format_exc())
            yield json.dumps({"type": "error", "message": "当前问答服务暂时不可用，请稍后重试。"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson; charset=utf-8")

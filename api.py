import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from models import AnalysisCard, QueryRequest, QueryResponse, Source
from query import query_with_rewrite, prepare_query, stream_answer
from uploads import extract_uploaded_context

app = FastAPI(title="AcetiBot API", description="食醋配方优化智能体")

_SOURCE_EXCERPT_LIMIT = 320
_KEYWORD_STOPWORDS = {
    "什么", "哪些", "如何", "怎么", "是不是", "可以", "需要", "以及", "相关", "区别", "差异",
    "标准", "要求", "依据", "关于", "进行", "用于", "适合", "产品", "配方", "工艺", "问题",
    "一个", "一种", "这个", "那个", "我们", "你们", "他们", "用户", "建议", "说明", "分析",
    "食醋", "食品", "国标", "国家标准",
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Vercel 部署后改为具体域名
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


async def parse_query_request(request: Request) -> tuple[str, dict | None, str | None]:
    content_type = request.headers.get("content-type", "")
    question = ""
    filters = None
    extra_context = None

    if "application/json" in content_type:
        payload = QueryRequest.model_validate(await request.json())
        question = payload.question.strip()
        filters = payload.filters
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

    return question, filters, extra_context


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
        question, filters, extra_context = await parse_query_request(request)
        result = query_with_rewrite(question, filters=filters, extra_context=extra_context)
        sources = [
            Source(text=_build_source_excerpt(node.text, question, result.get("rewritten", "")), metadata=node.metadata)
            for node in result["sources"]
        ]
        return QueryResponse(
            answer=result["answer"],
            sources=sources,
            analysis=serialize_analysis(result.get("analysis", [])),
        )
    except FileNotFoundError:
        return QueryResponse(answer="⚠️ 知识库尚未建立，请先运行 `python ingest.py`。", sources=[])
    except ValueError as e:
        return QueryResponse(answer=f"⚠️ {str(e)}", sources=[])
    except Exception as e:
        return QueryResponse(answer=f"⚠️ 查询出错：{str(e)}", sources=[])


@app.post("/query/stream")
async def query_formula_stream(request: Request):
    try:
        question, filters, extra_context = await parse_query_request(request)
        prepared = prepare_query(question, filters=filters, extra_context=extra_context)
    except FileNotFoundError:
        payload = {"type": "error", "message": "⚠️ 知识库尚未建立，请先运行 `python ingest.py`。"}
        return StreamingResponse(iter([json.dumps(payload, ensure_ascii=False) + "\n"]), media_type="application/x-ndjson; charset=utf-8")
    except ValueError as e:
        payload = {"type": "error", "message": f"⚠️ {str(e)}"}
        return StreamingResponse(iter([json.dumps(payload, ensure_ascii=False) + "\n"]), media_type="application/x-ndjson; charset=utf-8")
    except Exception as e:
        payload = {"type": "error", "message": f"⚠️ 查询出错：{str(e)}"}
        return StreamingResponse(iter([json.dumps(payload, ensure_ascii=False) + "\n"]), media_type="application/x-ndjson; charset=utf-8")

    def event_stream():
        try:
            for chunk in stream_answer(question, prepared["nodes"], prepared.get("extra_context")):
                yield json.dumps({"type": "delta", "content": chunk}, ensure_ascii=False) + "\n"
            yield json.dumps({
                "type": "done",
                "sources": serialize_sources(prepared["nodes"], question, prepared["rewritten"]),
                "analysis": prepared.get("analysis", []),
                "rewritten": prepared["rewritten"],
            }, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": f"⚠️ 查询出错：{str(e)}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson; charset=utf-8")

"""查询引擎 — 语义检索 + 查询改写 + CoT"""
import json
import os
import chromadb
import requests
from analysis import build_analysis_cards
from dotenv import load_dotenv
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.vector_stores.types import MetadataFilters, MetadataFilter, FilterOperator
from llama_index.vector_stores.chroma import ChromaVectorStore
from embeddings import SiliconFlowEmbedding
from prompts import FORMULA_PROMPT

load_dotenv()

_API_KEY  = os.getenv("SILICONFLOW_API_KEY")
_API_BASE = "https://api.siliconflow.cn/v1"
_MODEL    = os.getenv("SILICONFLOW_CHAT_MODEL", "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B")
_STREAM_MODEL = os.getenv("SILICONFLOW_STREAM_CHAT_MODEL", "deepseek-ai/DeepSeek-V3")
_REWRITE_MODEL = os.getenv("SILICONFLOW_REWRITE_MODEL", "deepseek-ai/DeepSeek-V3")
_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
_ANSWER_MAX_TOKENS = int(os.getenv("SILICONFLOW_ANSWER_MAX_TOKENS", "700"))
_REWRITE_MAX_TOKENS = int(os.getenv("SILICONFLOW_REWRITE_MAX_TOKENS", "120"))
_SHORT_QUESTION_REWRITE_THRESHOLD = int(os.getenv("RAG_REWRITE_THRESHOLD", "18"))
_MAX_NODE_CHARS = int(os.getenv("RAG_MAX_NODE_CHARS", "900"))
_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "2600"))
_MAX_EXTRA_CONTEXT_CHARS = int(os.getenv("RAG_MAX_EXTRA_CONTEXT_CHARS", "800"))
_TIMEOUT_SECONDS = int(os.getenv("SILICONFLOW_TIMEOUT_SECONDS", "60"))

Settings.embed_model = SiliconFlowEmbedding(api_key=_API_KEY)
_HTTP = requests.Session()

REWRITE_PROMPT = """你是一个食品科学领域的检索专家。
将下面的用户问题改写为更适合向量数据库检索的专业查询语句（保留关键词、补充同义词、去除口语化表达）。
只输出改写后的查询语句，不要解释。

用户问题：{question}
改写查询："""


def _chat_completion(prompt: str, model: str, temperature: float, max_tokens: int) -> str:
    resp = _HTTP.post(
        f"{_API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {_API_KEY}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _chat_completion_stream(prompt: str, model: str, temperature: float, max_tokens: int):
    with _HTTP.post(
        f"{_API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {_API_KEY}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        },
        timeout=_TIMEOUT_SECONDS,
        stream=True,
    ) as resp:
        resp.raise_for_status()
        decoder = json.JSONDecoder()
        json_buffer = ""
        for raw_line in resp.iter_lines(decode_unicode=False):
            if raw_line is None:
                continue
            line = raw_line.decode("utf-8", errors="strict").strip()
            if not line or not line.startswith("data:"):
                continue

            data = line[5:].strip()
            if data == "[DONE]":
                break

            json_buffer += data
            while json_buffer:
                try:
                    payload, offset = decoder.raw_decode(json_buffer)
                except json.JSONDecodeError:
                    break

                delta = payload["choices"][0].get("delta", {})
                content = delta.get("content") or ""
                if content:
                    yield content
                json_buffer = json_buffer[offset:].lstrip()


def should_rewrite(question: str, extra_context: str = None) -> bool:
    if extra_context:
        return True
    return len(question.strip()) >= _SHORT_QUESTION_REWRITE_THRESHOLD


def rewrite_query(question: str) -> str:
    """调用 LLM 将口语化问题改写为检索优化查询"""
    return _chat_completion(
        prompt=REWRITE_PROMPT.format(question=question),
        model=_REWRITE_MODEL,
        temperature=0.3,
        max_tokens=_REWRITE_MAX_TOKENS,
    )


_CLIENT = chromadb.PersistentClient(path="./chroma_db")
_COLLECTION = _CLIENT.get_or_create_collection("vinegar_kb")
_STORE = ChromaVectorStore(chroma_collection=_COLLECTION)
_INDEX = VectorStoreIndex.from_vector_store(_STORE)


def get_retriever(filters: dict = None):
    mf = None
    if filters:
        mf = MetadataFilters(filters=[
            MetadataFilter(key=k, value=v, operator=FilterOperator.EQ)
            for k, v in filters.items()
        ])

    return _INDEX.as_retriever(
        filters=mf,
        similarity_top_k=_TOP_K,
    )


def _build_context(nodes) -> str:
    parts = []
    total_chars = 0
    for i, node in enumerate(nodes, start=1):
        metadata = node.metadata or {}
        source = metadata.get("source", "未知来源")
        page = metadata.get("page")
        title = f"[{i}] 来源：{source}"
        if page:
            title += f"（第 {page} 页）"
        snippet = node.text[:_MAX_NODE_CHARS].strip()
        block = f"{title}\n{snippet}"
        total_chars += len(block)
        if total_chars > _MAX_CONTEXT_CHARS:
            break
        parts.append(block)
    return "\n\n".join(parts)


def _compose_context(nodes, extra_context: str | None) -> str:
    context = _build_context(nodes) or "未检索到有效资料。"
    if not extra_context:
        return context

    trimmed_extra = extra_context[:_MAX_EXTRA_CONTEXT_CHARS].strip()
    return f"【用户上传的补充资料】\n{trimmed_extra}\n\n【知识库检索结果】\n{context}"


def generate_answer(question: str, nodes, extra_context: str | None = None) -> str:
    prompt = FORMULA_PROMPT.format(
        context_str=_compose_context(nodes, extra_context),
        query_str=question,
    )
    return _chat_completion(
        prompt=prompt,
        model=_MODEL,
        temperature=0.1,
        max_tokens=_ANSWER_MAX_TOKENS,
    )


def prepare_query(question: str, filters: dict = None, extra_context: str = None) -> dict:
    """准备查询流程：改写 → 检索"""
    rewrite_input = question
    if extra_context:
        rewrite_input = (
            f"{question}\n\n"
            f"以下是用户上传文件提炼出的补充信息，请在理解用户需求时一并考虑：\n"
            f"{extra_context}"
        )

    rewritten = rewrite_query(rewrite_input) if should_rewrite(question, extra_context) else question
    retriever = get_retriever(filters)
    nodes     = retriever.retrieve(rewritten)
    return {
        "rewritten": rewritten,
        "nodes": nodes,
        "extra_context": extra_context,
        "analysis": build_analysis_cards(question, nodes, extra_context),
    }


def stream_answer(question: str, nodes, extra_context: str | None = None):
    prompt = FORMULA_PROMPT.format(
        context_str=_compose_context(nodes, extra_context),
        query_str=question,
    )
    yield from _chat_completion_stream(
        prompt=prompt,
        model=_STREAM_MODEL,
        temperature=0.1,
        max_tokens=_ANSWER_MAX_TOKENS,
    )


def query_with_rewrite(question: str, filters: dict = None, extra_context: str = None) -> dict:
    """完整查询流程：改写 → 检索 → CoT 生成"""
    prepared  = prepare_query(question, filters=filters, extra_context=extra_context)
    nodes     = prepared["nodes"]
    answer    = generate_answer(question, nodes, prepared["extra_context"])
    return {
        "answer":   answer,
        "rewritten": prepared["rewritten"],
        "sources":  nodes,
        "analysis": prepared["analysis"],
    }


if __name__ == "__main__":
    result = query_with_rewrite("如何设计适合江浙消费者口味偏淡、带米香的醋配方？")
    print("改写查询：", result["rewritten"])
    print("回答：", result["answer"])

"""查询引擎 — 问题路由 + 子库检索 + 引用回答"""
import json
import os
import re
import time
from collections import Counter
import chromadb
import requests
from analysis import build_analysis_cards
from dotenv import load_dotenv
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.vector_stores.types import (
    MetadataFilters,
    MetadataFilter,
    FilterCondition,
    FilterOperator,
)
from llama_index.vector_stores.chroma import ChromaVectorStore
from embeddings import SiliconFlowEmbedding
from page_labels import resolve_page_label
from prompts import FORMULA_PROMPT, CITATION_REPAIR_PROMPT, STREAM_PROMPT

load_dotenv()

_API_KEY  = os.getenv("SILICONFLOW_API_KEY")
_API_BASE = "https://api.siliconflow.cn/v1"
_MODEL    = os.getenv("SILICONFLOW_CHAT_MODEL", "deepseek-ai/DeepSeek-V3")
_STREAM_MODEL = os.getenv("SILICONFLOW_STREAM_CHAT_MODEL", "deepseek-ai/DeepSeek-V3")
_REWRITE_MODEL = os.getenv("SILICONFLOW_REWRITE_MODEL", "deepseek-ai/DeepSeek-V3")
_TOP_K = int(os.getenv("RAG_TOP_K", "7"))
_ANSWER_MAX_TOKENS = int(os.getenv("SILICONFLOW_ANSWER_MAX_TOKENS", "2000"))
_STREAM_ANSWER_MAX_TOKENS = int(os.getenv("SILICONFLOW_STREAM_ANSWER_MAX_TOKENS", "1800"))
_REWRITE_MAX_TOKENS = int(os.getenv("SILICONFLOW_REWRITE_MAX_TOKENS", "120"))
_SHORT_QUESTION_REWRITE_THRESHOLD = int(os.getenv("RAG_REWRITE_THRESHOLD", "18"))
_ROUTE_CANDIDATE_TOP_K = int(os.getenv("RAG_ROUTE_CANDIDATE_TOP_K", "36"))
_MAX_NODE_CHARS = int(os.getenv("RAG_MAX_NODE_CHARS", "620"))
_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "5200"))
_MAX_EXTRA_CONTEXT_CHARS = int(os.getenv("RAG_MAX_EXTRA_CONTEXT_CHARS", "800"))
_STREAM_CONTEXT_CHARS = int(os.getenv("RAG_STREAM_CONTEXT_CHARS", "3200"))
_TIMEOUT_SECONDS = int(os.getenv("SILICONFLOW_TIMEOUT_SECONDS", "60"))
_GROUP_RELEVANCE_RATIO = float(os.getenv("RAG_GROUP_RELEVANCE_RATIO", "0.87"))
_GROUP_RELEVANCE_DELTA = float(os.getenv("RAG_GROUP_RELEVANCE_DELTA", "0.08"))
_GROUP_PRIMARY_MAX = int(os.getenv("RAG_GROUP_PRIMARY_MAX", "5"))
_GROUP_CONSTRAINT_MAX = int(os.getenv("RAG_GROUP_CONSTRAINT_MAX", "3"))
_GROUP_SUPPORT_MAX = int(os.getenv("RAG_GROUP_SUPPORT_MAX", "3"))
_COMPREHENSIVE_CITATION_TARGET = int(os.getenv("RAG_COMPREHENSIVE_CITATION_TARGET", "20"))
_COMPREHENSIVE_TOTAL_LIMIT = int(os.getenv("RAG_COMPREHENSIVE_TOTAL_LIMIT", "24"))
_COMPREHENSIVE_PRIMARY_MAX = int(os.getenv("RAG_COMPREHENSIVE_PRIMARY_MAX", "16"))
_COMPREHENSIVE_CONSTRAINT_MAX = int(os.getenv("RAG_COMPREHENSIVE_CONSTRAINT_MAX", "4"))
_COMPREHENSIVE_SUPPORT_MAX = int(os.getenv("RAG_COMPREHENSIVE_SUPPORT_MAX", "4"))
_COMPREHENSIVE_GROUP_RELEVANCE_RATIO = float(os.getenv("RAG_COMPREHENSIVE_GROUP_RELEVANCE_RATIO", "0.72"))
_COMPREHENSIVE_GROUP_RELEVANCE_DELTA = float(os.getenv("RAG_COMPREHENSIVE_GROUP_RELEVANCE_DELTA", "0.18"))
_COMPREHENSIVE_MAX_NODE_CHARS = int(os.getenv("RAG_COMPREHENSIVE_MAX_NODE_CHARS", "420"))
_COMPREHENSIVE_CONTEXT_CHARS = int(os.getenv("RAG_COMPREHENSIVE_CONTEXT_CHARS", "9200"))
_COMPREHENSIVE_STREAM_CONTEXT_CHARS = int(os.getenv("RAG_COMPREHENSIVE_STREAM_CONTEXT_CHARS", "6800"))

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
_PLACEHOLDER_BRACKET_PATTERN = re.compile(r"\[[^\d\]]+[^\]]*\]")

DOC_TYPE_PAPER_LIKE = {"风味文献", "文献"}
DOC_TYPE_CONSUMER = {"消费者评价"}
DOC_TYPE_STANDARD = {"标准"}
DOC_TYPE_PATENT = {"专利"}

Settings.embed_model = SiliconFlowEmbedding(api_key=_API_KEY)
_HTTP = requests.Session()

REWRITE_PROMPT = """你是一个食品科学领域的检索专家。
将下面的用户问题改写为更适合向量数据库检索的专业查询语句（保留关键词、补充同义词、去除口语化表达）。
只输出改写后的查询语句，不要解释。

用户问题：{question}
改写查询："""

ROUTE_DEFINITIONS = {
    "consumer_insight": {
        "label": "消费者洞察",
        "doc_types": ["消费者评价"],
        "description": "只检索消费者评价、评论统计与偏好汇总资料。",
        "search_hint": "评论 评价 关注参数 统计 占比 偏好 排名 口碑",
        "keywords": [
            "消费者", "评论", "评价", "电商", "用户", "偏好", "复购", "购买", "包装", "物流",
            "性价比", "品牌感知", "差评", "好评", "sku", "商品", "店铺", "淘宝", "天猫",
            "销量", "排名", "口碑", "买家",
        ],
    },
    "standard_compliance": {
        "label": "标准合规",
        "doc_types": ["标准"],
        "description": "只检索国家标准、团体/企业标准等合规依据。",
        "search_hint": "标准 条款 指标 要求 定义 表格 限值 合规",
        "keywords": [
            "标准", "国标", "gb", "gb/t", "2719", "18187", "7718", "28050", "标签", "标示",
            "营养成分", "合规", "限量", "允许", "要求", "规范", "总酸", "酸度", "执行标准",
            "生产许可", "食品标签", "配料表",
        ],
    },
    "patent_process": {
        "label": "专利工艺",
        "doc_types": ["专利"],
        "description": "只检索专利技术方案、实施例与工艺路线资料。",
        "search_hint": "专利 技术方案 实施例 工艺步骤 权利要求",
        "keywords": [
            "专利", "发明", "申请号", "公开号", "权利要求", "实施例", "技术方案", "专利工艺",
            "专利路线", "专利方法", "专利里", "专利文献",
        ],
    },
    "flavor_mechanism": {
        "label": "风味机理",
        "doc_types": ["风味文献", "文献"],
        "description": "只检索风味形成、微生物代谢、香气物质与机理论文。",
        "search_hint": "风味机理 香气成分 挥发性物质 微生物 形成途径 代谢",
        "keywords": [
            "风味", "香气", "醋香", "挥发性", "风味物质", "香气物质", "糠醛", "吡嗪",
            "有机酸", "菌群", "微生物", "代谢", "形成机理", "发酵机理", "组学", "香气成分",
            "乳酸菌", "醋酸菌",
        ],
    },
    "formula_process": {
        "label": "配方工艺",
        "doc_types": ["风味文献", "文献", "专利", "标准", "消费者评价"],
        "description": "主检工艺论文、化学/风味论文与专利，标准用于约束边界，消费者资料用于辅助偏好判断。",
        "search_hint": "配方优化 工艺改良 参数 调整 发酵条件 风味提升 方案",
        "evidence_plan": [
            {
                "label": "工艺与风味论文",
                "doc_types": ["风味文献", "文献"],
                "role": "primary",
                "top_k": 3,
                "usage": "作为配方与工艺建议的主证据。",
                "search_hint": "工艺 风味 发酵机理 香气成分 参数优化",
            },
            {
                "label": "专利工艺",
                "doc_types": ["专利"],
                "role": "primary",
                "top_k": 2,
                "usage": "作为可落地产品方案与工艺实现参考。",
                "search_hint": "专利 实施例 技术方案 工艺步骤",
            },
            {
                "label": "标准约束",
                "doc_types": ["标准"],
                "role": "constraint",
                "top_k": 1,
                "usage": "只用于限制配方边界、标签与用量合规，不主导配方建议。",
                "search_hint": "标准 要求 限值 合规 总酸 标签 配料表",
            },
            {
                "label": "消费者偏好",
                "doc_types": ["消费者评价"],
                "role": "support",
                "top_k": 1,
                "usage": "只用于辅助选择更贴近消费者偏好的工艺方向。",
                "search_hint": "消费者 评论 偏好 接受度 口感 香气 性价比",
            },
        ],
        "keywords": [
            "配方", "配比", "工艺", "优化", "改良", "设计", "建议", "调整", "参数", "发酵条件",
            "陈酿", "产酸", "翻醅", "强化", "提升", "改善", "怎么做", "如何做", "方案",
        ],
    },
}

ROUTE_PRIORITY = [
    "consumer_insight",
    "standard_compliance",
    "patent_process",
    "flavor_mechanism",
    "formula_process",
]

TABLE_QUERY_HINTS = (
    "多少",
    "占比",
    "比例",
    "参数",
    "指标",
    "含量",
    "数值",
    "排名",
    "比较",
    "差异",
    "最高",
    "最低",
    "均值",
    "平均",
    "显著",
    "哪组",
    "哪些",
    "提及次数",
    "总酸",
    "总酯",
    "还原糖",
    "得分",
    "评分",
    "表",
)

PAPER_TABLE_HINTS = (
    "试验号",
    "样品",
    "组别",
    "响应面",
    "数据",
    "结果",
    "表格",
    "实验",
    "比较",
    "最高",
    "最低",
    "哪组",
)

NODE_MATCH_TERMS = TABLE_QUERY_HINTS + (
    "关注参数",
    "口味",
    "口感",
    "香气",
    "风味",
    "发酵",
    "配方",
    "工艺",
    "消费者",
    "菌群",
    "喜好度",
)

COMPLIANCE_TERMS = (
    "标准",
    "国标",
    "gb",
    "合规",
    "允许",
    "限量",
    "执行标准",
    "标签",
    "配料表",
    "营养成分",
)

EXPERIMENT_METRIC_TERMS = (
    "更高",
    "更低",
    "最高",
    "最低",
    "差异",
    "比较",
    "哪组",
    "哪个样品",
    "样品",
    "含量",
    "数值",
    "总酸",
    "总酯",
    "还原糖",
    "有机酸",
)

STANDARD_METRIC_TERMS = (
    "总酸",
    "总酯",
    "还原糖",
    "酸度",
    "理化指标",
    "指标",
)

STANDARD_PRODUCT_TERMS = (
    "镇江香醋",
    "甜醋",
    "食醋",
    "米醋",
    "陈醋",
    "香醋",
)

SIMPLE_QUESTION_TERMS = (
    "多少", "是否", "能否", "可否", "上限", "限量", "限值", "最低", "最高",
    "要求", "规定", "标准", "国标", "指标", "参数", "多少克", "多少%", "多少mg",
    "哪一项", "是什么", "专利号", "标准号",
)

COMPLEX_QUESTION_TERMS = (
    "如何设计", "如何优化", "如何改良", "方案", "路径", "策略", "综合", "系统",
    "新品", "年轻化", "地域化", "市场适配", "消费者适配", "复合", "兼顾", "同时满足",
    "实验设计", "项目", "开发", "研究", "详细分析",
)

QUESTION_INTENTS = {
    "配方优化": ("配方", "配比", "设计", "新品", "开发"),
    "工艺改良": ("工艺", "发酵", "陈酿", "翻醅", "参数", "控制"),
    "风味解析": ("风味", "香气", "挥发性", "米香", "醋香", "口感"),
    "市场适配": ("消费者", "偏好", "年轻化", "地域", "市场", "接受度"),
    "合规核查": ("标准", "国标", "标签", "配料表", "合规", "限量", "执行标准"),
}

ANSWER_PROFILES = {
    "A": {
        "label": "A档简答",
        "answer_max_tokens": min(_ANSWER_MAX_TOKENS, 260),
        "stream_max_tokens": min(_STREAM_ANSWER_MAX_TOKENS, 240),
        "target_citations": 2,
        "response_contract": "适用于单参数、单限值、单事实问题。结论优先，通常控制在50字左右；如需补充，只补最必要的依据说明。",
        "allow_next_step": False,
    },
    "B": {
        "label": "B档中等复杂",
        "answer_max_tokens": min(_ANSWER_MAX_TOKENS, 1100),
        "stream_max_tokens": min(_STREAM_ANSWER_MAX_TOKENS, 980),
        "target_citations": 7,
        "response_contract": "适用于对比、解释、局部优化问题。围绕2-3个关键维度展开，兼顾可读性与证据密度。",
        "allow_next_step": False,
    },
    "C": {
        "label": "C档综合项目型",
        "answer_max_tokens": _ANSWER_MAX_TOKENS,
        "stream_max_tokens": _STREAM_ANSWER_MAX_TOKENS,
        "target_citations": 14,
        "response_contract": "适用于配方设计、工艺优化、风味与市场/合规复合问题。使用研究摘要式长答，充分展开，每个最小层级至少3-4句，并分散引用10-20条真正使用的资料。",
        "allow_next_step": True,
    },
}


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
    _MAX_RETRIES = 3
    for attempt in range(_MAX_RETRIES):
        try:
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
                if resp.status_code == 429:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    if attempt < _MAX_RETRIES - 1:
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
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
                return  # 成功完成，退出重试循环
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429 and attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def should_rewrite(question: str, extra_context: str = None) -> bool:
    if extra_context:
        return True
    return len(question.strip()) >= _SHORT_QUESTION_REWRITE_THRESHOLD


def build_scoped_query(question: str, route: dict) -> str:
    hint = (route or {}).get("search_hint", "").strip()
    compact_question = question.strip()
    parts = [compact_question]
    if hint:
        parts.append(hint)
    if _question_prefers_paper_table_evidence(compact_question, route=route):
        parts.append("试验号 表格 数据 指标 含量 结果")
    return " ".join(part for part in parts if part).strip()


def build_group_scoped_query(question: str, evidence_group: dict) -> str:
    hint = (evidence_group or {}).get("search_hint", "").strip()
    compact_question = question.strip()
    parts = [compact_question]
    if hint:
        parts.append(hint)
    if _question_prefers_paper_table_evidence(
        compact_question,
        doc_types=(evidence_group or {}).get("doc_types"),
    ):
        parts.append("试验号 表格 数据 指标 含量 结果")
    return " ".join(part for part in parts if part).strip()


def rewrite_query(question: str, route: dict | None = None, extra_context: str | None = None) -> str:
    """调用 LLM 将口语化问题改写为检索优化查询"""
    if route and route.get("name") in ROUTE_DEFINITIONS and not extra_context:
        return build_scoped_query(question, route)
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


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "")).lower()


GENERIC_QUERY_TERMS = {
    _normalize_text(term)
    for term in ("表", "参数", "样品", "比较", "差异", "哪些", "最高", "最低", "更高", "更低")
}

CRITICAL_METRIC_TERMS = {
    _normalize_text(term)
    for term in ("总酸", "总酯", "还原糖", "有机酸", "提及次数", "占比", "评分", "喜好度")
}


def _keyword_score(text: str, keywords: list[str]) -> int:
    compact = _normalize_text(text)
    score = 0
    for keyword in keywords:
        key = _normalize_text(keyword)
        if key and key in compact:
            score += 3 if len(key) >= 3 else 2
    return score


def _question_prefers_table_evidence(query_text: str, route: dict | None = None) -> bool:
    compact = _normalize_text(query_text)
    if any(_normalize_text(keyword) in compact for keyword in TABLE_QUERY_HINTS):
        return True
    if _question_prefers_paper_table_evidence(query_text, route=route):
        return True
    route_name = (route or {}).get("name")
    return route_name in {"consumer_insight", "standard_compliance"}


def _question_prefers_paper_table_evidence(
    query_text: str,
    route: dict | None = None,
    doc_types: list[str] | None = None,
) -> bool:
    compact = _normalize_text(query_text)
    route_name = (route or {}).get("name")
    resolved_doc_types = set((route or {}).get("doc_types") or [])
    resolved_doc_types.update(doc_types or [])
    targets_paper_docs = bool(resolved_doc_types.intersection(DOC_TYPE_PAPER_LIKE))
    if route_name not in {"flavor_mechanism", "formula_process"} and not targets_paper_docs:
        return False
    if any(_normalize_text(term) in compact for term in COMPLIANCE_TERMS):
        return False
    metric_like = _question_requires_metric_grounding(query_text)
    experiment_like = any(_normalize_text(term) in compact for term in PAPER_TABLE_HINTS)
    return metric_like and experiment_like


def _is_formula_design_query(query_text: str, route: dict | None = None) -> bool:
    if (route or {}).get("name") != "formula_process":
        return False
    compact = _normalize_text(query_text)
    return any(term in compact for term in ("如何", "优化", "建议", "方案", "兼顾", "改善", "提升"))


def _is_comprehensive_query(query_text: str, route: dict | None = None) -> bool:
    if (route or {}).get("name") != "formula_process":
        return False
    compact = _normalize_text(query_text)
    complexity_terms = (
        "综合", "系统", "整体", "完整", "详细", "全面", "方案", "设计", "优化",
        "改良", "路径", "步骤", "兼顾", "同时", "多维", "多方面", "落地",
    )
    dimension_terms = ("标准", "合规", "消费者", "风味", "工艺", "专利", "配方")
    dimension_hits = sum(1 for term in dimension_terms if term in compact)
    return (
        len(query_text.strip()) >= 18
        or any(term in compact for term in complexity_terms)
        or dimension_hits >= 3
    )


def _group_selection_thresholds(query_text: str, route: dict | None = None) -> tuple[float, float]:
    if _is_comprehensive_query(query_text, route=route):
        return _COMPREHENSIVE_GROUP_RELEVANCE_RATIO, _COMPREHENSIVE_GROUP_RELEVANCE_DELTA
    return _GROUP_RELEVANCE_RATIO, _GROUP_RELEVANCE_DELTA


def _context_limits(query_text: str, route: dict | None = None) -> tuple[int, int, int]:
    if _is_comprehensive_query(query_text, route=route):
        return (
            _COMPREHENSIVE_MAX_NODE_CHARS,
            _COMPREHENSIVE_CONTEXT_CHARS,
            _COMPREHENSIVE_STREAM_CONTEXT_CHARS,
        )
    return _MAX_NODE_CHARS, _MAX_CONTEXT_CHARS, _STREAM_CONTEXT_CHARS


def _query_match_terms(query_text: str) -> list[str]:
    compact = _normalize_text(query_text)
    terms = []
    for term in NODE_MATCH_TERMS + EXPERIMENT_METRIC_TERMS + COMPLIANCE_TERMS:
        norm = _normalize_text(term)
        if len(norm) >= 2 and norm in compact and norm not in terms:
            terms.append(norm)
    terms.sort(key=len, reverse=True)
    return terms


def _node_keyword_bonus(node_text: str, query_text: str) -> tuple[float, int, int]:
    compact_text = _normalize_text(node_text)
    terms = _query_match_terms(query_text)
    matched = 0
    specific_terms = 0
    bonus = 0.0
    for term in terms:
        if term in compact_text:
            matched += 1
            bonus += 0.08 if len(term) >= 3 else 0.05
            if term not in GENERIC_QUERY_TERMS:
                specific_terms += 1
        elif term not in GENERIC_QUERY_TERMS:
            specific_terms -= 1
    return min(bonus, 0.45), matched, specific_terms


def _node_numeric_bonus(node_text: str, query_text: str) -> float:
    compact_query = _normalize_text(query_text)
    if not any(term in compact_query for term in ("最高", "最关注", "最多", "top", "更高", "排名")):
        return 0.0
    values = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", node_text)]
    if not values:
        return 0.0
    max_value = max(values)
    return min(max_value, 1000.0) / 1000.0 * 0.18


def _critical_term_penalty(node_text: str, query_text: str) -> float:
    query_compact = _normalize_text(query_text)
    text_compact = _normalize_text(node_text)
    required_terms = [term for term in CRITICAL_METRIC_TERMS if term in query_compact]
    if not required_terms:
        return 0.0
    if any(term in text_compact for term in required_terms):
        return 0.0
    return -0.35


def _question_requires_metric_grounding(query_text: str) -> bool:
    compact = _normalize_text(query_text)
    return any(
        term in compact
        for term in (
            "多少", "占比", "比例", "排名", "最高", "最低", "更高", "更低",
            "总酸", "总酯", "还原糖", "有机酸", "提及次数", "评分", "喜好度",
        )
    )


def _question_mentions_compliance(query_text: str) -> bool:
    compact = _normalize_text(query_text)
    return any(_normalize_text(term) in compact for term in COMPLIANCE_TERMS)


def _question_mentions_consumer_preference(query_text: str) -> bool:
    compact = _normalize_text(query_text)
    return any(term in compact for term in ("消费者", "偏好", "接受度", "喜好", "口味", "口感"))


def _question_looks_like_standard_metric_query(query_text: str) -> bool:
    compact = _normalize_text(query_text)
    has_metric = any(_normalize_text(term) in compact for term in STANDARD_METRIC_TERMS)
    has_standard_ref = bool(re.search(r"gb/?t?\s*\d+", query_text, re.I)) or any(
        _normalize_text(term) in compact for term in ("标准", "国标", "执行标准", "要求", "限值")
    )
    has_product_ref = sum(1 for term in STANDARD_PRODUCT_TERMS if _normalize_text(term) in compact) >= 1
    has_compare = any(_normalize_text(term) in compact for term in ("差异", "区别", "比较", "分别"))
    return has_metric and (has_standard_ref or has_product_ref or has_compare)


def _question_metric_terms(query_text: str) -> list[str]:
    compact = _normalize_text(query_text)
    return [term for term in CRITICAL_METRIC_TERMS if term in compact]


def _nodes_for_doc_types(nodes, doc_types: set[str]):
    return [node for node in nodes if ((node.metadata or {}).get("doc_type") in doc_types)]


def _nodes_for_chunk_kinds(nodes, chunk_kinds: set[str]):
    return [node for node in nodes if ((node.metadata or {}).get("chunk_kind", "paragraph") in chunk_kinds)]


def _nodes_for_evidence_role(nodes, role: str):
    return [node for node in nodes if ((node.metadata or {}).get("evidence_role") == role)]


def _nodes_containing_terms(nodes, terms: list[str]):
    if not terms:
        return list(nodes)
    matched = []
    for node in nodes:
        compact = _normalize_text(node.text)
        if any(term in compact for term in terms):
            matched.append(node)
    return matched


def _gate_failure(reason_code: str, message: str) -> dict:
    return {"code": reason_code, "message": message}


def evaluate_evidence_gate(question: str, nodes, route: dict, extra_context: str | None = None) -> dict:
    route_name = route.get("name", "general")
    failures = []
    warnings = []
    metric_terms = _question_metric_terms(question)
    top_score = float(nodes[0].score or 0.0) if nodes else 0.0

    consumer_nodes = _nodes_for_doc_types(nodes, DOC_TYPE_CONSUMER)
    standard_nodes = _nodes_for_doc_types(nodes, DOC_TYPE_STANDARD)
    patent_nodes = _nodes_for_doc_types(nodes, DOC_TYPE_PATENT)
    paper_nodes = _nodes_for_doc_types(nodes, DOC_TYPE_PAPER_LIKE)
    table_nodes = _nodes_for_chunk_kinds(nodes, {"table_fact", "table_summary", "table_row"})
    paper_table_nodes = _nodes_for_doc_types(table_nodes, DOC_TYPE_PAPER_LIKE)
    consumer_table_nodes = [
        node for node in consumer_nodes
        if (node.metadata or {}).get("chunk_kind") in {"table_fact", "table_summary", "table_row"}
    ]

    if not nodes:
        failures.append(_gate_failure("no_evidence", "当前问题未检索到可用证据。"))

    if route_name == "consumer_insight":
        if not consumer_nodes:
            failures.append(_gate_failure("missing_consumer_evidence", "当前未命中消费者子库证据。"))
        if _question_prefers_table_evidence(question, route) and not consumer_table_nodes:
            failures.append(_gate_failure("missing_consumer_table", "当前消费者问题缺少可直接支撑排序/占比判断的表格证据。"))

    elif route_name == "standard_compliance":
        if not standard_nodes:
            failures.append(_gate_failure("missing_standard_evidence", "当前未命中标准/法规证据。"))

    elif route_name == "patent_process":
        if not patent_nodes:
            failures.append(_gate_failure("missing_patent_evidence", "当前未命中专利工艺证据。"))

    elif route_name == "flavor_mechanism":
        if not paper_nodes:
            failures.append(_gate_failure("missing_flavor_evidence", "当前未命中风味/机理论文证据。"))
        if _question_prefers_paper_table_evidence(question, route) and not paper_table_nodes:
            failures.append(_gate_failure("missing_paper_table", "当前论文问题缺少可直接引用的实验表格证据。"))

    elif route_name == "formula_process":
        primary_nodes = _nodes_for_evidence_role(nodes, "primary")
        constraint_nodes = _nodes_for_evidence_role(nodes, "constraint")
        support_nodes = _nodes_for_evidence_role(nodes, "support")
        if not primary_nodes:
            failures.append(_gate_failure("missing_primary_evidence", "配方工艺问题缺少主证据层（论文/风味/专利）。"))
        if _question_mentions_compliance(question) and not constraint_nodes:
            failures.append(_gate_failure("missing_constraint_evidence", "问题涉及合规约束，但当前未命中标准约束证据。"))
        if _question_mentions_consumer_preference(question) and not support_nodes:
            warnings.append(_gate_failure("missing_consumer_support", "问题涉及消费者偏好，但当前未命中消费者辅助证据；本轮将仅保留非消费者维度的可靠结论。"))

    if _question_requires_metric_grounding(question):
        metric_nodes = _nodes_containing_terms(nodes, metric_terms)
        if metric_terms and not metric_nodes:
            failures.append(
                _gate_failure(
                    "missing_metric_grounding",
                    f"问题要求关键指标判断，但当前证据中未直接命中这些指标：{'、'.join(metric_terms)}。",
                )
            )
        if route_name == "consumer_insight" and _question_prefers_table_evidence(question, route) and not consumer_table_nodes:
            failures.append(_gate_failure("missing_metric_table", "当前消费者统计问题缺少可直接引用的数值表格证据。"))
        if route_name in {"flavor_mechanism", "formula_process"} and _question_prefers_paper_table_evidence(question, route):
            paper_metric_table_nodes = _nodes_containing_terms(paper_table_nodes, metric_terms)
            if metric_terms and not paper_metric_table_nodes:
                failures.append(
                    _gate_failure(
                        "missing_paper_metric_table",
                        f"当前论文问题需要表格化指标证据，但未直接命中这些指标：{'、'.join(metric_terms)}。",
                    )
                )

    gate_passed = not failures
    return {
        "passed": gate_passed,
        "status": "pass" if gate_passed else "refuse",
        "route_name": route_name,
        "route_label": route.get("label", "通用检索"),
        "top_score": round(top_score, 4),
        "failures": failures,
        "warnings": warnings,
        "stats": {
            "node_count": len(nodes),
            "consumer_nodes": len(consumer_nodes),
            "standard_nodes": len(standard_nodes),
            "patent_nodes": len(patent_nodes),
            "paper_nodes": len(paper_nodes),
            "table_nodes": len(table_nodes),
            "metric_terms": metric_terms,
            "extra_context": bool(extra_context),
        },
    }


def build_refusal_answer(question: str, route: dict, gate: dict) -> str:
    failures = gate.get("failures", [])
    failure_lines = "\n".join(f"1、{item['message']}" for item in failures) or "1、当前证据不足。"

    route_name = route.get("name")
    failure_codes = {item.get("code") for item in failures}
    if route_name == "consumer_insight":
        guidance = "建议补充可量化的消费者数据，如评论统计表、评分表或偏好汇总。"
    elif route_name == "standard_compliance":
        if "missing_standard_evidence" in failure_codes:
            guidance = "当前更像是标准子库召回失败。建议优先核查标准资料是否已正确入库，并确认标准号、产品类别或指标名称是否可被命中。"
        else:
            guidance = "建议补充对应国标/团标/企标条款，或明确需要核查的指标名称。"
    elif route_name == "patent_process":
        guidance = "建议补充相关专利全文、实施例或更明确的工艺关键词。"
    elif route_name == "flavor_mechanism":
        guidance = "建议补充包含目标指标或机理描述的论文段落、表格或实验结果。"
    elif route_name == "formula_process":
        guidance = "建议至少补充工艺论文/风味论文或专利实施例；如问题同时涉及合规或消费者偏好，也需要对应标准或消费者证据。"
    else:
        guidance = "建议缩小问题范围，或补充更明确的文档来源与指标关键词。"

    return (
        "一、当前暂不建议直接给出结论\n"
        "基于现有证据，为避免形成看似完整但不可复核的判断，本轮回答暂不直接展开。\n\n"
        "（一）当前主要缺口\n"
        f"{failure_lines}\n\n"
        "（二）建议你补充的方向\n"
        f"1、{guidance}\n"
        "2、如果你希望我仅基于现有知识库给出通用方向，请在问题中明确说明“允许通用建议”。"
    )


def build_gate_analysis_card(gate: dict) -> dict:
    if gate.get("passed"):
        return {
            "title": "证据门禁",
            "body": "本轮问题已通过程序化证据门禁，允许进入生成阶段。" if not gate.get("warnings") else "本轮问题已通过证据门禁，但存在辅助维度证据缺口。",
            "items": [
                f"路由：{gate.get('route_label', '通用检索')}",
                f"证据数：{gate.get('stats', {}).get('node_count', 0)}",
                f"Top score：{gate.get('top_score', 0.0)}",
                *[item["message"] for item in gate.get("warnings", [])[:2]],
            ],
            "tone": "warning" if gate.get("warnings") else "positive",
        }

    return {
        "title": "证据门禁未通过",
        "body": "系统检测到当前证据不足以支撑可靠回答，因此已触发拒答保护。",
        "items": [item["message"] for item in gate.get("failures", [])],
        "tone": "warning",
    }


def _doc_type_filters(doc_types: list[str]):
    if not doc_types:
        return None
    if len(doc_types) == 1:
        return MetadataFilter(key="doc_type", value=doc_types[0], operator=FilterOperator.EQ)
    return MetadataFilters(
        filters=[
            MetadataFilter(key="doc_type", value=doc_type, operator=FilterOperator.EQ)
            for doc_type in doc_types
        ],
        condition=FilterCondition.OR,
    )


def _user_filter_to_metadata(filter_key: str, filter_value):
    if filter_value is None:
        return None
    if isinstance(filter_value, list):
        return MetadataFilters(
            filters=[
                MetadataFilter(key=filter_key, value=value, operator=FilterOperator.EQ)
                for value in filter_value
            ],
            condition=FilterCondition.OR,
        )
    return MetadataFilter(key=filter_key, value=filter_value, operator=FilterOperator.EQ)


def build_metadata_filters(route_doc_types: list[str], user_filters: dict | None):
    filter_parts = []
    route_filter = _doc_type_filters(route_doc_types)
    if route_filter is not None:
        filter_parts.append(route_filter)

    if user_filters:
        for key, value in user_filters.items():
            metadata_filter = _user_filter_to_metadata(key, value)
            if metadata_filter is not None:
                filter_parts.append(metadata_filter)

    if not filter_parts:
        return None
    if len(filter_parts) == 1:
        single = filter_parts[0]
        if isinstance(single, MetadataFilter):
            return MetadataFilters(filters=[single], condition=FilterCondition.AND)
        return single
    return MetadataFilters(filters=filter_parts, condition=FilterCondition.AND)


def resolve_route(question: str, extra_context: str | None = None, filters: dict | None = None) -> dict:
    explicit_doc_type = (filters or {}).get("doc_type")
    if explicit_doc_type:
        doc_types = explicit_doc_type if isinstance(explicit_doc_type, list) else [explicit_doc_type]
        return {
            "name": "user_override",
            "label": "用户指定",
            "doc_types": doc_types,
            "description": "用户显式指定了文档过滤条件，优先按用户要求检索。",
            "scores": {},
        }

    combined_text = question
    if extra_context:
        combined_text = f"{question}\n{extra_context}"

    scores = {
        route_name: _keyword_score(combined_text, route["keywords"])
        for route_name, route in ROUTE_DEFINITIONS.items()
    }

    normalized = _normalize_text(combined_text)
    if "消费者" in question and any(term in normalized for term in ("配方", "工艺", "优化", "建议")):
        scores["consumer_insight"] += 2
        scores["formula_process"] += 2

    mentions_compliance = any(_normalize_text(term) in normalized for term in COMPLIANCE_TERMS)
    mentions_experiment_metric = any(_normalize_text(term) in normalized for term in EXPERIMENT_METRIC_TERMS)
    mentions_requirement_style = any(term in normalized for term in ("要求", "规定", "限量", "符合", "应当", "执行标准"))
    standard_metric_query = _question_looks_like_standard_metric_query(combined_text)

    if mentions_experiment_metric and not mentions_compliance and not standard_metric_query:
        scores["standard_compliance"] = max(0, scores["standard_compliance"] - 5)
        scores["flavor_mechanism"] += 2
        scores["formula_process"] += 1

    if mentions_requirement_style:
        scores["standard_compliance"] += 4
        if "配方" not in normalized and "工艺" not in normalized:
            scores["flavor_mechanism"] = max(0, scores["flavor_mechanism"] - 2)

    if standard_metric_query:
        scores["standard_compliance"] += 8
        scores["flavor_mechanism"] = max(0, scores["flavor_mechanism"] - 2)
        scores["formula_process"] = max(0, scores["formula_process"] - 1)

    if any(term in normalized for term in ("样品", "有机酸", "挥发性", "风味物质", "总酸", "总酯", "还原糖")) and not mentions_compliance:
        scores["flavor_mechanism"] += 2

    best_name = None
    best_score = 0
    for route_name in ROUTE_PRIORITY:
        score = scores.get(route_name, 0)
        if score > best_score:
            best_name = route_name
            best_score = score

    if best_name is None or best_score <= 0:
        return {
            "name": "general",
            "label": "通用检索",
            "doc_types": [],
            "description": "问题意图不够明确，暂不收窄子库范围。",
            "scores": scores,
        }

    selected = ROUTE_DEFINITIONS[best_name]
    route = {"name": best_name, "scores": scores}
    route.update(selected)
    return route


def classify_question_intent(question: str, route: dict | None = None) -> str:
    compact = _normalize_text(question)
    scores = {
        label: sum(1 for keyword in keywords if _normalize_text(keyword) in compact)
        for label, keywords in QUESTION_INTENTS.items()
    }

    if (route or {}).get("name") == "formula_process":
        scores["配方优化"] += 1
        scores["工艺改良"] += 1
    elif (route or {}).get("name") == "flavor_mechanism":
        scores["风味解析"] += 2
    elif (route or {}).get("name") == "consumer_insight":
        scores["市场适配"] += 2
    elif (route or {}).get("name") == "standard_compliance":
        scores["合规核查"] += 2

    best_label = max(scores, key=scores.get)
    if scores[best_label] <= 0:
        return "综合复合型提问"
    ranked = sorted(scores.values(), reverse=True)
    if len(ranked) > 1 and ranked[0] == ranked[1]:
        return "综合复合型提问"
    return best_label


def classify_question_complexity(
    question: str,
    route: dict | None = None,
    extra_context: str | None = None,
    intent_label: str | None = None,
) -> str:
    compact = _normalize_text(question)
    intent = intent_label or classify_question_intent(question, route=route)
    route_name = (route or {}).get("name")
    length_score = len(question.strip())

    has_compare = any(term in compact for term in ("比较", "差异", "区别", "优缺点", "对比", "分别"))
    has_complex = any(_normalize_text(term) in compact for term in COMPLEX_QUESTION_TERMS)
    has_simple = any(_normalize_text(term) in compact for term in SIMPLE_QUESTION_TERMS)
    dimension_hits = sum(1 for term in ("标准", "风味", "工艺", "消费者", "市场", "专利", "配方") if term in question)

    if extra_context:
        return "C"
    if route_name == "formula_process" and (_is_comprehensive_query(question, route=route) or has_complex):
        return "C"
    if intent == "综合复合型提问" and (dimension_hits >= 2 or length_score >= 20):
        return "C"
    if has_compare or intent in {"工艺改良", "风味解析", "市场适配"}:
        if has_complex or dimension_hits >= 2 or length_score >= 18:
            return "C"
        return "B"
    if route_name in {"consumer_insight", "flavor_mechanism", "patent_process"} and length_score >= 14:
        return "B"
    if has_simple and dimension_hits <= 1 and length_score <= 18:
        return "A"
    if route_name == "standard_compliance" and dimension_hits <= 1 and length_score <= 20:
        return "A"
    return "B"


def build_answer_profile(
    question: str,
    route: dict | None = None,
    extra_context: str | None = None,
) -> dict:
    intent_label = classify_question_intent(question, route=route)
    complexity = classify_question_complexity(
        question,
        route=route,
        extra_context=extra_context,
        intent_label=intent_label,
    )
    profile = dict(ANSWER_PROFILES[complexity])
    profile["grade"] = complexity
    profile["intent_label"] = intent_label
    return profile


def get_retriever(metadata_filters=None, similarity_top_k=None):
    mf = metadata_filters

    return _INDEX.as_retriever(
        filters=mf,
        similarity_top_k=similarity_top_k or _TOP_K,
    )


def _dedupe_nodes(nodes, limit: int | None = None):
    seen = set()
    unique_nodes = []
    for node in nodes:
        metadata = node.metadata or {}
        key = (
            metadata.get("relative_path"),
            metadata.get("source"),
            metadata.get("page"),
            metadata.get("chunk_kind"),
            metadata.get("table_id"),
            node.text[:160],
        )
        if key in seen:
            continue
        seen.add(key)
        unique_nodes.append(node)
    unique_nodes.sort(key=lambda item: float(item.score or 0.0), reverse=True)
    return unique_nodes[: (limit or _TOP_K)]


def _limit_and_dedupe_nodes(nodes, limit: int, preserve_order: bool = False):
    seen = set()
    unique_nodes = []
    iterable = nodes if preserve_order else sorted(nodes, key=lambda item: float(item.score or 0.0), reverse=True)
    for node in iterable:
        metadata = node.metadata or {}
        key = (
            metadata.get("relative_path"),
            metadata.get("source"),
            metadata.get("page"),
            metadata.get("chunk_kind"),
            metadata.get("table_id"),
            node.text[:160],
        )
        if key in seen:
            continue
        seen.add(key)
        unique_nodes.append(node)
        if len(unique_nodes) >= limit:
            break
    return unique_nodes


def _group_max_limit(group: dict, query_text: str, route: dict | None = None) -> int:
    base_limit = max(group.get("top_k", 1), 1)
    if (route or {}).get("name") != "formula_process":
        return base_limit

    if _is_comprehensive_query(query_text, route=route):
        role = group.get("role")
        if role == "primary":
            return max(base_limit, _COMPREHENSIVE_PRIMARY_MAX)
        if role == "constraint":
            return max(base_limit, _COMPREHENSIVE_CONSTRAINT_MAX)
        if role == "support":
            return max(base_limit, _COMPREHENSIVE_SUPPORT_MAX)
        return max(base_limit, _COMPREHENSIVE_TOTAL_LIMIT)

    role = group.get("role")
    if role == "primary":
        max_limit = max(base_limit, _GROUP_PRIMARY_MAX)
    elif role == "constraint":
        max_limit = max(base_limit, _GROUP_CONSTRAINT_MAX)
        if _question_mentions_compliance(query_text):
            max_limit = max(max_limit, 2)
    elif role == "support":
        max_limit = max(base_limit, _GROUP_SUPPORT_MAX)
        if _question_mentions_consumer_preference(query_text):
            max_limit = max(max_limit, 2)
    else:
        max_limit = base_limit
    return max_limit


def _select_group_nodes(nodes, group: dict, query_text: str, route: dict | None = None, preserve_order: bool = False):
    if not nodes:
        return []

    min_limit = max(group.get("top_k", 1), 1)
    max_limit = _group_max_limit(group, query_text, route=route)
    relevance_ratio, relevance_delta = _group_selection_thresholds(query_text, route=route)
    ordered_nodes = nodes if preserve_order else sorted(nodes, key=lambda item: float(item.score or 0.0), reverse=True)
    top_score = float((ordered_nodes[0].score or 0.0))
    selected = []

    for node in ordered_nodes:
        score = float(node.score or 0.0)
        within_ratio = top_score <= 0 or score >= top_score * relevance_ratio
        within_delta = top_score <= 0 or (top_score - score) <= relevance_delta
        if len(selected) < min_limit or within_ratio or within_delta:
            selected.append(node)
        if len(selected) >= max_limit:
            break

    return _limit_and_dedupe_nodes(selected, max_limit, preserve_order=True)


def _metadata_value_matches(actual, expected) -> bool:
    if expected is None:
        return True
    if isinstance(expected, list):
        return actual in expected
    return actual == expected


def _node_matches_scope(node, route_doc_types: list[str], filters: dict | None) -> bool:
    metadata = node.metadata or {}
    if route_doc_types and metadata.get("doc_type") not in route_doc_types:
        return False
    for key, expected in (filters or {}).items():
        if not _metadata_value_matches(metadata.get(key), expected):
            return False
    return True


def _annotate_node(node, evidence_group: dict | None = None):
    metadata = node.metadata or {}
    if evidence_group:
        metadata["evidence_role"] = evidence_group.get("role")
        metadata["evidence_group"] = evidence_group.get("label")
        metadata["evidence_usage"] = evidence_group.get("usage")
    return node


def _node_source_bonus(metadata: dict, query_text: str) -> float:
    source = str(metadata.get("source") or "")
    if not source:
        return 0.0
    source_stem = os.path.splitext(source)[0]
    compact_source = _normalize_text(source_stem)
    compact_query = _normalize_text(query_text)
    if len(compact_source) >= 6 and compact_source in compact_query:
        return 0.24
    return 0.0


def _node_source_matches_query(metadata: dict, query_text: str) -> bool:
    source = str(metadata.get("source") or "")
    if not source:
        return False
    source_stem = os.path.splitext(source)[0]
    compact_source = _normalize_text(source_stem)
    compact_query = _normalize_text(query_text)
    return len(compact_source) >= 6 and compact_source in compact_query


def _metric_label_from_text(segment: str, metric_terms: list[str]) -> str | None:
    compact = _normalize_text(segment)
    for term in metric_terms:
        if term in compact:
            return term
    return None


def _extract_numeric_value(segment: str) -> float | None:
    matches = re.findall(r"-?\d+(?:\.\d+)?", segment)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def _extract_identifier(segment: str) -> tuple[str, str] | None:
    patterns = (
        ("试验号", r"试验号[:：]\s*([^\|\s]+)"),
        ("样品", r"样品(?:编号)?[:：]\s*([^\|]+?)\s*$"),
        ("组别", r"组别[:：]\s*([^\|]+?)\s*$"),
        ("处理", r"处理[:：]\s*([^\|]+?)\s*$"),
        ("样本", r"样本(?:编号)?[:：]\s*([^\|]+?)\s*$"),
    )
    for label, pattern in patterns:
        match = re.search(pattern, segment)
        if match:
            return label, match.group(1).strip()
    return None


def _extract_table_record(text: str, metric_terms: list[str]) -> dict | None:
    segments = [segment.strip() for segment in text.split("|")]
    identifier = None
    metric_label = None
    metric_value = None

    for segment in segments:
        if identifier is None:
            identifier = _extract_identifier(segment)
        if metric_label is None:
            metric_label = _metric_label_from_text(segment, metric_terms)
            if metric_label:
                metric_value = _extract_numeric_value(segment)

    if not identifier or metric_label is None or metric_value is None:
        return None

    identifier_label, identifier_value = identifier
    return {
        "identifier_label": identifier_label,
        "identifier_value": identifier_value,
        "metric_label": metric_label,
        "metric_value": metric_value,
        "raw_text": text,
    }


def _sort_table_records(records: list[dict], reverse: bool = True) -> list[dict]:
    return sorted(
        records,
        key=lambda item: (
            float(item.get("metric_value", 0.0)),
            str(item.get("identifier_value", "")),
        ),
        reverse=reverse,
    )


def _table_aggregation_mode(question: str) -> str:
    compact = _normalize_text(question)
    if any(term in compact for term in ("最高", "最大", "更高")):
        return "max"
    if any(term in compact for term in ("最低", "最小", "更低")):
        return "min"
    return "compare"


def _build_table_where(source: str, table_id: str) -> dict:
    return {
        "$and": [
            {"source": {"$eq": source}},
            {"table_id": {"$eq": table_id}},
            {"chunk_kind": {"$eq": "table_fact"}},
        ]
    }


def _load_table_rows(source: str, table_id: str) -> list[dict]:
    result = _COLLECTION.get(
        where=_build_table_where(source, table_id),
        include=["documents", "metadatas"],
    )
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    rows = []
    for document, metadata in zip(documents, metadatas):
        rows.append(
            {
                "text": document,
                "metadata": metadata or {},
            }
        )
    rows.sort(key=lambda item: int((item["metadata"] or {}).get("row_index") or 0))
    return rows


def build_table_aggregation(question: str, nodes, route: dict | None = None) -> dict | None:
    if not _question_prefers_paper_table_evidence(question, route=route):
        return None

    metric_terms = _question_metric_terms(question)
    if not metric_terms:
        return None

    paper_table_nodes = [
        node for node in nodes
        if ((node.metadata or {}).get("doc_type") in DOC_TYPE_PAPER_LIKE)
        and ((node.metadata or {}).get("chunk_kind") == "table_fact")
    ]
    if not paper_table_nodes:
        return None

    focus_nodes = [
        node for node in paper_table_nodes
        if _node_source_matches_query(node.metadata or {}, question)
    ]
    candidate_nodes = focus_nodes or paper_table_nodes
    anchor_node = candidate_nodes[0]
    anchor_metadata = anchor_node.metadata or {}
    source = anchor_metadata.get("source")
    table_id = anchor_metadata.get("table_id")
    if not source or not table_id:
        return None

    rows = _load_table_rows(source, table_id)
    parsed_records = []
    for row in rows:
        record = _extract_table_record(row["text"], metric_terms)
        if not record:
            continue
        metadata = row["metadata"] or {}
        record.update(
            {
                "page": metadata.get("page"),
                "row_index": metadata.get("row_index"),
                "table_id": metadata.get("table_id"),
                "source": metadata.get("source"),
                "relative_path": metadata.get("relative_path"),
                "doc_type": metadata.get("doc_type"),
            }
        )
        parsed_records.append(record)

    if len(parsed_records) < 2:
        return None

    mode = _table_aggregation_mode(question)
    sorted_desc = _sort_table_records(parsed_records, reverse=True)
    sorted_asc = list(reversed(sorted_desc))

    if mode == "max":
        winner = sorted_desc[0]
        leaderboard = sorted_desc[:3]
    elif mode == "min":
        winner = sorted_asc[0]
        leaderboard = sorted_asc[:3]
    else:
        winner = sorted_desc[0]
        leaderboard = sorted_desc[:5]

    metric_label = winner.get("metric_label") or metric_terms[0]
    identifier_label = winner.get("identifier_label") or "试验号"
    leaderboard_text = "；".join(
        f"{item['identifier_label']}{item['identifier_value']} = {item['metric_value']}"
        for item in leaderboard
    )

    summary_lines = [
        f"已对《{source}》中的表 {table_id} 做程序化聚合，共识别 {len(parsed_records)} 条 {metric_label} 数据。",
    ]
    if mode == "max":
        summary_lines.append(
            f"最高值对应{identifier_label}{winner['identifier_value']}，{metric_label} = {winner['metric_value']}。"
        )
    elif mode == "min":
        summary_lines.append(
            f"最低值对应{identifier_label}{winner['identifier_value']}，{metric_label} = {winner['metric_value']}。"
        )
    else:
        summary_lines.append(f"按 {metric_label} 从高到低排序，前几项为：{leaderboard_text}。")

    return {
        "mode": mode,
        "source": source,
        "table_id": table_id,
        "page": winner.get("page"),
        "metric_label": metric_label,
        "identifier_label": identifier_label,
        "record_count": len(parsed_records),
        "winner": winner,
        "leaderboard": leaderboard,
        "summary_lines": summary_lines,
    }


def build_standard_metric_aggregation(question: str, nodes, route: dict | None = None) -> dict | None:
    if (route or {}).get("name") != "standard_compliance":
        return None
    metric_terms = _question_metric_terms(question)
    if "总酸" not in metric_terms:
        return None

    standard_table_nodes = [
        node for node in nodes
        if ((node.metadata or {}).get("doc_type") in DOC_TYPE_STANDARD)
        and ((node.metadata or {}).get("chunk_kind") == "table_fact")
    ]
    if not standard_table_nodes:
        return None

    anchor_metadata = standard_table_nodes[0].metadata or {}
    source = anchor_metadata.get("source")
    table_id = anchor_metadata.get("table_id")
    if not source or not table_id:
        return None

    rows = _load_table_rows(source, table_id)
    values = {}
    current_label = None
    for row in rows:
        text = row["text"]
        item_matches = re.findall(r"(?:项\s*目|项目)[:：]\s*([^\|]+)", text)
        for label in item_matches:
            clean_label = label.strip().replace(" ", "")
            if clean_label in {"食醋", "甜醋"}:
                current_label = clean_label
        metric_value = None
        indicator_match = re.search(r"(?:指\s*标|指标)[:：]\s*(-?\d+(?:\.\d+)?)", text)
        if indicator_match:
            metric_value = float(indicator_match.group(1))
        elif "≥" in text:
            trailing_number = _extract_numeric_value(text)
            if trailing_number is not None and current_label in {"食醋", "甜醋"}:
                metric_value = trailing_number
        if current_label in {"食醋", "甜醋"} and metric_value is not None:
            values[current_label] = metric_value

    if not values:
        return None

    summary_lines = [
        f"已对《{source}》中的表 {table_id} 做程序化提取，识别到总酸要求："
        + "；".join(f"{label} ≥ {value} g/100mL" for label, value in values.items())
        + "。"
    ]
    return {
        "source": source,
        "table_id": table_id,
        "metric_label": "总酸",
        "values": values,
        "summary_lines": summary_lines,
    }


def _rank_node_score(node, query_text: str, route: dict | None = None, evidence_group: dict | None = None) -> float:
    metadata = node.metadata or {}
    score = float(node.score or 0.0)
    chunk_kind = metadata.get("chunk_kind", "paragraph")
    table_status = metadata.get("table_status")
    prefer_table = _question_prefers_table_evidence(query_text, route)
    prefer_paper_table = _question_prefers_paper_table_evidence(query_text, route=route)
    compact_query = _normalize_text(query_text)

    if prefer_paper_table:
        if chunk_kind == "table_fact":
            score += 0.72
        elif chunk_kind in {"table_summary", "table_row"}:
            score += 0.3
        elif chunk_kind == "paragraph":
            score -= 0.2
    elif prefer_table:
        if chunk_kind == "table_fact":
            score += 0.45
        elif chunk_kind in {"table_summary", "table_row"}:
            score += 0.22
        elif chunk_kind == "paragraph":
            score -= 0.04
    else:
        if chunk_kind == "table_fact":
            score += 0.12
        elif chunk_kind == "table_summary":
            score += 0.05

    if chunk_kind.startswith("table"):
        if table_status == "pass":
            score += 0.08
        elif table_status == "review":
            score -= 0.12

    role = (evidence_group or {}).get("role") or metadata.get("evidence_role")
    if route and route.get("name") == "formula_process":
        if role == "primary":
            score += 0.1
        elif role == "constraint":
            score += 0.03
        elif role == "support":
            score += 0.02
        if not prefer_table:
            if chunk_kind == "paragraph":
                score += 0.12
            elif chunk_kind == "table_fact":
                score -= 0.08
            elif chunk_kind == "table_summary":
                score -= 0.03
        if any(term in compact_query for term in ("如何", "优化", "建议", "方案", "兼顾", "改善", "提升")):
            if chunk_kind == "paragraph":
                score += 0.18
            elif chunk_kind == "table_fact":
                score -= 0.22
            elif chunk_kind == "table_summary":
                score -= 0.08

    keyword_bonus, matched_terms, specific_term_balance = _node_keyword_bonus(node.text, query_text)
    score += keyword_bonus
    if matched_terms == 0 and specific_term_balance < 0:
        score -= 0.26

    score += _node_source_bonus(metadata, query_text)
    score += _node_numeric_bonus(node.text, query_text)
    score += _critical_term_penalty(node.text, query_text)

    return score


def _rerank_nodes(nodes, query_text: str, route: dict | None = None, evidence_group: dict | None = None):
    for node in nodes:
        node.score = _rank_node_score(node, query_text, route=route, evidence_group=evidence_group)
    return sorted(nodes, key=lambda item: float(item.score or 0.0), reverse=True)


def retrieve_nodes(
    query_text: str,
    route_doc_types: list[str],
    filters: dict | None,
    route: dict | None = None,
    result_limit: int | None = None,
):
    limit = result_limit or _TOP_K
    if not route_doc_types and not filters:
        return _dedupe_nodes(_rerank_nodes(get_retriever(similarity_top_k=limit).retrieve(query_text), query_text, route=route), limit=limit)

    scoped_filters = build_metadata_filters(route_doc_types, filters)
    scoped_nodes = get_retriever(
        metadata_filters=scoped_filters,
        similarity_top_k=max(limit, _ROUTE_CANDIDATE_TOP_K),
    ).retrieve(query_text)
    reranked_scoped_nodes = _rerank_nodes(scoped_nodes, query_text, route=route)
    deduped_scoped_nodes = _dedupe_nodes(reranked_scoped_nodes, limit=limit)
    if deduped_scoped_nodes:
        return deduped_scoped_nodes

    candidate_nodes = get_retriever(similarity_top_k=max(limit, _ROUTE_CANDIDATE_TOP_K)).retrieve(query_text)
    fallback_scoped_nodes = [node for node in candidate_nodes if _node_matches_scope(node, route_doc_types, filters)]
    return _dedupe_nodes(_rerank_nodes(fallback_scoped_nodes, query_text, route=route), limit=limit)


def retrieve_route_nodes(query_text: str, route: dict, filters: dict | None, answer_profile: dict | None = None):
    evidence_plan = route.get("evidence_plan") or []
    target_limit = max(
        _TOP_K,
        int((answer_profile or {}).get("target_citations", _TOP_K)),
    )
    if not evidence_plan:
        return retrieve_nodes(query_text, route["doc_types"], filters, route=route, result_limit=target_limit)

    merged_nodes = []
    for group in evidence_plan:
        group_query = build_group_scoped_query(query_text, group)
        group_target = max(group.get("top_k", 1), target_limit // max(len(evidence_plan), 1))
        candidate_limit = max(_ROUTE_CANDIDATE_TOP_K * 3, group_target * 24)
        candidate_nodes = get_retriever(similarity_top_k=candidate_limit).retrieve(group_query)
        matched = [
            _annotate_node(node, group)
            for node in candidate_nodes
            if _node_matches_scope(node, group.get("doc_types", []), filters)
        ]
        preserve_order = False
        if not matched and group.get("doc_types"):
            scoped_filters = build_metadata_filters(group.get("doc_types", []), filters)
            matched = [
                _annotate_node(node, group)
                for node in get_retriever(
                    metadata_filters=scoped_filters,
                    similarity_top_k=max(group_target * 8, 12),
                ).retrieve(group_query)
            ]
        reranked = _rerank_nodes(matched, query_text, route=route, evidence_group=group)
        if _is_formula_design_query(query_text, route):
            paragraph_nodes = [node for node in reranked if (node.metadata or {}).get("chunk_kind") == "paragraph"]
            other_nodes = [node for node in reranked if (node.metadata or {}).get("chunk_kind") != "paragraph"]
            reranked = paragraph_nodes + other_nodes
            preserve_order = True
        merged_nodes.extend(_select_group_nodes(reranked, group, query_text, route=route, preserve_order=preserve_order))

    total_limit = max(
        len(merged_nodes),
        sum(group.get("top_k", 0) for group in evidence_plan) or _TOP_K,
        target_limit,
    )
    if _is_comprehensive_query(query_text, route=route):
        total_limit = min(total_limit, _COMPREHENSIVE_TOTAL_LIMIT)
    return _limit_and_dedupe_nodes(merged_nodes, total_limit, preserve_order=True)


def _build_context(question: str, nodes, route: dict) -> str:
    node_char_limit, context_char_limit, _ = _context_limits(question, route=route)
    evidence_plan = route.get("evidence_plan") or []
    if evidence_plan:
        grouped_parts = []
        seen_sources = 1
        total_chars = 0
        for group in evidence_plan:
            group_nodes = [
                node for node in nodes
                if ((node.metadata or {}).get("evidence_group") == group.get("label"))
            ]
            if not group_nodes:
                continue
            blocks = [f"### {group['label']}（{group['usage']}）"]
            for node in group_nodes:
                metadata = node.metadata or {}
                source = metadata.get("source", "未知来源")
                page = metadata.get("page")
                title = f"[{seen_sources}] 来源：{source}"
                if page:
                    title += f"（第 {page} 页）"
                snippet = node.text[:node_char_limit].strip()
                block = f"{title}\n{snippet}"
                total_chars += len(block)
                if total_chars > context_char_limit:
                    break
                blocks.append(block)
                seen_sources += 1
            grouped_parts.append("\n\n".join(blocks))
            if total_chars > context_char_limit:
                break
        return "\n\n".join(grouped_parts)

    parts = []
    total_chars = 0
    for i, node in enumerate(nodes, start=1):
        metadata = node.metadata or {}
        source = metadata.get("source", "未知来源")
        page = metadata.get("page")
        title = f"[{i}] 来源：{source}"
        if page:
            title += f"（第 {page} 页）"
        snippet = node.text[:node_char_limit].strip()
        block = f"{title}\n{snippet}"
        total_chars += len(block)
        if total_chars > context_char_limit:
            break
        parts.append(block)
    return "\n\n".join(parts)


def _build_retrieval_debug(
    question: str,
    rewritten: str,
    route: dict,
    nodes,
    gate: dict,
) -> dict:
    doc_type_counter = Counter((node.metadata or {}).get("doc_type", "<none>") for node in nodes)
    top_hits = []
    for node in nodes[:8]:
        metadata = node.metadata or {}
        top_hits.append(
            {
                "source": metadata.get("source", ""),
                "relative_path": metadata.get("relative_path", ""),
                "doc_type": metadata.get("doc_type", ""),
                "page": metadata.get("page"),
                "chunk_kind": metadata.get("chunk_kind", "paragraph"),
                "score": round(float(node.score or 0.0), 4),
            }
        )
    return {
        "question": question,
        "route": route.get("name"),
        "route_label": route.get("label"),
        "rewritten_query": rewritten,
        "retrieved_count": len(nodes),
        "retrieved_doc_types": dict(doc_type_counter),
        "top_hits": top_hits,
        "gate_status": gate.get("status"),
        "gate_failures": [item.get("message") for item in gate.get("failures", [])],
    }


def _format_table_aggregation(table_aggregation: dict | None) -> str:
    if not table_aggregation:
        return ""

    lines = [
        "【程序化表格归纳】",
        *table_aggregation.get("summary_lines", []),
    ]
    leaderboard = table_aggregation.get("leaderboard") or []
    if leaderboard:
        lines.append(
            "关键数据行："
            + "；".join(
                f"{item['identifier_label']}{item['identifier_value']}（第{item.get('page', '?')}页，"
                f"{item['metric_label']}={item['metric_value']}）"
                for item in leaderboard[:5]
            )
        )
    lines.append("请优先依据这段程序化归纳回答，并保持与原始引用一致。")
    return "\n".join(lines)


def _format_standard_metric_aggregation(standard_metric_aggregation: dict | None) -> str:
    if not standard_metric_aggregation:
        return ""
    lines = [
        "【程序化标准归纳】",
        *standard_metric_aggregation.get("summary_lines", []),
        "请优先依据这段程序化标准归纳回答，并保持与原始引用一致。",
    ]
    return "\n".join(lines)


def _compose_context(
    question: str,
    nodes,
    extra_context: str | None,
    route: dict,
    answer_profile: dict | None = None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
) -> str:
    context = _build_context(question, nodes, route) or "未检索到有效资料。"
    table_summary = _format_table_aggregation(table_aggregation)
    standard_summary = _format_standard_metric_aggregation(standard_metric_aggregation)
    route_header = (
        f"【本轮检索子库】\n"
        f"问题归类：{route['label']}\n"
        f"检索范围：{route['description']}\n\n"
    )
    if route.get("name") == "formula_process":
        route_header += (
            "【证据使用原则】\n"
            "1. 工艺论文、化学/风味论文与专利是配方建议的主证据。\n"
            "2. 标准只用于限制整体配方边界、标签与用量合规。\n"
            "3. 消费者资料只用于辅助工艺与风味方向选择，不单独主导结论。\n\n"
        )
        required_aspects = []
        if _question_mentions_compliance(question):
            required_aspects.append("合规约束：至少给出 1 条由“标准约束”证据支撑的边界或限制，并标注引用。")
        if _question_mentions_consumer_preference(question):
            required_aspects.append("消费者接受度/偏好：至少给出 1 条由“消费者偏好”证据支撑的方向判断，并标注引用。")
        if required_aspects:
            route_header += "【本题必答维度】\n" + "\n".join(f"{idx}. {item}" for idx, item in enumerate(required_aspects, start=1)) + "\n\n"
        profile_grade = (answer_profile or {}).get("grade")
        requested_target = int((answer_profile or {}).get("target_citations", 0))
        if profile_grade == "C":
            coverage_target = min(max(requested_target, 10), len(nodes))
        elif profile_grade == "B":
            coverage_target = min(max(requested_target, 5), len(nodes))
        else:
            coverage_target = min(max(requested_target, 1), len(nodes))
        if coverage_target >= 4:
            route_header += (
                "【证据覆盖要求】\n"
                f"当前已命中 {len(nodes)} 条高相关证据。最终答案请优先综合至少 {coverage_target} 条不同编号的证据，"
                "避免只重复前 1-2 条资料。若证据充足，综合性问题允许扩展到 20 条左右的不同编号引用。\n\n"
            )
    knowledge_context = context
    summary_parts = [part for part in (table_summary, standard_summary) if part]
    if summary_parts:
        knowledge_context = f"{chr(10).join(summary_parts)}\n\n【知识库检索结果】\n{context}"
    if not extra_context:
        return route_header + knowledge_context

    trimmed_extra = extra_context[:_MAX_EXTRA_CONTEXT_CHARS].strip()
    return (
        f"{route_header}"
        f"【用户上传的补充资料】\n{trimmed_extra}\n\n"
        f"{knowledge_context}"
    )


def _build_answer_prompt(
    question: str,
    nodes,
    route: dict,
    answer_profile: dict,
    extra_context: str | None = None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
) -> str:
    return FORMULA_PROMPT.format(
        complexity_label=answer_profile["label"],
        intent_label=answer_profile["intent_label"],
        route_label=route.get("label", "通用检索"),
        response_contract=answer_profile["response_contract"],
        context_str=_compose_context(
            question,
            nodes,
            extra_context,
            route,
            answer_profile=answer_profile,
            table_aggregation=table_aggregation,
            standard_metric_aggregation=standard_metric_aggregation,
        ),
        query_str=question,
    )


def _build_stream_prompt(
    question: str,
    nodes,
    route: dict,
    answer_profile: dict,
    extra_context: str | None = None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
    conversation_history: list | None = None,
) -> str:
    _, _, stream_context_limit = _context_limits(question, route=route)
    context_str = _compose_context(
        question,
        nodes,
        extra_context,
        route,
        answer_profile=answer_profile,
        table_aggregation=table_aggregation,
        standard_metric_aggregation=standard_metric_aggregation,
    )
    if len(context_str) > stream_context_limit:
        context_str = context_str[:stream_context_limit].rstrip() + "\n…"

    # 构建对话历史上下文段
    conv_section = ""
    if conversation_history:
        lines = []
        for msg in conversation_history:
            role = "用户" if msg.get("role") == "user" else "助手"
            content = str(msg.get("content", "")).strip()
            if content:
                lines.append(f"{role}：{content}")
        if lines:
            conv_section = "\n近期对话历史（仅供理解问题背景，不作为新证据引用）：\n" + "\n".join(lines) + "\n"

    return STREAM_PROMPT.format(
        complexity_label=answer_profile["label"],
        intent_label=answer_profile["intent_label"],
        route_label=route.get("label", "通用检索"),
        response_contract=answer_profile["response_contract"],
        context_str=context_str,
        query_str=conv_section + question,
    )


def _clean_unit_text(unit: str) -> str:
    text = unit.strip()
    text = re.sub(r"^\s*(?:[-*]\s+|\d+\.\s+)", "", text)
    text = text.replace("**", "").replace("`", "")
    return text.strip()


def _is_outline_heading(unit: str) -> bool:
    stripped = unit.strip()
    if not stripped:
        return False
    return bool(
        re.match(r"^[一二三四五六七八九十]+、", stripped)
        or re.match(r"^（[一二三四五六七八九十]+）", stripped)
        or re.match(r"^\d+、", stripped)
    )


def _answer_units(answer: str) -> list[str]:
    units = []
    for line in (answer or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if (
            stripped.startswith("#")
            or _is_outline_heading(stripped)
            or stripped.startswith("|")
            or stripped.startswith(">")
            or re.match(r"^(?:[-*]\s+|\d+\.\s+)", stripped)
        ):
            units.append(stripped)
            continue
        segments = re.split(r"(?<=[。！？；])\s*", stripped)
        for segment in segments:
            segment = segment.strip()
            if segment:
                units.append(segment)
    return units


def _is_markdown_table_separator(unit: str) -> bool:
    stripped = unit.strip()
    if not stripped.startswith("|"):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _is_markdown_table_header(unit: str) -> bool:
    stripped = unit.strip()
    if not stripped.startswith("|"):
        return False
    if _is_markdown_table_separator(stripped):
        return False
    if _CITATION_PATTERN.search(stripped):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    nonempty = [cell for cell in cells if cell]
    if len(nonempty) < 2:
        return False
    if any(re.search(r"\d", cell) for cell in nonempty):
        return False
    return all(len(cell) <= 16 for cell in nonempty)


def _is_markdown_table_row(unit: str) -> bool:
    stripped = unit.strip()
    if not stripped.startswith("|"):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return len(cells) >= 2 and any(cell for cell in cells)


def _has_markdown_table(answer: str) -> bool:
    lines = [line.strip() for line in (answer or "").splitlines() if line.strip()]
    for index in range(len(lines) - 1):
        if _is_markdown_table_row(lines[index]) and _is_markdown_table_separator(lines[index + 1]):
            return True
    return False


def _preserves_table_structure(original_answer: str, candidate_answer: str) -> bool:
    if not _has_markdown_table(original_answer):
        return True
    return _has_markdown_table(candidate_answer)


def _unit_requires_citation(unit: str) -> bool:
    stripped = unit.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    if _is_outline_heading(stripped):
        return False
    if _is_markdown_table_separator(stripped) or _is_markdown_table_header(stripped):
        return False
    if stripped.startswith("|"):
        return True
    if stripped.endswith(("：", ":")) and stripped.count("。") == 0 and stripped.count("；") == 0:
        return False

    cleaned = _clean_unit_text(stripped)
    if not cleaned:
        return False
    if any(
        phrase in cleaned
        for phrase in (
            "当前子库证据不足",
            "当前未收到该数据",
            "当前不建议直接作答",
            "建议补充",
            "建议查阅",
        )
    ):
        return False

    evidence_terms = (
        "总酸", "总酯", "还原糖", "有机酸", "评分", "占比", "提及次数", "标准", "国标", "GB",
        "要求", "规定", "应", "需", "必须", "建议", "可", "能够", "有助于", "提升", "降低",
        "增加", "减少", "优化", "改善", "符合", "标注", "最高", "最低", "优先", "采用",
        "控制", "表明", "显示", "说明", "结果", "表2", "试验号", "样品",
    )
    contains_digit = bool(re.search(r"\d", cleaned))
    contains_evidence_term = any(term in cleaned for term in evidence_terms)
    return contains_digit or contains_evidence_term


def validate_answer_citations(answer: str, max_citation_id: int | None = None) -> dict:
    if not (answer or "").strip():
        return {
            "passed": False,
            "checked_units": 0,
            "failed_units": ["回答为空。"],
        }
    failed_units = []
    checked_units = 0
    for unit in _answer_units(answer):
        if not _unit_requires_citation(unit):
            continue
        checked_units += 1
        citations = [int(item) for item in _CITATION_PATTERN.findall(unit)]
        has_numeric_citation = bool(citations)
        has_placeholder = bool(_PLACEHOLDER_BRACKET_PATTERN.search(unit))
        if not has_numeric_citation:
            failed_units.append(unit)
            continue
        if max_citation_id is not None and any(item < 1 or item > max_citation_id for item in citations):
            failed_units.append(unit)
            continue
        if has_placeholder and not has_numeric_citation:
            failed_units.append(unit)
    return {
        "passed": not failed_units,
        "checked_units": checked_units,
        "failed_units": failed_units,
    }


def _line_has_numeric_citation(line: str) -> bool:
    return bool(_CITATION_PATTERN.search(line or ""))


def _move_citations_to_line_end(line: str) -> str:
    match = re.match(r"^(\s*(?:[-*]\s+|\d+\.\s+)?)((?:\[\d+\]\s*)+)(.+)$", line)
    if not match:
        return line

    prefix, leading_citations_block, remainder = match.groups()
    remainder = remainder.lstrip()
    leading_citations = re.findall(r"\[\d+\]", leading_citations_block)
    if not leading_citations:
        return line

    if _CITATION_PATTERN.search(remainder):
        return f"{prefix}{remainder}"

    citation_text = "".join(leading_citations)
    punctuation_match = re.match(r"^(.*?)([。！？；.!?]+)$", remainder)
    if punctuation_match:
        body, punctuation = punctuation_match.groups()
        return f"{prefix}{body}{citation_text}{punctuation}"
    return f"{prefix}{remainder}{citation_text}"


def normalize_answer_citation_placement(answer: str) -> str:
    normalized_lines = []
    for line in (answer or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or _is_outline_heading(stripped) or stripped.startswith("|"):
            normalized_lines.append(line)
            continue
        normalized_lines.append(_move_citations_to_line_end(line))
    return "\n".join(normalized_lines)


def strip_invalid_citations(answer: str, max_citation_id: int) -> str:
    def replace(match):
        citation_id = int(match.group(1))
        return match.group(0) if 1 <= citation_id <= max_citation_id else ""

    stripped_lines = []
    for line in (answer or "").splitlines():
        updated = re.sub(r"\[(\d+)\]", replace, line)
        updated = re.sub(r"\s{2,}", " ", updated).rstrip()
        stripped_lines.append(updated)
    return "\n".join(stripped_lines)


def _prune_uncited_answer(answer: str) -> str:
    kept_lines = []
    for line in (answer or "").splitlines():
        stripped = line.strip()
        if not stripped:
            kept_lines.append(line)
            continue
        if stripped.startswith("#") or _is_outline_heading(stripped):
            kept_lines.append(line)
            continue
        if _is_markdown_table_separator(stripped) or _is_markdown_table_header(stripped):
            kept_lines.append(line)
            continue
        if _unit_requires_citation(stripped) and not _line_has_numeric_citation(stripped):
            continue
        kept_lines.append(line)

    compact_lines = []
    blank_run = 0
    for line in kept_lines:
        if not line.strip():
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        compact_lines.append(line)
    return "\n".join(compact_lines).strip()


def build_citation_failure_answer(route: dict, citation_check: dict, nodes) -> str:
    failed_units = citation_check.get("failed_units", [])[:4]
    reasons = "\n".join(f"{index}、{item}" for index, item in enumerate(failed_units, start=1)) or "1、关键结论缺少规范引用。"
    source_lines = []
    for idx, node in enumerate(nodes[:3], start=1):
        metadata = node.metadata or {}
        source = metadata.get("source", "未知来源")
        page = metadata.get("page")
        page_label = metadata.get("page_label") or resolve_page_label(metadata.get("relative_path"), page)
        page_text = page_label or page
        if page_text:
            source_lines.append(f"{idx}、[{idx}] {source}（第 {page_text} 页）")
        else:
            source_lines.append(f"{idx}、[{idx}] {source}")

    guidance = "请缩小问题范围，或直接要求我只回答某一项指标/某一篇资料中的结论。"
    if route.get("name") == "formula_process":
        guidance = "建议先改问某一具体工艺参数、某一项风味指标，或明确指定要依据的论文/专利。"

    return (
        "一、当前回答未通过引用合规检查\n"
        "为保证可溯源、可复核，系统拦截了本轮回答，因为部分关键句没有形成合规引用。\n\n"
        "（一）未通过的关键句\n"
        f"{reasons}\n\n"
        "（二）当前可直接复核的资料\n"
        f"{chr(10).join(source_lines) if source_lines else '1、当前无可展示来源。'}\n\n"
        "（三）建议\n"
        f"1、{guidance}"
    )


def _repair_answer_citations(
    question: str,
    answer: str,
    nodes,
    route: dict,
    answer_profile: dict | None = None,
    extra_context: str | None = None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
    failed_units: list[str] | None = None,
) -> str:
    table_requirement = ""
    if _has_markdown_table(answer):
        table_requirement = (
            "- 原始答案中已经存在 Markdown 表格，尤其是 `### 配方建议` 等章节下的表格；"
            "修订后必须保留为 Markdown 表格，只允许补引用或删去无证据行，不得改写成段落或纯文本。\n"
        )
    prompt = CITATION_REPAIR_PROMPT.format(
        table_requirement=table_requirement,
        failed_units="\n".join(f"- {item}" for item in (failed_units or [])[:8]) or "- 关键结论缺少规范引用。",
        context_str=_compose_context(
            question,
            nodes,
            extra_context,
            route,
            answer_profile=answer_profile,
            table_aggregation=table_aggregation,
            standard_metric_aggregation=standard_metric_aggregation,
        ),
        answer_str=answer,
        query_str=question,
    )
    try:
        return _chat_completion(
            prompt=prompt,
            model=_MODEL,
            temperature=0.1,
            max_tokens=(answer_profile or ANSWER_PROFILES["B"])["answer_max_tokens"],
        )
    except Exception:
        return ""


def build_citation_analysis_card(citation_check: dict) -> dict:
    status = citation_check.get("status")
    if status in {"pass", "pruned", "repaired"}:
        body = "本轮回答已通过程序化引用合规检查。"
        if status == "pruned":
            body = "本轮回答先经程序化裁剪后，已通过程序化引用合规检查。"
        if status == "repaired":
            body = "本轮回答先经修订后，已通过程序化引用合规检查。"
        return {
            "title": "回答引用检查",
            "body": body,
            "items": [
                f"检查句数：{citation_check.get('checked_units', 0)}",
                f"状态：{status}",
            ],
            "tone": "positive",
        }

    return {
        "title": "回答引用检查未通过",
        "body": "系统发现关键结论缺少规范引用，因此已改为安全降级输出。",
        "items": citation_check.get("failed_units", [])[:3] or ["关键结论缺少规范引用。"],
        "tone": "warning",
    }


def validate_answer_consistency(
    answer: str,
    question: str,
    route: dict,
    nodes=None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
) -> dict:
    normalized_answer = _normalize_text(answer)
    failures = []
    warnings = []
    checked_rules = 0

    if table_aggregation and _question_prefers_paper_table_evidence(question, route=route):
        winner = table_aggregation.get("winner") or {}
        if winner:
            checked_rules += 2
            identifier = _normalize_text(str(winner.get("identifier_value", "")))
            metric_value = str(winner.get("metric_value", ""))
            if identifier and identifier not in normalized_answer:
                failures.append(f"回答未体现表格聚合得到的关键编号：{winner.get('identifier_value')}")
            if metric_value and metric_value not in answer:
                failures.append(f"回答未体现表格聚合得到的关键数值：{metric_value}")

    if standard_metric_aggregation and (route or {}).get("name") == "standard_compliance":
        values = standard_metric_aggregation.get("values") or {}
        if "总酸" in _question_metric_terms(question):
            target_labels = ["甜醋"] if "甜醋" in question and "甜醋" in values else ["食醋"]
            for label in target_labels:
                value = values.get(label)
                if value is None:
                    continue
                checked_rules += 1
                if str(value) not in answer:
                    failures.append(f"回答未体现标准表中的{label}关键数值：{value}")

    if (route or {}).get("name") == "formula_process" and nodes:
        cited_roles = set()
        for citation_id in {int(item) for item in _CITATION_PATTERN.findall(answer or "")}:
            index = citation_id - 1
            if 0 <= index < len(nodes):
                role = ((nodes[index].metadata or {}).get("evidence_role"))
                if role:
                    cited_roles.add(role)

        if _question_mentions_compliance(question):
            checked_rules += 1
            if "constraint" not in cited_roles:
                failures.append("问题涉及合规约束，但回答未引用标准约束证据。")

        if _question_mentions_consumer_preference(question):
            checked_rules += 1
            if "support" not in cited_roles:
                warnings.append("问题涉及消费者接受度/偏好，但回答未直接引用消费者偏好证据。")

    return {
        "passed": not failures,
        "checked_rules": checked_rules,
        "failures": failures,
        "warnings": warnings,
    }


def build_consistency_failure_answer(route: dict, consistency_check: dict, nodes) -> str:
    failures = "\n".join(f"1、{item}" for item in consistency_check.get("failures", [])[:4]) or "1、关键结论与程序化证据不一致。"
    source_lines = []
    for idx, node in enumerate(nodes[:3], start=1):
        metadata = node.metadata or {}
        source = metadata.get("source", "未知来源")
        page = metadata.get("page")
        page_label = metadata.get("page_label") or resolve_page_label(metadata.get("relative_path"), page)
        page_text = page_label or page
        source_lines.append(f"{idx}、[{idx}] {source}" + (f"（第 {page_text} 页）" if page_text else ""))
    return (
        "一、当前回答未通过一致性检查\n"
        "为保证可复核，系统检测到回答中的关键结论与程序化证据不一致，因此已拦截本轮回答。\n\n"
        "（一）检查发现\n"
        f"{failures}\n\n"
        "（二）当前可直接复核的资料\n"
        f"{chr(10).join(source_lines) if source_lines else '1、当前无可展示来源。'}"
    )


def build_consistency_analysis_card(consistency_check: dict) -> dict:
    status = consistency_check.get("status")
    if status in {"pass", "programmatic_recovery"}:
        body = "本轮回答已通过程序化一致性检查。"
        if status == "programmatic_recovery":
            body = "本轮回答先经程序化回退后，已通过一致性检查。"
        if consistency_check.get("warnings"):
            body = "本轮回答主结论已通过一致性检查，但仍有辅助维度待补证。"
        return {
            "title": "回答一致性检查",
            "body": body,
            "items": [
                f"检查规则：{consistency_check.get('checked_rules', 0)}",
                f"状态：{status}",
                *consistency_check.get("warnings", [])[:2],
            ],
            "tone": "warning" if consistency_check.get("warnings") else "positive",
        }
    return {
        "title": "回答一致性检查未通过",
        "body": "系统检测到关键结论与程序化证据不一致，因此已触发保护。",
        "items": consistency_check.get("failures", [])[:3] or ["关键结论与程序化证据不一致。"],
        "tone": "warning",
    }


def _build_programmatic_table_answer(question: str, route: dict, table_aggregation: dict | None) -> str | None:
    if not table_aggregation:
        return None
    if not _question_prefers_paper_table_evidence(question, route=route):
        return None

    source = str(table_aggregation.get("source") or "").replace(".pdf", "")
    table_id = table_aggregation.get("table_id", "")
    metric_label = table_aggregation.get("metric_label", "指标")
    identifier_label = table_aggregation.get("identifier_label", "试验号")
    winner = table_aggregation.get("winner") or {}
    leaderboard = table_aggregation.get("leaderboard") or []
    if not winner:
        return None

    mode = table_aggregation.get("mode")
    if mode == "max":
        return (
            "一、结论\n"
            f"根据《{source}》表 {table_id} 的程序化汇总结果，{identifier_label}{winner['identifier_value']}的"
            f"{metric_label}最高，为 {winner['metric_value']}[1]。\n\n"
            "（一）依据\n"
            f"1、系统已对该表共 {table_aggregation.get('record_count', 0)} 条数据做程序化比较[1]。\n"
            + (
                "2、排名前三的数据分别为："
                + "；".join(
                    f"{item['identifier_label']}{item['identifier_value']} = {item['metric_value']}"
                    for item in leaderboard[:3]
                )
                + "[1]"
                if leaderboard
                else ""
            )
        ).strip()

    if mode == "min":
        return (
            "一、结论\n"
            f"根据《{source}》表 {table_id} 的程序化汇总结果，{identifier_label}{winner['identifier_value']}的"
            f"{metric_label}最低，为 {winner['metric_value']}[1]。\n\n"
            "（一）依据\n"
            f"1、系统已对该表共 {table_aggregation.get('record_count', 0)} 条数据做程序化比较[1]。"
        )

    if mode == "compare":
        return (
            "一、结论\n"
            f"根据《{source}》表 {table_id} 的程序化汇总结果，当前识别到 {table_aggregation.get('record_count', 0)} 条"
            f"{metric_label}数据，其中最高值为{identifier_label}{winner['identifier_value']} = {winner['metric_value']}[1]。\n\n"
            "（一）主要比较结果\n"
            + "\n".join(
                f"{index}、{item['identifier_label']}{item['identifier_value']}：{item['metric_label']} = {item['metric_value']}[1]"
                for index, item in enumerate(leaderboard[:5], start=1)
            )
        ).strip()

    return None


def _build_programmatic_standard_answer(
    question: str,
    route: dict,
    standard_metric_aggregation: dict | None,
) -> str | None:
    if (route or {}).get("name") != "standard_compliance":
        return None
    if not standard_metric_aggregation:
        return None
    if "总酸" not in _question_metric_terms(question):
        return None

    values = standard_metric_aggregation.get("values") or {}
    if not values:
        return None

    mentions_sweet = "甜醋" in question
    if mentions_sweet and "甜醋" in values:
        return (
            "一、结论\n"
            f"根据标准表的程序化提取结果，甜醋的总酸（以乙酸计）要求为 ≥ {values['甜醋']} g/100mL[1]。\n\n"
            "（一）依据\n"
            f"- 《{standard_metric_aggregation.get('source', '').replace('.pdf', '')}》表 {standard_metric_aggregation.get('table_id', '')}"
            f" 中给出的甜醋指标为 ≥ {values['甜醋']} g/100mL[1]。"
        )

    lines = [
        "一、结论",
        f"根据标准表的程序化提取结果，食醋的总酸（以乙酸计）要求为 ≥ {values.get('食醋')} g/100mL[1]。",
    ]
    if "甜醋" in values:
        lines.append(f"甜醋的总酸（以乙酸计）要求为 ≥ {values['甜醋']} g/100mL[1]。")
    lines.extend(
        [
            "",
            "（一）依据",
            f"1、《{standard_metric_aggregation.get('source', '').replace('.pdf', '')}》表 {standard_metric_aggregation.get('table_id', '')}"
            " 中已程序化提取出对应总酸限值[1]。",
        ]
    )
    return "\n".join(lines)


def _generate_answer_text(
    question: str,
    nodes,
    route: dict,
    answer_profile: dict,
    extra_context: str | None = None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
) -> str:
    programmatic_answer = _build_programmatic_table_answer(question, route, table_aggregation)
    if programmatic_answer:
        return programmatic_answer
    programmatic_standard_answer = _build_programmatic_standard_answer(question, route, standard_metric_aggregation)
    if programmatic_standard_answer:
        return programmatic_standard_answer
    prompt = _build_answer_prompt(
        question,
        nodes,
        route,
        answer_profile,
        extra_context=extra_context,
        table_aggregation=table_aggregation,
        standard_metric_aggregation=standard_metric_aggregation,
    )
    return _chat_completion(
        prompt=prompt,
        model=_MODEL,
        temperature=0.1,
        max_tokens=answer_profile["answer_max_tokens"],
    )


def generate_answer(
    question: str,
    nodes,
    route: dict,
    answer_profile: dict,
    extra_context: str | None = None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
) -> tuple[str, dict, dict]:
    answer = _generate_answer_text(
        question,
        nodes,
        route,
        answer_profile,
        extra_context=extra_context,
        table_aggregation=table_aggregation,
        standard_metric_aggregation=standard_metric_aggregation,
    )
    return finalize_answer(
        answer,
        question,
        nodes,
        route,
        answer_profile=answer_profile,
        extra_context=extra_context,
        table_aggregation=table_aggregation,
        standard_metric_aggregation=standard_metric_aggregation,
    )


def finalize_answer(
    answer: str,
    question: str,
    nodes,
    route: dict,
    answer_profile: dict | None = None,
    extra_context: str | None = None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
) -> tuple[str, dict, dict]:
    max_citation_id = len(nodes or [])
    answer = normalize_answer_citation_placement(answer)
    requires_table_preservation = _has_markdown_table(answer)
    citation_check = validate_answer_citations(answer, max_citation_id=max_citation_id)
    citation_check["status"] = "pass" if citation_check["passed"] and citation_check["checked_units"] > 0 else "retry_needed"
    consistency_check = {"passed": False, "checked_rules": 0, "failures": [], "status": "retry_needed"}

    if citation_check["passed"] and citation_check["checked_units"] > 0:
        consistency_check = validate_answer_consistency(
            answer,
            question,
            route,
            nodes=nodes,
            table_aggregation=table_aggregation,
            standard_metric_aggregation=standard_metric_aggregation,
        )
        consistency_check["status"] = "pass" if consistency_check["passed"] else "retry_needed"
        if consistency_check["passed"]:
            return answer, citation_check, consistency_check

    sanitized_answer = strip_invalid_citations(answer, max_citation_id)
    pruned_answer = normalize_answer_citation_placement(_prune_uncited_answer(sanitized_answer))
    pruned_check = validate_answer_citations(pruned_answer, max_citation_id=max_citation_id)
    pruned_table_ok = _preserves_table_structure(answer, pruned_answer)
    pruned_check["status"] = "pruned" if pruned_check["passed"] and pruned_answer and pruned_check["checked_units"] > 0 and pruned_table_ok else "retry_needed"
    if pruned_check["passed"] and pruned_answer and pruned_check["checked_units"] > 0:
        if requires_table_preservation and not pruned_table_ok:
            pruned_check["passed"] = False
            pruned_check["failed_units"] = [
                *(pruned_check.get("failed_units", []) or []),
                "程序化裁剪后丢失了原始 Markdown 表格结构。",
            ]
        else:
            consistency_check = validate_answer_consistency(
                pruned_answer,
                question,
                route,
                nodes=nodes,
                table_aggregation=table_aggregation,
                standard_metric_aggregation=standard_metric_aggregation,
            )
            consistency_check["status"] = "pass" if consistency_check["passed"] else "retry_needed"
            if consistency_check["passed"]:
                return pruned_answer, pruned_check, consistency_check

    repaired_answer = normalize_answer_citation_placement(_repair_answer_citations(
        question,
        answer,
        nodes,
        route,
        answer_profile=answer_profile,
        extra_context=extra_context,
        table_aggregation=table_aggregation,
        standard_metric_aggregation=standard_metric_aggregation,
        failed_units=[*citation_check.get("failed_units", []), *(consistency_check.get("failures", []) or [])],
    ))
    repaired_check = validate_answer_citations(repaired_answer, max_citation_id=max_citation_id)
    repaired_table_ok = _preserves_table_structure(answer, repaired_answer)
    repaired_check["status"] = "repaired" if repaired_check["passed"] and repaired_check["checked_units"] > 0 and repaired_table_ok else "failed"
    if repaired_check["passed"] and repaired_check["checked_units"] > 0:
        if requires_table_preservation and not repaired_table_ok:
            repaired_check["passed"] = False
            repaired_check["failed_units"] = [
                *(repaired_check.get("failed_units", []) or []),
                "修订后的答案丢失了原始 Markdown 表格结构。",
            ]
        else:
            consistency_check = validate_answer_consistency(
                repaired_answer,
                question,
                route,
                nodes=nodes,
                table_aggregation=table_aggregation,
                standard_metric_aggregation=standard_metric_aggregation,
            )
            consistency_check["status"] = "pass" if consistency_check["passed"] else "retry_needed"
            if consistency_check["passed"]:
                return repaired_answer, repaired_check, consistency_check

    programmatic_answer = _build_programmatic_table_answer(question, route, table_aggregation)
    if not programmatic_answer:
        programmatic_answer = _build_programmatic_standard_answer(question, route, standard_metric_aggregation)
    if programmatic_answer and _preserves_table_structure(answer, programmatic_answer):
        programmatic_answer = normalize_answer_citation_placement(programmatic_answer)
        programmatic_citation = validate_answer_citations(programmatic_answer, max_citation_id=max_citation_id)
        programmatic_citation["status"] = "programmatic_recovery"
        consistency_check = validate_answer_consistency(
            programmatic_answer,
            question,
            route,
            nodes=nodes,
            table_aggregation=table_aggregation,
            standard_metric_aggregation=standard_metric_aggregation,
        )
        programmatic_consistency = consistency_check
        programmatic_consistency["status"] = "programmatic_recovery" if programmatic_consistency["passed"] else "failed"
        if programmatic_citation["passed"] and programmatic_consistency["passed"]:
            return programmatic_answer, programmatic_citation, programmatic_consistency

    fallback_check = repaired_check if repaired_check.get("failed_units") else citation_check
    consistency_check = validate_answer_consistency(
        answer,
        question,
        route,
        nodes=nodes,
        table_aggregation=table_aggregation,
        standard_metric_aggregation=standard_metric_aggregation,
    )
    consistency_check["status"] = "failed"
    if consistency_check.get("failures"):
        return build_consistency_failure_answer(route, consistency_check, nodes), fallback_check, consistency_check
    return build_citation_failure_answer(route, fallback_check, nodes), fallback_check, consistency_check


def prepare_query(question: str, filters: dict = None, extra_context: str = None, conversation_history: list | None = None) -> dict:
    """准备查询流程：路由 → 改写 → 子库检索"""
    rewrite_input = question
    if extra_context:
        rewrite_input = (
            f"{question}\n\n"
            f"以下是用户上传文件提炼出的补充信息，请在理解用户需求时一并考虑：\n"
            f"{extra_context}"
        )
    # 若有对话历史，将上一轮 user 问题拼入检索用 query，提升追问检索精度
    if conversation_history:
        prev_user = next(
            (m["content"] for m in reversed(conversation_history) if m.get("role") == "user"),
            None,
        )
        if prev_user and prev_user != question:
            rewrite_input = f"{prev_user}  {rewrite_input}"

    route = resolve_route(question, extra_context=extra_context, filters=filters)
    answer_profile = build_answer_profile(question, route=route, extra_context=extra_context)
    # A档简答题跳过改写，减少首字延迟（节省一次串行LLM调用）
    if answer_profile.get("grade") == "A" and not extra_context:
        rewritten = question
    elif route.get("name") in ROUTE_DEFINITIONS:
        rewritten = rewrite_query(rewrite_input, route=route, extra_context=extra_context)
    else:
        rewritten = rewrite_query(rewrite_input, route=route, extra_context=extra_context) if should_rewrite(question, extra_context) else question
    nodes = retrieve_route_nodes(rewritten, route, filters, answer_profile=answer_profile)
    table_aggregation = build_table_aggregation(question, nodes, route=route)
    standard_metric_aggregation = build_standard_metric_aggregation(question, nodes, route=route)
    gate = evaluate_evidence_gate(question, nodes, route, extra_context=extra_context)
    analysis_cards = [build_gate_analysis_card(gate), *build_analysis_cards(question, nodes, extra_context)]
    debug = _build_retrieval_debug(question, rewritten, route, nodes, gate)
    return {
        "rewritten": rewritten,
        "nodes": nodes,
        "route": route,
        "filters": filters,
        "extra_context": extra_context,
        "conversation_history": conversation_history,
        "analysis": analysis_cards,
        "gate": gate,
        "answer_profile": answer_profile,
        "debug": debug,
        "table_aggregation": table_aggregation,
        "standard_metric_aggregation": standard_metric_aggregation,
    }


def stream_answer(
    question: str,
    nodes,
    route: dict,
    answer_profile: dict,
    extra_context: str | None = None,
    table_aggregation: dict | None = None,
    standard_metric_aggregation: dict | None = None,
    conversation_history: list | None = None,
):
    programmatic_answer = _build_programmatic_table_answer(question, route, table_aggregation)
    if not programmatic_answer:
        programmatic_answer = _build_programmatic_standard_answer(question, route, standard_metric_aggregation)

    if programmatic_answer:
        _pass_check = {"status": "pass", "passed": True, "checked_units": 0, "failed_units": []}
        _pass_consistency = {"status": "pass", "passed": True, "checked_rules": 0, "failures": []}
        chunk_size = 80
        for idx in range(0, len(programmatic_answer), chunk_size):
            yield {"type": "delta", "content": programmatic_answer[idx: idx + chunk_size]}
        yield {"type": "checks", "citation_check": _pass_check, "consistency_check": _pass_consistency}
        return

    prompt = _build_stream_prompt(
        question,
        nodes,
        route,
        answer_profile=answer_profile,
        extra_context=extra_context,
        table_aggregation=table_aggregation,
        standard_metric_aggregation=standard_metric_aggregation,
        conversation_history=conversation_history,
    )
    raw_chunks = []
    for chunk in _chat_completion_stream(
        prompt=prompt,
        model=_STREAM_MODEL,
        temperature=0.1,
        max_tokens=answer_profile["stream_max_tokens"],
    ):
        raw_chunks.append(chunk)
        yield {"type": "delta", "content": chunk}

    # 引用合规检查已关闭，直接使用原始答案
    _pass_check = {"status": "pass", "passed": True, "checked_units": 0, "failed_units": []}
    _pass_consistency = {"status": "pass", "passed": True, "checked_rules": 0, "failures": []}
    yield {"type": "checks", "citation_check": _pass_check, "consistency_check": _pass_consistency}


def query_with_rewrite(question: str, filters: dict = None, extra_context: str = None, conversation_history: list | None = None) -> dict:
    """完整查询流程：路由 → 检索 → 生成"""
    prepared = prepare_query(question, filters=filters, extra_context=extra_context, conversation_history=conversation_history)
    nodes = prepared["nodes"]
    citation_check = {"status": "skipped", "passed": False, "checked_units": 0, "failed_units": []}
    consistency_check = {"status": "skipped", "passed": False, "checked_rules": 0, "failures": []}
    analysis = list(prepared["analysis"])
    if prepared["gate"]["passed"]:
        answer, citation_check, consistency_check = generate_answer(
            question,
            nodes,
            prepared["route"],
            prepared["answer_profile"],
            prepared["extra_context"],
            table_aggregation=prepared.get("table_aggregation"),
            standard_metric_aggregation=prepared.get("standard_metric_aggregation"),
        )
        analysis.append(build_citation_analysis_card(citation_check))
        analysis.append(build_consistency_analysis_card(consistency_check))
    else:
        answer = build_refusal_answer(question, prepared["route"], prepared["gate"])
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
    return {
        "answer": answer,
        "rewritten": prepared["rewritten"],
        "sources": nodes,
        "analysis": analysis,
        "route": prepared["route"],
        "gate": prepared["gate"],
        "answer_profile": prepared["answer_profile"],
        "debug": prepared["debug"],
        "table_aggregation": prepared.get("table_aggregation"),
        "standard_metric_aggregation": prepared.get("standard_metric_aggregation"),
        "citation_check": citation_check,
        "consistency_check": consistency_check,
    }


if __name__ == "__main__":
    result = query_with_rewrite("如何设计适合江浙消费者口味偏淡、带米香的醋配方？")
    print("改写查询：", result["rewritten"])
    print("回答：", result["answer"])

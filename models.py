from pydantic import BaseModel


class QueryRequest(BaseModel):
    question: str
    filters: dict | None = None  # e.g. {"doc_type": "配方", "region": "江苏"}
    conversation_history: list[dict] | None = None  # 多轮对话历史 [{role, content}, ...]


class Source(BaseModel):
    text: str
    metadata: dict


class AnalysisCard(BaseModel):
    title: str
    body: str
    items: list[str] = []
    tone: str = "neutral"


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    citation_catalog: list[Source] = []
    analysis: list[AnalysisCard] = []

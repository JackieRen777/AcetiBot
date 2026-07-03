from pydantic import BaseModel


class QueryRequest(BaseModel):
    question: str
    filters: dict | None = None  # e.g. {"doc_type": "配方", "region": "江苏"}


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
    analysis: list[AnalysisCard] = []

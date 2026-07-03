"""硅基流动 Embedding — 兼容 SiliconFlow /v1/embeddings API"""
import requests
from llama_index.core.embeddings import BaseEmbedding

class SiliconFlowEmbedding(BaseEmbedding):
    api_key: str
    model: str = "BAAI/bge-m3"
    api_base: str = "https://api.siliconflow.cn/v1"

    def _embed(self, texts: list) -> list:
        resp = requests.post(
            f"{self.api_base}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=60,
        )
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]

    def _get_query_embedding(self, query: str) -> list:
        return self._embed([query])[0]

    def _get_text_embedding(self, text: str) -> list:
        return self._embed([text])[0]

    def _get_text_embeddings(self, texts: list) -> list:
        return self._embed(texts)

    async def _aget_query_embedding(self, query: str) -> list:
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> list:
        return self._get_text_embedding(text)

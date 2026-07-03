"""检索质量诊断脚本 — 基于现有知识库做基线测试"""
import os
import chromadb
from dotenv import load_dotenv
from embeddings import SiliconFlowEmbedding

load_dotenv()
embed = SiliconFlowEmbedding(api_key=os.getenv("SILICONFLOW_API_KEY"))
client = chromadb.PersistentClient(path="./chroma_db")
col = client.get_collection("vinegar_kb")

# 测试问题集（基于已入库的14个国标文件）
TEST_QUERIES = [
    ("酿造食醋总酸含量要求",        ["GB18187-2000.pdf", "GB2719-2018.pdf"]),
    ("食醋中真菌毒素限量",           ["GB2761-2017-kw-1.pdf"]),
    ("食品添加剂在食醋中的使用规定",  ["GB2760-2024kw.pdf"]),
    ("镇江香醋地理标志产品标准",      ["GBT19777-2013bz.pdf"]),  # 扫描件，预期失败
    ("食品标签通则要求",             ["GB7718-2025.pdf"]),
]

print("检索质量诊断报告")
print("=" * 60)

hits, misses = 0, 0
for query, expected_sources in TEST_QUERIES:
    vec = embed.get_query_embedding(query)
    results = col.query(query_embeddings=[vec], n_results=5, include=["metadatas","distances"])
    retrieved = [m.get("source","?") for m in results["metadatas"][0]]
    dists = results["distances"][0]

    hit = any(s in retrieved for s in expected_sources)
    status = "✅ HIT" if hit else "❌ MISS"
    if hit: hits += 1
    else: misses += 1

    print(f"\n{status} | {query}")
    print(f"  期望: {expected_sources[0]}")
    print(f"  Top3: {retrieved[:3]}")
    print(f"  距离: {[round(d,3) for d in dists[:3]]}")

print(f"\n{'='*60}")
print(f"命中率: {hits}/{hits+misses} = {hits/(hits+misses)*100:.0f}%")
print("（注：距离越小越相关，<0.5 为良好，>0.8 为偏差大）")

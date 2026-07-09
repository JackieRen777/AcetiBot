"""本地自验收脚本：检查路由、门禁与首屏证据是否符合预期。"""

from __future__ import annotations

import json
from pathlib import Path

from query import prepare_query, query_with_rewrite

CASES_PATH = Path("docs/acceptance_cases_v1.json")


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def summarize_top_node(prepared: dict) -> dict:
    nodes = prepared.get("nodes", [])
    if not nodes:
        return {}
    node = nodes[0]
    metadata = node.metadata or {}
    return {
        "source": metadata.get("source", ""),
        "doc_type": metadata.get("doc_type", ""),
        "chunk_kind": metadata.get("chunk_kind", "paragraph"),
        "page": metadata.get("page"),
        "score": round(float(node.score or 0.0), 4),
        "text": node.text[:180].replace("\n", " "),
    }


def summarize_table_aggregation(prepared: dict) -> dict:
    aggregation = prepared.get("table_aggregation") or {}
    winner = aggregation.get("winner") or {}
    return {
        "mode": aggregation.get("mode"),
        "source": aggregation.get("source", ""),
        "table_id": aggregation.get("table_id", ""),
        "record_count": aggregation.get("record_count", 0),
        "metric_label": aggregation.get("metric_label", ""),
        "winner_identifier": winner.get("identifier_value", ""),
        "winner_value": winner.get("metric_value"),
    }


def summarize_citation_check(result: dict) -> dict:
    check = result.get("citation_check") or {}
    return {
        "status": check.get("status"),
        "passed": check.get("passed"),
        "checked_units": check.get("checked_units", 0),
        "failed_units": check.get("failed_units", []),
    }


def summarize_consistency_check(result: dict) -> dict:
    check = result.get("consistency_check") or {}
    return {
        "status": check.get("status"),
        "passed": check.get("passed"),
        "checked_rules": check.get("checked_rules", 0),
        "failures": check.get("failures", []),
    }


def evaluate_case(case: dict) -> dict:
    prepared = prepare_query(case["question"])
    route_name = prepared["route"]["name"]
    gate_passed = prepared["gate"]["passed"]
    top = summarize_top_node(prepared)
    aggregation = summarize_table_aggregation(prepared)
    answer_result = None
    citation = {}
    consistency = {}
    if case.get("check_answer_citations"):
        answer_result = query_with_rewrite(case["question"])
        citation = summarize_citation_check(answer_result)
        consistency = summarize_consistency_check(answer_result)

    checks = []
    checks.append(("route", route_name == case["expected_route"], route_name, case["expected_route"]))
    checks.append(("gate", gate_passed != case["should_refuse"], gate_passed, not case["should_refuse"]))

    expected_source_contains = case.get("expected_source_contains")
    if expected_source_contains:
        actual = top.get("source", "")
        checks.append(("source", expected_source_contains in actual, actual, expected_source_contains))

    expected_doc_type = case.get("expected_doc_type")
    if expected_doc_type:
        actual = top.get("doc_type", "")
        checks.append(("doc_type", actual == expected_doc_type, actual, expected_doc_type))

    expected_chunk_kind = case.get("expected_chunk_kind")
    if expected_chunk_kind:
        actual = top.get("chunk_kind", "")
        checks.append(("chunk_kind", actual == expected_chunk_kind, actual, expected_chunk_kind))

    expected_aggregation_mode = case.get("expected_aggregation_mode")
    if expected_aggregation_mode:
        actual = aggregation.get("mode")
        checks.append(("aggregation_mode", actual == expected_aggregation_mode, actual, expected_aggregation_mode))

    expected_aggregation_source_contains = case.get("expected_aggregation_source_contains")
    if expected_aggregation_source_contains:
        actual = aggregation.get("source", "")
        checks.append(
            (
                "aggregation_source",
                expected_aggregation_source_contains in actual,
                actual,
                expected_aggregation_source_contains,
            )
        )

    expected_winner_identifier = case.get("expected_winner_identifier")
    if expected_winner_identifier:
        actual = aggregation.get("winner_identifier", "")
        checks.append(("winner_identifier", actual == expected_winner_identifier, actual, expected_winner_identifier))

    expected_citation_status = case.get("expected_citation_status")
    if expected_citation_status:
        actual = citation.get("status")
        checks.append(("citation_status", actual == expected_citation_status, actual, expected_citation_status))

    expected_citation_passed = case.get("expected_citation_passed")
    if expected_citation_passed is not None:
        actual = citation.get("passed")
        checks.append(("citation_passed", actual == expected_citation_passed, actual, expected_citation_passed))

    expected_answer_contains = case.get("expected_answer_contains") or []
    for idx, fragment in enumerate(expected_answer_contains, start=1):
        actual = fragment in ((answer_result or {}).get("answer", ""))
        checks.append((f"answer_contains_{idx}", actual, actual, fragment))

    expected_consistency_passed = case.get("expected_consistency_passed")
    if expected_consistency_passed is not None:
        actual = consistency.get("passed")
        checks.append(("consistency_passed", actual == expected_consistency_passed, actual, expected_consistency_passed))

    passed = all(item[1] for item in checks)
    return {
        "id": case["id"],
        "question": case["question"],
        "pending": case.get("pending", False),
        "passed": passed,
        "checks": checks,
        "route": route_name,
        "gate_passed": gate_passed,
        "top": top,
        "aggregation": aggregation,
        "citation": citation,
        "consistency": consistency,
        "answer": (answer_result or {}).get("answer", ""),
        "gate_failures": [failure["message"] for failure in prepared.get("gate", {}).get("failures", [])],
        "debug": prepared.get("debug", {}),
    }


def main() -> None:
    cases = load_cases()
    results = []
    executed = 0
    passed = 0
    skipped = 0

    print("AcetiBot 本地自验收")
    print("=" * 72)

    for case in cases:
        if case.get("pending"):
            skipped += 1
            print(f"\n⏭️  SKIP | {case['id']}")
            print(f"  说明: {case.get('notes', 'pending')}")
            continue

        executed += 1
        result = evaluate_case(case)
        results.append(result)

        status = "✅ PASS" if result["passed"] else "❌ FAIL"
        print(f"\n{status} | {case['id']}")
        print(f"  问题: {case['question']}")
        print(f"  路由: {result['route']}")
        print(f"  门禁通过: {result['gate_passed']}")
        if result["top"]:
            print(
                "  Top1: "
                f"{result['top'].get('source', '?')} | {result['top'].get('doc_type', '?')} | "
                f"{result['top'].get('chunk_kind', '?')} | score={result['top'].get('score', 0.0)}"
            )
        if result["debug"]:
            print(f"  检索分布: {result['debug'].get('retrieved_doc_types', {})}")
        if result["aggregation"].get("mode"):
            print(
                "  聚合: "
                f"{result['aggregation'].get('mode')} | {result['aggregation'].get('source', '?')} | "
                f"{result['aggregation'].get('winner_identifier', '?')} -> {result['aggregation'].get('winner_value')}"
            )
        if result["citation"].get("status"):
            print(
                "  引用检查: "
                f"{result['citation'].get('status')} | passed={result['citation'].get('passed')} | "
                f"checked={result['citation'].get('checked_units', 0)}"
            )
        if result["consistency"].get("status"):
            print(
                "  一致性检查: "
                f"{result['consistency'].get('status')} | passed={result['consistency'].get('passed')} | "
                f"rules={result['consistency'].get('checked_rules', 0)}"
            )
        if result["gate_failures"]:
            print(f"  门禁原因: {result['gate_failures']}")

        for name, ok, actual, expected in result["checks"]:
            mark = "OK" if ok else "MISS"
            print(f"    - {name}: {mark} | actual={actual} | expected={expected}")

        if result["passed"]:
            passed += 1

    print(f"\n{'=' * 72}")
    print(f"执行用例: {executed}")
    print(f"通过数量: {passed}")
    print(f"失败数量: {executed - passed}")
    print(f"预留跳过: {skipped}")


if __name__ == "__main__":
    main()

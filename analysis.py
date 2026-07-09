"""轻量分析层：按需展示地域识别与感官数据摘要。"""
import re


REGION_RULES = [
    {
        "label": "江浙",
        "keywords": ["江浙", "江苏", "浙江", "江南", "苏南", "杭嘉湖"],
        "profile": "偏好酸感柔和、口味清爽、米香或甜润表达。",
    },
    {
        "label": "华北",
        "keywords": ["华北", "北京", "天津", "河北", "山东"],
        "profile": "偏好风味更厚实、酸感更明确、综合色泽与醇厚度。",
    },
    {
        "label": "华南",
        "keywords": ["华南", "广东", "广西", "福建", "海南"],
        "profile": "偏好鲜甜协调、入口顺滑、接受度更看重复合香气。",
    },
    {
        "label": "川渝",
        "keywords": ["川渝", "四川", "重庆", "成都"],
        "profile": "偏好酸香更突出、刺激度略强、复合发酵香更鲜明。",
    },
    {
        "label": "东北",
        "keywords": ["东北", "辽宁", "吉林", "黑龙江"],
        "profile": "偏好风味直接、酸味存在感明确、佐餐适配性强。",
    },
]

SENSOR_AXIS_ORDER = ["酸", "甜", "苦", "鲜", "咸"]


def detect_region(question: str) -> dict | None:
    lowered = question.lower()
    for rule in REGION_RULES:
        matched = [keyword for keyword in rule["keywords"] if keyword.lower() in lowered]
        if matched:
            return {
                "title": "地域识别",
                "body": f"已识别目标地域为{rule['label']}，本轮建议会优先参考这一地区的口味偏好。",
                "items": [
                    f"命中关键词：{'、'.join(matched[:3])}",
                    f"风味倾向：{rule['profile']}",
                ],
                "tone": "accent",
            }
    return None


def summarize_sensor_context(extra_context: str | None) -> dict | None:
    if not extra_context or "感官检测 CSV" not in extra_context:
        return None

    sample_id = re.search(r"样本编号：([^。]+)", extra_context)
    label = re.search(r"样品标签：([^。]+)", extra_context)
    values_match = re.search(r"五维感官值：([^。]+)", extra_context)

    items = []
    if sample_id:
        items.append(f"样本编号：{sample_id.group(1).strip()}")
    if label:
        items.append(f"样品标签：{label.group(1).strip()}")
    if values_match:
        values = values_match.group(1)
        parsed = {}
        for axis, value in re.findall(r"([酸甜苦鲜咸])=([0-9]+(?:\.[0-9]+)?)", values):
            parsed[axis] = value
        if parsed:
            ordered = [f"{axis}={parsed[axis]}" for axis in SENSOR_AXIS_ORDER if axis in parsed]
            items.append(f"五维特征：{'，'.join(ordered)}")

    return {
        "title": "感官数据已纳入",
        "body": "本轮回答已将上传的电子鼻/电子舌特征作为补充分析依据，而不只是附件展示。",
        "items": items or ["已识别到感官 CSV，并用于本轮检索与生成。"],
        "tone": "success",
    }

def build_analysis_cards(question: str, nodes, extra_context: str | None) -> list[dict]:
    cards = []
    region_card = detect_region(question)
    if region_card:
        cards.append(region_card)

    sensor_card = summarize_sensor_context(extra_context)
    if sensor_card:
        cards.append(sensor_card)
    return cards

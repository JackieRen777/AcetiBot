"""轻量分析层：地域识别、感官数据摘要、标准核查提示。"""
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


def build_compliance_note(question: str, nodes) -> dict:
    sources = [((node.metadata or {}).get("source") or "") for node in nodes]
    items = []

    if any("GB2719" in source for source in sources):
        items.append("已关联 GB2719-2018《食醋》作为基础理化与分类约束。")
    if any("GB18187" in source for source in sources):
        items.append("已关联 GB/T 18187 作为酿造食醋相关工艺/产品标准依据。")
    if any("GB7718" in source for source in sources):
        items.append("已关联 GB 7718，可用于后续标签文案与标示核查。")
    if any("GB28050" in source for source in sources):
        items.append("已关联 GB 28050，可用于后续营养成分表标示核查。")

    lowered = question.lower()
    if "甜醋" in question and any("GB2719" in source for source in sources):
        items.append("甜醋总酸硬约束：≥ 2.5 g/100mL。")
    elif any(keyword in lowered for keyword in ["酸度", "总酸", "酸含量"]) and any("GB2719" in source for source in sources):
        items.append("普通食醋总酸硬约束：≥ 3.5 g/100mL；甜醋总酸：≥ 2.5 g/100mL。")

    if items:
        return {
            "title": "标准核查",
            "body": "本轮回答已自动挂接到现有标准来源，可作为建议边界与后续人工复核起点。",
            "items": items,
            "tone": "warning",
        }

    return {
        "title": "标准核查",
        "body": "当前回答暂未命中明确的标准型证据。若后续给出具体酸度、标签或工艺参数，建议继续追加标准核查。",
        "items": [],
        "tone": "neutral",
    }


def build_analysis_cards(question: str, nodes, extra_context: str | None) -> list[dict]:
    cards = []
    region_card = detect_region(question)
    if region_card:
        cards.append(region_card)

    sensor_card = summarize_sensor_context(extra_context)
    if sensor_card:
        cards.append(sensor_card)

    cards.append(build_compliance_note(question, nodes))
    return cards

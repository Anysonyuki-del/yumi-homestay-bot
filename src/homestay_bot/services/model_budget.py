import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelBudget:
    """集中定义生产模型调用的字符、轮次和输出硬上限。"""

    main_request_chars: int = 48_000
    main_chain_chars: int = 120_000
    main_calls: int = 3
    tool_result_rounds: int = 2
    main_max_tokens: int = 1_800
    refinement_max_tokens: int = 900
    delivery_rewrite_max_tokens: int = 900
    history_messages: int = 6
    history_message_chars: int = 1_000
    history_total_chars: int = 6_000
    question_chars: int = 2_000
    customer_context_chars: int = 6_000
    faq_candidates: int = 20
    faq_candidates_chars: int = 4_000
    tool_result_chars: int = 24_000


MODEL_BUDGET = ModelBudget()


def serialized_chars(value: Any) -> int:
    """按实际发送 JSON 的字符数计算预算，不估算或记录正文。"""
    return len(json.dumps(value, ensure_ascii=False, default=str))


def bound_json_value(value: Any, *, char_budget: int) -> Any:
    """按 JSON 结构裁剪值，保证结果仍可被安全序列化和解析。"""
    if char_budget <= 0:
        return [] if isinstance(value, list) else {}
    if serialized_chars(value) <= char_budget:
        return value
    if isinstance(value, str):
        # 字符串使用二分查找保留最大合法前缀，避免反复线性序列化。
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            if serialized_chars(value[:middle]) <= char_budget:
                low = middle
            else:
                high = middle - 1
        return value[:low]
    if isinstance(value, list):
        bounded: list[Any] = []
        for item in value:
            list_candidate = [*bounded, item]
            if serialized_chars(list_candidate) > char_budget:
                break
            bounded.append(item)
        return bounded
    if isinstance(value, dict):
        bounded_dict: dict[str, Any] = {}
        for key, item in value.items():
            dict_candidate = {**bounded_dict, str(key): item}
            if serialized_chars(dict_candidate) <= char_budget:
                bounded_dict[str(key)] = item
                continue
            remaining = max(0, char_budget - serialized_chars(bounded_dict) - len(str(key)) - 6)
            cropped = bound_json_value(item, char_budget=remaining)
            cropped_candidate = {**bounded_dict, str(key): cropped}
            if serialized_chars(cropped_candidate) <= char_budget:
                bounded_dict[str(key)] = cropped
            break
        return bounded_dict
    return None

"""
Flow-level utilities for Step2.

- extract_flow_slots: 收集 start page 的槽位定义（flow.flow.slots）
- extract_flow_conditions: 收集 start page 的条件（flow.flow.transitionEvents 中的 condition）
"""

from typing import Dict, Any, List, Set, Tuple


def extract_flow_slots(flow_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """收集 flow 层级的槽位信息（用于 start page 抽槽）"""
    flow_obj = flow_data.get('flow', {}).get('flow', {})
    slot_infos: List[Dict[str, Any]] = []
    for slot in flow_obj.get('slots', []):
        display_name = slot.get('displayName', '')
        if display_name:
            slot_type = slot.get('type', {})
            slot_infos.append({
                'displayName': display_name,
                'entityType': slot_type.get('className', ''),
                'classType': slot_type.get('classType', '')
            })
    return slot_infos


def extract_flow_conditions(flow_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """收集 flow 层级的条件（start page 条件），带过滤与去重"""
    flow_obj = flow_data.get('flow', {}).get('flow', {})
    transition_events = flow_obj.get('transitionEvents', [])
    flow_conditions: List[Dict[str, Any]] = []
    seen: Set[Tuple[Any, Any, Any, Any, Any]] = set()  # (lhs_var, rhs_normalized, comparator, target_page_id, target_flow_id)

    for event in transition_events:
        condition = event.get('condition', {})
        handler = event.get('transitionEventHandler', {})
        target_page_id = handler.get('targetPageId')
        target_flow_id = handler.get('targetFlowId')
        comparator = None
        rhs_value = None
        lhs_exprs = []
        restriction = condition.get('restriction', condition)
        if isinstance(restriction, dict):
            comparator = restriction.get('comparator')
            rhs = restriction.get('rhs', {})
            if isinstance(rhs, dict):
                rhs_value = rhs.get('value') or rhs.get('phrase', {}).get('values', [])
            lhs = restriction.get('lhs', {})
            if isinstance(lhs, dict):
                lhs_exprs = lhs.get('member', {}).get('expressions', [])

        # 提取 lhs 变量名（取最后一段）
        lhs_var = None
        if lhs_exprs:
            last = lhs_exprs[-1].get('value')
            if last:
                lhs_var = last.replace('.', '_').replace('-', '_').replace('$', '')

        # 过滤：无 lhs、无 comparator、或 rhs 为 true/None，或 JumpTo_* 变量
        if not comparator or not lhs_var:
            continue
        if lhs_var.startswith("JumpTo_"):
            continue
        if rhs_value is None or rhs_value is True or rhs_value == "true":
            continue
        if isinstance(rhs_value, list) and any(v in ("true", True) for v in rhs_value):
            continue
        
        # 规范化 rhs_value 用于去重：如果是列表，排序后转为元组；否则转为字符串
        if isinstance(rhs_value, list):
            # 过滤掉 None、true、"true" 等无效值
            rhs_normalized = tuple(sorted([str(v) for v in rhs_value if v and v != "true" and v is not True]))
            if not rhs_normalized:
                continue
        else:
            rhs_normalized = str(rhs_value)
        
        # 去重：基于 (lhs_var, rhs_normalized, comparator, target_page_id, target_flow_id)
        # 注意：如果同一个条件指向不同的 target，应该被认为是不同的条件
        # 但如果同一个条件指向相同的 target，应该去重
        key = (lhs_var, rhs_normalized, comparator, target_page_id, target_flow_id)
        if key in seen:
            continue
        seen.add(key)

        flow_conditions.append({
            "comparator": comparator,
            "rhs": rhs_value,
            "lhs_expressions": lhs_exprs,
            "lhs_var": lhs_var,
            "target_page_id": target_page_id,
            "target_flow_id": target_flow_id
        })

    return flow_conditions


def extract_flow_conditions_for_event(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """仅针对单个 transitionEvent 提取条件（用于按 intent 生成 workflow）"""
    flow_conditions: List[Dict[str, Any]] = []
    # 注意：单个 event 通常只有一个条件，但为了保持一致性，仍然使用去重逻辑

    condition = event.get('condition', {})
    handler = event.get('transitionEventHandler', {})
    target_page_id = handler.get('targetPageId')
    target_flow_id = handler.get('targetFlowId')
    comparator = None
    rhs_value = None
    lhs_exprs = []
    restriction = condition.get('restriction', condition)
    if isinstance(restriction, dict):
        comparator = restriction.get('comparator')
        rhs = restriction.get('rhs', {})
        if isinstance(rhs, dict):
            rhs_value = rhs.get('value') or rhs.get('phrase', {}).get('values', [])
        lhs = restriction.get('lhs', {})
        if isinstance(lhs, dict):
            lhs_exprs = lhs.get('member', {}).get('expressions', [])

    lhs_var = None
    if lhs_exprs:
        last = lhs_exprs[-1].get('value')
        if last:
            lhs_var = last.replace('.', '_').replace('-', '_').replace('$', '')

    if not comparator or not lhs_var:
        return flow_conditions
    if lhs_var.startswith("JumpTo_"):
        return flow_conditions
    if rhs_value is None or rhs_value is True or rhs_value == "true":
        return flow_conditions
    if isinstance(rhs_value, list) and any(v in ("true", True) for v in rhs_value):
        return flow_conditions
    if not target_page_id and not target_flow_id:
        return flow_conditions
    
    # 规范化 rhs_value 用于去重（与 extract_flow_conditions 保持一致）
    if isinstance(rhs_value, list):
        # 过滤掉 None、true、"true" 等无效值
        rhs_normalized = tuple(sorted([str(v) for v in rhs_value if v and v != "true" and v is not True]))
        if not rhs_normalized:
            return flow_conditions
    else:
        rhs_normalized = str(rhs_value)
    
    # 去重：基于 (lhs_var, rhs_normalized, comparator, target_page_id, target_flow_id)
    # 注意：单个 event 通常只有一个条件，但为了保持一致性，仍然使用去重逻辑
    key = (lhs_var, rhs_normalized, comparator, target_page_id, target_flow_id)
    # 单个 event 不需要 seen set，直接添加

    flow_conditions.append({
        "comparator": comparator,
        "rhs": rhs_value,
        "lhs_expressions": lhs_exprs,
        "lhs_var": lhs_var,
        "target_page_id": target_page_id,
        "target_flow_id": target_flow_id
    })

    return flow_conditions



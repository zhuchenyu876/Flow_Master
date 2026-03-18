"""
Builders for intent workflow chains (flow-level slots/conditions).
"""

from typing import List, Dict, Any, Tuple, Callable, Set


def build_flow_slot_chain(
    safe_intent_name: str,
    flow_slot_infos: List[Dict[str, Any]],
    entity_candidates: Dict[str, Dict[str, List[str]]],
    lang: str,
    gen_var: Callable[[], str],
    start_name: str,
    global_config: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    构建 flow 层槽位抽取链路：start -> capture -> llm -> parse
    返回 (nodes, edges, chain_source_end)
    """
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    chain_source = start_name

    if not flow_slot_infos:
        return nodes, edges, chain_source

    flow_slot_names = [s.get('displayName') for s in flow_slot_infos if s.get('displayName')]
    if not flow_slot_names:
        return nodes, edges, chain_source

    # capture
    flow_capture_name = f"capture_flow_slots_{safe_intent_name}"
    flow_capture_node = {
        "type": "captureUserReply",
        "name": flow_capture_name,
        "title": "Capture Flow Slots (Start Page)",
        "variable_assign": "last_user_response"
    }

    # llm
    llm_var = gen_var()
    flow_llm_name = f"llm_flow_slots_{safe_intent_name}"

    # hints from entity candidates
    hint_lines = []
    for slot_info in flow_slot_infos:
        slot_name = slot_info.get('displayName', '')
        entity_type = slot_info.get('entityType', '')
        class_type = slot_info.get('classType', '')
        normalized = slot_name.replace('-', '_')
        if entity_type and class_type == 'ENUMERATION':
            # entity_type 是 className，需要加上 @ 前缀来匹配 entity_candidates 的 key
            entity_key = f"@{entity_type}"
            candidates = entity_candidates.get(entity_key, {}).get(lang, [])
            if candidates:
                hint_lines.append(f'- {normalized}: allowed values ({lang}) = ' + ", ".join(candidates))

    hint_text = ''
    if hint_lines:
        hint_text = '\n##Hints\n' + "\n".join(hint_lines) + '\n'

    slot_list = "\n".join([f"- {s.replace('-', '_')}" for s in flow_slot_names])
    output_template = "{\n" + "\n".join([f'  "{s.replace("-", "_")}": ""' for s in flow_slot_names]) + "\n}"
    flow_prompt = f'''#Role
You are an information extraction specialist. Your task is to extract parameters from the user's reply.

##User Input
{{{{last_user_response}}}}

##Parameters to Extract
{slot_list}

##Output Template
{output_template}

##Instructions
Extract the required parameters from user input and return in JSON format. If a parameter is not found, use empty string.
{hint_text}'''

    flow_llm_node = {
        "type": "llmVariableAssignment",
        "name": flow_llm_name,
        "title": "Extract Flow Slots (Start Page)",
        "prompt_template": flow_prompt,
        "variable_assign": llm_var,
        "llm_name": global_config.get("llmcodemodel", "azure-gpt-4o"),
        "chat_history_flag": global_config.get("enable_short_memory", False),
        "chat_history_count": global_config.get("short_chat_count", 5)
    }

    # code parse
    flow_parse_name = f"parse_flow_slots_{safe_intent_name}"
    normalized_slots = [s.replace('-', '_') for s in flow_slot_names]
    return_dict = ",\n".join([f'        "{v}": data.get("{v}", "")' for v in normalized_slots])
    flow_parse_code = f'''import json
import re

def main({llm_var}) -> dict:
    match = re.search(r'([{{].*?[}}])', {llm_var}, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
        except:
            data = {{}}
    else:
        data = {{}}
    return {{
{return_dict}
    }}
'''
    flow_parse_node = {
        "type": "code",
        "name": flow_parse_name,
        "title": "Parse Flow Slots (Start Page)",
        "code": flow_parse_code,
        "outputs": normalized_slots,
        "args": [llm_var]
    }

    nodes.extend([flow_capture_node, flow_llm_node, flow_parse_node])
    edges.append({
        "source_node": chain_source,
        "target_node": flow_capture_name,
        "connection_type": "default"
    })
    edges.append({
        "source_node": flow_capture_name,
        "target_node": flow_llm_name,
        "connection_type": "default"
    })
    edges.append({
        "source_node": flow_llm_name,
        "target_node": flow_parse_name,
        "connection_type": "default"
    })
    chain_source = flow_parse_name

    return nodes, edges, chain_source


def build_flow_condition_chain(
    safe_intent_name: str,
    flow_conditions: List[Dict[str, Any]],
    chain_source: str,
    gen_node_name: Callable[[str], str],
    target_page_id: str = None,
    target_flow_id: str = None,
    filter_by_target: bool = True,
    branch_target_node_map: Dict[Tuple[Any, Any, Any, Any, Any], str] = None,
    other_target_node: str = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    构建 flow 层条件链路：chain_source -> condition_node，并为每个分支创建条件边。
    返回 (nodes, edges, chain_source_end)
    
    支持 __NER_CONVERGE__ 特殊标记：当 chain_source 以 __NER_CONVERGE__ 开头时，
    表示有多个源节点需要汇聚到下一个节点。格式为 __NER_CONVERGE__node1,node2,node3
    """
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    
    # 解析 __NER_CONVERGE__ 特殊标记
    converge_sources: List[str] = []
    if chain_source and chain_source.startswith("__NER_CONVERGE__"):
        # 格式: __NER_CONVERGE__node1,node2,node3
        converge_part = chain_source[len("__NER_CONVERGE__"):]
        converge_sources = [s.strip() for s in converge_part.split(",") if s.strip()]
        # 如果没有条件，直接返回汇聚信息
        if not flow_conditions:
            return nodes, edges, chain_source
    elif not flow_conditions:
        return nodes, edges, chain_source

    condition_branches = []
    # 注意：去重应该在 extract_flow_conditions 中完成，这里作为兜底
    # 同时，只处理指向当前 workflow target 的条件
    # 去重 key 包含 target，避免相同条件但不同目标被合并
    seen_conditions: Set[Tuple[Any, Any, Any, Any, Any]] = set()
    
    for idx, cond in enumerate(flow_conditions, 1):
        comparator = cond.get("comparator")
        rhs = cond.get("rhs")
        lhs_var = cond.get("lhs_var").lower()
        cond_target_page = cond.get("target_page_id")
        cond_target_flow = cond.get("target_flow_id")

        # 跳过无效分支：无变量/无比较值/无目标
        if not lhs_var or comparator is None or rhs is None:
            continue
        
        # 只处理指向当前 workflow target 的条件（如果 filter_by_target=True）
        # 如果 filter_by_target=False，处理所有条件（用于情况2：相同 intent 不同 target）
        if filter_by_target:
            if target_page_id is not None or target_flow_id is not None:
                if cond_target_page != target_page_id or cond_target_flow != target_flow_id:
                    continue
        
        # 如果没有传入 target，但条件也没有 target，跳过
        if not cond_target_page and not cond_target_flow:
            continue
        
        # 额外过滤：空字符串、空列表、true值
        if isinstance(rhs, str) and rhs.strip() == "":
            continue
        if isinstance(rhs, list) and len(rhs) == 0:
            continue
        if rhs == "true" or rhs is True:
            continue
        
        # 规范化 rhs 用于去重（与 extract_flow_conditions 保持一致）
        # 注意：先规范化用于去重，然后再处理用于条件值
        if isinstance(rhs, list):
            # 过滤掉 None、true、"true" 等无效值，然后排序转为元组
            rhs_filtered = [v for v in rhs if v and v != "true" and v is not True]
            if not rhs_filtered:
                continue
            rhs_normalized = tuple(sorted([str(v) for v in rhs_filtered]))
            # 用于条件值的 rhs（取第一个）
            rhs_for_condition = rhs_filtered[0]
        else:
            rhs_normalized = str(rhs)
            rhs_for_condition = rhs
        
        # 去重：检查是否已经存在相同的条件（包含 target）
        condition_key = (lhs_var, rhs_normalized, comparator or "", cond_target_page, cond_target_flow)
        if condition_key in seen_conditions:
            continue
        seen_conditions.add(condition_key)

        cond_name = f"flow_condition_{idx}"

        branch = {
            "condition_id": cond_name,
            "condition_name": cond_name,
            "logical_operator": "and",
            "conditions": [],
            "condition_key": condition_key
        }
        branch["conditions"].append({
            "condition_type": "variable",
            "comparison_operator": "=" if comparator == "EQUALS" else comparator,
            "condition_value": rhs_for_condition,
            "condition_variable": lhs_var
        })
        branch["target_page_id"] = cond_target_page
        branch["target_flow_id"] = cond_target_flow
        condition_branches.append(branch)

    if condition_branches:
        condition_node_name = gen_node_name('flow_condition_start')
        # 使用列表副本，避免后续修改影响原始列表
        condition_node = {
            "type": "condition",
            "name": condition_node_name,
            "title": "Flow Start Conditions",
            "if_else_conditions": condition_branches.copy()  # 使用副本，避免引用问题
        }
        nodes.append(condition_node)
        
        # 处理汇聚连线：如果有多个源节点，每个源节点都连接到条件节点
        if converge_sources:
            for src_node in converge_sources:
                edges.append({
                    "source_node": src_node,
                    "target_node": condition_node_name,
                    "connection_type": "default"
                })
        else:
            edges.append({
                "source_node": chain_source,
                "target_node": condition_node_name,
                "connection_type": "default"
            })

        # 为每个分支创建条件边（target_page_id/target_flow_id 使用占位命名，后续解析/解析成 jump）
        for branch in condition_branches:
            branch_target_page = branch.get("target_page_id")
            branch_target_flow = branch.get("target_flow_id")
            cond_id = branch.get("condition_id")
            target_name = None
            if branch_target_node_map:
                target_name = branch_target_node_map.get(branch.get("condition_key"))
            if not target_name:
                if branch_target_page:
                    target_name = f"page_{branch_target_page[:8]}"
                elif branch_target_flow:
                    target_name = f"jump_to_flow_{branch_target_flow[:8]}"
            if not target_name:
                continue
            edges.append({
                "source_node": condition_node_name,
                "target_node": target_name,
                "connection_type": "condition",
                "condition_id": cond_id
            })
        
        # 添加 other 分支（fallback），连接到后续流程
        # other 分支应该连接到下一个节点（如果有 intent 参数提取，则连接到 intent 参数提取；否则连接到 page 流程）
        # 这里先创建一个占位符，后续在 _generate_single_intent_workflow 中会处理
        other_branch = {
            "condition_id": f"flow_condition_other_{safe_intent_name}",
            "condition_name": "Other",
            "logical_operator": "other",
            "conditions": [],
            "target_page_id": None,
            "target_flow_id": None
        }
        condition_node["if_else_conditions"].append(other_branch)
        # other 分支用于无条件 event，若提供目标节点则直接连边
        if other_target_node:
            edges.append({
                "source_node": condition_node_name,
                "target_node": other_target_node,
                "connection_type": "condition",
                "condition_id": other_branch["condition_id"]
            })
        # 注意：chain_source 不更新为 condition_node_name，因为 other 分支需要连接到后续流程

    return nodes, edges, chain_source



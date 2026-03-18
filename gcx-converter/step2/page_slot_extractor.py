"""
Page Slot Extractor - 处理 Page 层 Slots 抽取

职责：
- 处理 page 层 slots 抽取（capture → llm → parse）
- 实体提示、变量规范化等
- 封装成可复用函数，返回生成的 nodes/edges 片段及入口/出口节点

输入：
- page: page 配置字典
- page_id: page ID
- lang: 语言代码
- entity_candidates: 实体候选值映射
- gen_unique_name: 生成唯一节点名的函数
- gen_var: 生成变量名的函数

输出：
- nodes: List[Dict] - 生成的节点列表（capture, llm, code）
- edges: List[Dict] - 生成的边列表
- entry_node_name: str - 入口节点名称（如果之前没有，则为 capture 节点）
- previous_node: str - 更新后的前一个节点（用于后续连接）

注意：
- 此模块只处理 page 层级的 slots，不处理 flow 层级的 slots
- flow 层级的 slots 由 flow_start_handler.py 和 intent_chain_builder.py 处理
"""
import re
from typing import Dict, Any, List, Tuple, Optional, Callable


def _parse_regexp_to_hint(pattern: str, entity_type: str) -> str:
    """
    将正则表达式模式转换为人类可读的格式提示
    
    Args:
        pattern: 正则表达式模式（如 "^\\b\\d{6}\\b"）
        entity_type: 实体类型名称（如 "@common_6digit"）
    
    Returns:
        人类可读的格式描述
    """
    # 常见正则模式到描述的映射
    common_patterns = {
        r'\\d{6}': 'exactly 6 digits (e.g., 123456)',
        r'\\d{4}': 'exactly 4 digits (e.g., 1234)',
        r'\\d{3}': 'exactly 3 digits (e.g., 123)',
        r'\\d+': 'one or more digits',
        r'\\w+': 'one or more word characters',
        r'[a-zA-Z]+': 'one or more letters',
        r'[0-9]+': 'one or more numbers',
    }
    
    # 尝试匹配常见模式
    for regex_part, description in common_patterns.items():
        if regex_part in pattern:
            return f'format: {description}'
    
    # 根据 entity_type 名称推断格式
    entity_name = entity_type.replace('@', '').lower()
    if '6digit' in entity_name or '6_digit' in entity_name:
        return 'format: exactly 6 digits (e.g., 123456)'
    elif '4digit' in entity_name or '4_digit' in entity_name:
        return 'format: exactly 4 digits (e.g., 1234)'
    elif 'digit' in entity_name:
        return 'format: numeric digits only'
    elif 'phone' in entity_name:
        return 'format: phone number'
    elif 'email' in entity_name:
        return 'format: email address'
    elif 'date' in entity_name:
        return 'format: date'
    
    # 默认：显示正则表达式模式
    return f'format: matches pattern {pattern}'


def build_page_slot_chain(
    page: Dict[str, Any],
    page_id: str,
    lang: str,
    entity_candidates: Dict[str, Dict[str, List[str]]],
    gen_unique_name: Callable[[str, str], str],
    gen_var: Callable[[], str],
    existing_entry: Optional[str],
    previous_node: Optional[str],
    gen_variable_name: Callable[[], str] = None,
    entity_kinds: Dict[str, str] = None,  # 新增：entity kind 类型映射
    global_config: Dict[str, Any] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    根据 page 的 slots 生成抽槽链（capture → llm → code）

    Returns:
        nodes, edges, entry_node_name, previous_node (更新后的)
    """
    page_value = page.get('value', {}) if 'value' in page else page
    slots = page_value.get('slots', [])
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    entry_node_name = existing_entry

    if not slots:
        return nodes, edges, entry_node_name, previous_node

    # 收集 slot 信息
    slot_infos = []
    for slot in slots:
        display_name = slot.get('displayName', '')
        if display_name:
            slot_type = slot.get('type', {})
            entity_type = slot_type.get('className', '')
            class_type = slot_type.get('classType', '')
            slot_infos.append({
                'displayName': display_name,
                'entityType': entity_type,
                'classType': class_type
            })

    slot_names = [s['displayName'] for s in slot_infos]
    if not slot_names:
        return nodes, edges, entry_node_name, previous_node

    print(f"  - Found {len(slot_names)} slots: {', '.join(slot_names)}")

    # capture
    capture_node_name = gen_unique_name('capture_slot', page_id)
    capture_node = {
        "type": "captureUserReply",
        "name": capture_node_name,
        "title": "Capture Slot Parameters",
        "variable_assign": "last_user_response",
        "variables": slot_names
    }
    nodes.append(capture_node)

    # 如果还没有 entry node，capture 就是 entry
    if entry_node_name is None:
        entry_node_name = capture_node_name

    # 连接 previous -> capture
    if previous_node:
        edges.append({
            "source_node": previous_node,
            "target_node": capture_node_name,
            "connection_type": "default"
        })
    previous_node = capture_node_name

    # LLM 节点
    llm_variable = gen_var()
    llm_node_name = gen_unique_name('llm_extract_slot', page_id)

    # 构建 Entity Type hints
    hint_lines = []
    if entity_kinds is None:
        entity_kinds = {}
    
    for slot_info in slot_infos:
        slot_name = slot_info['displayName']
        entity_type = slot_info['entityType']
        class_type = slot_info['classType']
        normalized_name = slot_name.replace('-', '_')

        if entity_type and class_type == 'ENUMERATION':
            # entity_type 是 className，需要加上 @ 前缀来匹配 entity_candidates 的 key
            # 注意：className 可能已经带 @ 前缀（如 @common_6digit），需要检查避免重复添加
            entity_key = f"@{entity_type}" if not entity_type.startswith('@') else entity_type
            
            # 检查 entity 的 kind 类型
            entity_kind = entity_kinds.get(entity_key, 'KIND_MAP')
            
            if entity_kind == 'KIND_REGEXP':
                # 正则表达式类型：生成格式提示，而不是候选值
                # 从 entity_candidates 中获取正则表达式模式
                regexp_patterns = entity_candidates.get(entity_key, {}).get(lang, [])
                if regexp_patterns:
                    # 解析正则表达式，生成人类可读的格式描述
                    pattern = regexp_patterns[0] if regexp_patterns else ''
                    format_hint = _parse_regexp_to_hint(pattern, entity_type)
                    hint_lines.append(f'- {normalized_name}: {format_hint}')
                    print(f"    - {normalized_name}: regexp entity ({entity_type}), pattern: {pattern}")
                else:
                    print(f"    - {normalized_name}: regexp entity ({entity_type}), no pattern found")
            else:
                # KIND_MAP 或其他类型：使用候选值
                lang_vals = entity_candidates.get(entity_key, {}).get(lang, [])
                # 如果没找到，尝试不带 @ 的 key
                if not lang_vals:
                    lang_vals = entity_candidates.get(entity_type, {}).get(lang, [])
                if lang_vals:
                    hint_lines.append(f'- {normalized_name}: allowed values ({lang}) = ' + ", ".join(lang_vals))
                    print(f"    - {normalized_name}: found {len(lang_vals)} candidate values from {entity_type}")
                else:
                    print(f"    - {normalized_name}: no candidate values found for {entity_type}")
        elif entity_type and class_type == 'BUILT_IN_CLASS':
            print(f"    - {normalized_name}: built-in entity type {entity_type}, no value restriction")

    hint_text = ''
    if hint_lines:
        hint_text = '\n##Hints (Use one of the allowed values for each parameter)\n' + "\n".join(hint_lines) + '\n'

    slot_list = "\n".join([f"- {s.replace('-', '_')}" for s in slot_names])
    output_template = "{\n"
    for slot_name in slot_names:
        normalized_name = slot_name.replace('-', '_')
        output_template += f'  "{normalized_name}": "",\n'
    output_template += "}"

    prompt = f'''#Role
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

    llm_node = {
        "type": "llmVariableAssignment",
        "name": llm_node_name,
        "title": f"Extract Slot Parameters",
        "prompt_template": prompt,
        "variable_assign": llm_variable,
        "llm_name": global_config.get("llmcodemodel", "azure-gpt-4o"),
        "chat_history_flag": global_config.get("enable_short_memory", False),
        "chat_history_count": global_config.get("short_chat_count", 5)
    }
    nodes.append(llm_node)

    edges.append({
        "source_node": capture_node_name,
        "target_node": llm_node_name,
        "connection_type": "default"
    })

    # CODE 节点
    code_node_name = gen_unique_name('parse_slot', page_id)

    normalized_slots = [s.replace('-', '_') for s in slot_names]
    return_dict = ",\n".join([f'        "{v}": data.get("{v}", "")' for v in normalized_slots])

    parse_code = f'''import json
import re

def main({llm_variable}) -> dict:
    match = re.search(r'([{{].*?[}}])', {llm_variable}, re.DOTALL)
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

    code_node = {
        "type": "code",
        "name": code_node_name,
        "title": "Parse Slot Parameters",
        "code": parse_code,
        "outputs": normalized_slots,
        "args": [llm_variable]
    }
    nodes.append(code_node)

    edges.append({
        "source_node": llm_node_name,
        "target_node": code_node_name,
        "connection_type": "default"
    })

    previous_node = code_node_name
    print(f"  - Generated slot extraction flow: capture → llm → code")
    print(f"    Extracted variables: {', '.join(normalized_slots)}")

    return nodes, edges, entry_node_name, previous_node



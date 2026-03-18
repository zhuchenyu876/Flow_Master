"""
Page Processor - 处理页面级别的节点生成

职责：
- 解析 page 中的 responses，生成 text 节点
- 解析 page 中的 setParameterActions，生成 code 节点
- 解析 page 中的 transitionEvents，生成意图识别和条件判断节点
- 生成 jump 节点（用于页面层级的跳转）
- 生成全局 transition 节点
- 处理页面层级的各种逻辑

注意：
- 此模块负责处理单个 page 的节点生成
- 实际的 workflow 组装由 intent_workflow_builder.py 负责
"""

from typing import Dict, List, Any, Tuple, Callable, Optional, Set
import re
import json
from logger_config import get_logger, is_verbose
from step2.parse_expressions import parse_dialogflow_value

logger = get_logger(__name__)
VERBOSE = is_verbose()

def vprint(*args, **kwargs):
    """只在 VERBOSE 模式下才输出到日志的调试信息"""
    if VERBOSE:
        try:
            message = " ".join(str(a) for a in args)
        except Exception:
            message = " ".join(map(str, args))
        logger.debug(message)

if not VERBOSE:
    def print(*args, **kwargs):  # type: ignore[override]
        try:
            message = " ".join(str(a) for a in args)
        except Exception:
            message = " ".join(map(str, args))
        logger.debug(message)

# write by senlin.deng 2026-01-19
# 解析复杂表达式，支持AND和OR条件，在生成复杂条件的code节点时使用
def parse_mixed_and_or_condition(condition_string: str) -> Dict[str, Any]:
    """
    Parse mixed AND + OR conditions like:
    $page.params.status = "FINAL" AND ($session.params.PageInput = "x" OR $session.params.PageInput = "y" OR ...)
    
    Returns:
        {
            "and_conditions": [{"variable": ..., "operator": ..., "value": ..., "param_type": "page"|"session"}, ...],
            "or_group": {
                "variable": ...,
                "param_type": "page"|"session",
                "values": [...]
            }
        }
    """
    try:
        # 处理简单的 "true" 或 "false" 条件
        condition_stripped = condition_string.strip().lower()
        if condition_stripped == 'true':
            return {
                "and_conditions": [],
                "or_group": None,
                "raw_condition": condition_string,
                "is_literal": True,
                "literal_value": True
            }
        elif condition_stripped == 'false':
            return {
                "and_conditions": [],
                "or_group": None,
                "raw_condition": condition_string,
                "is_literal": True,
                "literal_value": False
            }
        
        # 提取括号内的 OR 部分
        or_match = re.search(r'\(([^)]+)\)', condition_string)
        if not or_match:
            return None
        
        or_part = or_match.group(1)
        # 获取 AND 部分（括号外的部分）
        and_part = condition_string[:or_match.start()].strip()
        if and_part.endswith(' AND'):
            and_part = and_part[:-4].strip()
        
        # 解析 AND 条件（括号外的部分）
        # 支持 $page.params.xxx 和 $session.params.xxx
        and_conditions = []
        and_parts = and_part.split(' AND ')
        
        for part in and_parts:
            part = part.strip()
            if not part:
                continue
            # 匹配 $page.params.xxx 或 $session.params.xxx
            match = re.match(
                r'\$(page|session)\.params\.([a-zA-Z0-9_-]+)\s*(!=|=|>|<|>=|<=)\s*("([^"]*)"|null|true|false|(\S+))',
                part
            )
            if match:
                param_type = match.group(1)  # "page" 或 "session"
                var_name = match.group(2).replace('-', '_')
                operator = match.group(3)
                value_raw = match.group(5) if match.group(5) is not None else (match.group(6) if match.group(6) else match.group(4))
                value = normalize_condition_value(value_raw)
                
                operator_mapping = {'=': '=', '!=': '≠', '>': '>', '<': '<', '>=': '≥', '<=': '≤'}
                mapped_operator = operator_mapping.get(operator, operator)
                
                and_conditions.append({
                    'variable': var_name,
                    'operator': mapped_operator,
                    'value': value,
                    'param_type': param_type
                })
        
        # 解析 OR 条件（括号内的部分）
        or_parts = or_part.split(' OR ')
        or_conditions = []  # 改为存储完整的条件对象
        or_variable = None
        or_param_type = None
        or_operator = None  # 新增：记录 OR 组的操作符
        
        for part in or_parts:
            part = part.strip()
            # 匹配 $page.params.xxx 或 $session.params.xxx
            match = re.match(
                r'\$(page|session)\.params\.([a-zA-Z0-9_-]+)\s*(!=|=|>|<|>=|<=)\s*("([^"]*)"|null|true|false|(\S+))',
                part
            )
            if match:
                param_type = match.group(1)
                var_name = match.group(2).replace('-', '_')
                operator = match.group(3)
                value_raw = match.group(5) if match.group(5) is not None else (match.group(6) if match.group(6) else match.group(4))
                value = normalize_condition_value(value_raw)
                
                operator_mapping = {'=': '=', '!=': '≠', '>': '>', '<': '<', '>=': '≥', '<=': '≤'}
                mapped_operator = operator_mapping.get(operator, operator)
                
                if or_variable is None:
                    or_variable = var_name
                    or_param_type = param_type
                    or_operator = mapped_operator  # 记录第一个条件的操作符
                
                or_conditions.append({
                    'value': value,
                    'operator': mapped_operator
                })
        
        if not and_conditions and not or_conditions:
            return None
        
        # 为了向后兼容，同时提供 values 列表和完整的 conditions 列表
        or_values = [c['value'] for c in or_conditions] if or_conditions else []
        
        return {
            "and_conditions": and_conditions,
            "or_group": {
                "variable": or_variable,
                "param_type": or_param_type,
                "operator": or_operator,  # 新增：OR 组的操作符（第一个条件的操作符）
                "values": or_values,  # 向后兼容：只包含值的列表
                "conditions": or_conditions  # 新增：包含完整条件信息的列表
            } if or_conditions else None,
            "raw_condition": condition_string
        }
    except Exception as e:
        logger.error(f"Failed to parse mixed AND+OR condition: {condition_string}, error: {e}")
        return None

def generate_mixed_condition_code_node(
    mixed_condition: Dict[str, Any],
    page_id: str,
    gen_unique_node_name: Callable[[str, str], str],
    condition_index: int = 0
) -> Tuple[Dict[str, Any], str]:
    """
    Generate a code node for mixed AND + OR condition evaluation.
    
    Args:
        mixed_condition: Parsed mixed condition from parse_mixed_and_or_condition
        page_id: Page ID for node naming
        gen_unique_node_name: Function to generate unique node names
        condition_index: Index for unique variable naming (default 0)
        
    Returns:
        (code_node, output_variable_name)
    """
    node_name = gen_unique_node_name('mixed_condition_check', page_id)
    # 使用带索引的变量名，避免多个混合条件时变量名冲突
    output_var = f"condition_result_{condition_index}" if condition_index > 0 else "condition_result"
    
    # 处理字面量条件（true/false）
    if mixed_condition.get('is_literal'):
        literal_value = mixed_condition.get('literal_value', True)
        # 生成 if True/False: 格式，与其他条件保持一致
        code_lines = [
            "def main() -> dict:",
            f"    '''Literal condition: {mixed_condition.get('raw_condition', '')}'''",
            f"    if {literal_value}:",
            f"        return {{'{output_var}': {literal_value}}}"
        ]
        code_node = {
            "type": "code",
            "name": node_name,
            "title": f"Literal Condition ({literal_value})",
            "code": "\n".join(code_lines),
            "outputs": [output_var],
            "args": [],
            "variable_assign": output_var
        }
        return code_node, output_var
    
    # 收集所有需要的输入变量
    input_variables = []
    and_conditions = mixed_condition.get('and_conditions', [])
    or_group = mixed_condition.get('or_group')
    
    # 从 AND 条件中收集变量
    for cond in and_conditions:
        var_name = cond['variable'].lower()
        if var_name not in input_variables:
            input_variables.append(var_name)
    
    # 从 OR 组中收集变量
    if or_group and or_group.get('variable'):
        var_name = or_group['variable'].lower()
        if var_name not in input_variables:
            input_variables.append(var_name)
    
    # 构建代码
    code_lines = []
    code_lines.append(f"def main({', '.join(input_variables)}) -> dict:")
    code_lines.append("    '''")
    code_lines.append(f"    Evaluate mixed AND + OR condition:")
    code_lines.append(f"    {mixed_condition.get('raw_condition', '')}")
    code_lines.append("    '''")
    
    # 生成 AND 条件检查
    and_checks = []
    for cond in and_conditions:
        var_name = cond['variable'].lower()
        operator = cond['operator']
        value = cond['value']
        
        # 处理不同的运算符
        if operator == '=' or operator == '==':
            if isinstance(value, str):
                and_checks.append(f'{var_name} == "{value}"')
            else:
                and_checks.append(f'{var_name} == {value}')
        elif operator == '≠' or operator == '!=':
            if isinstance(value, str):
                and_checks.append(f'{var_name} != "{value}"')
            else:
                and_checks.append(f'{var_name} != {value}')
        elif operator == '>':
            and_checks.append(f'{var_name} > {value}')
        elif operator == '<':
            and_checks.append(f'{var_name} < {value}')
        elif operator == '≥' or operator == '>=':
            and_checks.append(f'{var_name} >= {value}')
        elif operator == '≤' or operator == '<=':
            and_checks.append(f'{var_name} <= {value}')
    
    # 生成 OR 组检查
    or_check = ""
    if or_group and or_group.get('values'):
        var_name = or_group['variable'].lower()
        values = or_group['values']
        or_operator = or_group.get('operator', '=')  # 获取 OR 组的操作符
        
        # 根据操作符生成不同的检查逻辑
        if or_operator == '≠' or or_operator == '!=':
            # != 操作符：a != x OR a != y 等价于 a not in [x, y] 只有当所有值都不等时才为 True
            # 但如果是 OR 关系，只要有一个 != 成立就为 True，这意味着只要值不是所有 values 都满足
            # 实际上 a != x OR a != y 在逻辑上几乎总是 True（除非 x == y）
            # 所以这里应该理解为：变量不在这些值中的任何一个
            # 使用 not in 来检查
            values_str = ', '.join([f'"{v}"' if isinstance(v, str) else str(v) for v in values])
            or_check = f'{var_name} not in [{values_str}]'
        else:
            # = 操作符或其他：使用 in 来简化多值检查
            values_str = ', '.join([f'"{v}"' if isinstance(v, str) else str(v) for v in values])
            or_check = f'{var_name} in [{values_str}]'
    
    # 组合最终条件
    if and_checks and or_check:
        # AND conditions AND (OR group)
        and_part = ' and '.join(and_checks)
        code_lines.append(f"    and_result = {and_part}")
        code_lines.append(f"    or_result = {or_check}")
        code_lines.append(f"    result = and_result and or_result")
    elif and_checks:
        and_part = ' and '.join(and_checks)
        code_lines.append(f"    result = {and_part}")
    elif or_check:
        code_lines.append(f"    result = {or_check}")
    else:
        code_lines.append("    result = True")
    
    code_lines.append(f"    return {{'{output_var}': result}}")
    
    code_node = {
        "type": "code",
        "name": node_name,
        "title": "Mixed Condition Check (AND + OR)",
        "code": "\n".join(code_lines),
        "outputs": [output_var],
        "args": input_variables,
        "variable_assign": output_var
    }
    
    return code_node, output_var

# 生成复杂条件的code节点，在生成复杂条件的code节点时使用
def generate_combined_mixed_condition_code_node(
    mixed_conditions_with_index: List[Tuple[int, Dict[str, Any]]],
    page_id: str,
    gen_unique_node_name: Callable[[str, str], str]
) -> Tuple[Dict[str, Any], str, Dict[int, str]]:
    """
    Generate a single code node that evaluates multiple mixed AND + OR conditions.
    Returns the index of the first matching condition as a string ("1", "2", "3", ...).
    
    Args:
        mixed_conditions_with_index: List of (branch_index, mixed_condition) tuples
        page_id: Page ID for node naming
        gen_unique_node_name: Function to generate unique node names
        
    Returns:
        (code_node, output_variable_name, index_to_condition_value_map)
        index_to_condition_value_map: {branch_index: "1", "2", ...} for condition node to use
    """
    node_name = gen_unique_node_name('combined_mixed_condition', page_id)
    output_var = "mixed_condition_result"
    
    # 收集所有需要的输入变量（跳过字面量条件）
    all_input_variables = set()
    
    for _, mixed_condition in mixed_conditions_with_index:
        # 跳过字面量条件（true/false），它们不需要输入变量
        if mixed_condition.get('is_literal'):
            continue
            
        and_conditions = mixed_condition.get('and_conditions', [])
        or_group = mixed_condition.get('or_group')
        
        # 从 AND 条件中收集变量
        for cond in and_conditions:
            var_name = cond['variable'].lower()
            all_input_variables.add(var_name)
        
        # 从 OR 组中收集变量
        if or_group and or_group.get('variable'):
            var_name = or_group['variable'].lower()
            all_input_variables.add(var_name)
    
    input_variables = sorted(list(all_input_variables))
    
    # 构建代码
    code_lines = []
    if input_variables:
        code_lines.append(f"def main({', '.join(input_variables)}) -> dict:")
    else:
        code_lines.append("def main() -> dict:")
    code_lines.append("    '''")
    code_lines.append("    Evaluate multiple mixed AND + OR conditions.")
    code_lines.append("    Returns the index of the first matching condition.")
    code_lines.append("    '''")
    
    # 创建索引映射
    index_to_condition_value = {}
    
    # 为每个混合条件生成检查代码
    for result_idx, (branch_index, mixed_condition) in enumerate(mixed_conditions_with_index, 1):
        condition_value = str(result_idx)  # "1", "2", "3", ...
        index_to_condition_value[branch_index] = condition_value
        
        # 处理字面量条件（true/false）
        if mixed_condition.get('is_literal'):
            literal_value = mixed_condition.get('literal_value', True)
            code_lines.append(f"    # Condition {result_idx}: Literal {literal_value}")
            if literal_value:
                # 生成 if True: 语句，与其他条件保持一致的格式
                code_lines.append(f"    if True:")
                code_lines.append(f'        return {{"{output_var}": "{condition_value}"}}')
            else:
                # false 条件：生成 if False: 语句（永远不会执行）
                code_lines.append(f"    if False:")
                code_lines.append(f'        return {{"{output_var}": "{condition_value}"}}')
            continue
        
        and_conditions = mixed_condition.get('and_conditions', [])
        or_group = mixed_condition.get('or_group')
        
        # 生成 AND 条件检查
        and_checks = []
        for cond in and_conditions:
            var_name = cond['variable'].lower()
            operator = cond['operator']
            value = cond['value']
            
            if operator == '=' or operator == '==':
                if isinstance(value, str):
                    and_checks.append(f'{var_name} == "{value}"')
                else:
                    and_checks.append(f'{var_name} == {value}')
            elif operator == '≠' or operator == '!=':
                if isinstance(value, str):
                    and_checks.append(f'{var_name} != "{value}"')
                else:
                    and_checks.append(f'{var_name} != {value}')
            elif operator == '>':
                and_checks.append(f'{var_name} > {value}')
            elif operator == '<':
                and_checks.append(f'{var_name} < {value}')
            elif operator == '≥' or operator == '>=':
                and_checks.append(f'{var_name} >= {value}')
            elif operator == '≤' or operator == '<=':
                and_checks.append(f'{var_name} <= {value}')
        
        # 生成 OR 组检查（支持 != 操作符）
        or_check = ""
        if or_group and or_group.get('values'):
            var_name = or_group['variable'].lower()
            values = or_group['values']
            or_operator = or_group.get('operator', '=')  # 获取 OR 组的操作符
            values_str = ', '.join([f'"{v}"' if isinstance(v, str) else str(v) for v in values])
            
            # 根据操作符生成不同的检查逻辑
            if or_operator == '≠' or or_operator == '!=':
                or_check = f'{var_name} not in [{values_str}]'
            else:
                or_check = f'{var_name} in [{values_str}]'
        
        # 组合条件
        if and_checks and or_check:
            full_condition = f"({' and '.join(and_checks)}) and ({or_check})"
        elif and_checks:
            full_condition = ' and '.join(and_checks)
        elif or_check:
            full_condition = or_check
        else:
            full_condition = "True"
        
        # 添加条件判断
        code_lines.append(f"    # Condition {result_idx}: {mixed_condition.get('raw_condition', '')[:50]}...")
        code_lines.append(f"    if {full_condition}:")
        code_lines.append(f'        return {{"{output_var}": "{condition_value}"}}')
    
    # 默认返回 "0" 表示没有匹配
    code_lines.append(f"    # No condition matched")
    code_lines.append(f'    return {{"{output_var}": "0"}}')
    
    code_node = {
        "type": "code",
        "name": node_name,
        "title": "Combined Mixed Condition Check",
        "code": "\n".join(code_lines),
        "outputs": [output_var],
        "args": input_variables,
        "variable_assign": output_var
    }
    
    return code_node, output_var, index_to_condition_value


# write by senlin.deng 2026-01-15
# 修复：responses中可能包含表达式code节点，需要先解析出来。
def extract_payload_expressions(payload: Any, input_variables: List[str]) -> Tuple[List[Tuple[str, str]], List[str]]:
    """
    递归提取 payload 中的 Dialogflow 表达式
    
    Args:
        payload: payload 数据（可能是 dict、list、str 或其他类型）
        input_variables: 已收集的输入变量列表
        
    Returns:
        (表达式列表[(变量名, 表达式代码)], 更新后的输入变量列表)
    """
    expressions = []
    
    if isinstance(payload, dict):
        for key, val in payload.items():
            sub_exprs, input_variables = extract_payload_expressions(val, input_variables)
            expressions.extend(sub_exprs)
    
    elif isinstance(payload, list):
        for item in payload:
            sub_exprs, input_variables = extract_payload_expressions(item, input_variables)
            expressions.extend(sub_exprs)
    
    elif isinstance(payload, str):
        # 查找 $sys.func.XXX(...) 系统函数表达式
        i = 0
        while i < len(payload):
            sys_func_pos = payload.find('$sys.func.', i)
            if sys_func_pos == -1:
                break
            
            # 查找函数名
            func_start = sys_func_pos + len('$sys.func.')
            func_name_end = payload.find('(', func_start)
            
            if func_name_end == -1:
                i = sys_func_pos + 1
                continue
            
            func_name = payload[func_start:func_name_end]
            
            # 查找匹配的右括号
            paren_start = func_name_end
            paren_depth = 1
            j = paren_start + 1
            
            while j < len(payload) and paren_depth > 0:
                if payload[j] == '(':
                    paren_depth += 1
                elif payload[j] == ')':
                    paren_depth -= 1
                j += 1
            
            if paren_depth == 0:
                # 找到了完整的表达式
                expr = payload[sys_func_pos:j]
                
                # 使用 parse_dialogflow_value 解析表达式
                try:
                    code, input_variables = parse_dialogflow_value(expr, input_variables)
                    
                    # 为这个表达式生成一个输出变量名
                    # 从 input_variables 中提取最后一个作为输出变量名的基础
                    if input_variables:
                        if func_name == 'GET_FIELD' and len(input_variables) >= 2:
                            output_var = input_variables[-1]
                        else:
                            output_var = input_variables[-1]
                    else:
                        output_var = f"expr_result_{len(expressions)}"
                    
                    expressions.append((output_var, code))
                except Exception as e:
                    logger.debug(f"Failed to parse expression {expr}: {e}")
                
                i = j
            else:
                i = sys_func_pos + 1
    
    return expressions, input_variables

# write by senlin.deng 2026-01-15
# 修复：responses中可能包含表达式code节点，需要先解析出来。
def parse_responses(
    page: Dict[str, Any], 
    lang: str, 
    gen_unique_node_name: Callable[[str, str], str]
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    解析 page 中的 responses，生成 code 节点（如果有表达式）和 text 节点
    
    Args:
        page: page配置字典
        lang: 语言代码（en, zh-cn, zh-hant）
        gen_unique_node_name: 生成唯一节点名的函数
        
    Returns:
        (code节点或None, text节点列表)
    """
    text_nodes = []
    all_expressions = []  # 收集所有表达式
    all_input_variables = []  # 收集所有输入变量
    
    # 获取 onLoad (支持两种数据结构)
    if 'value' in page:
        on_load = page.get('value', {}).get('onLoad', {})
    else:
        on_load = page.get('onLoad', {})
    
    # 支持两种 response 结构：
    # 1. staticUserResponse.candidates (原始导出格式)
    # 2. 直接的 responses 数组 (step1 处理后的格式)
    responses_to_process = []
    
    if 'staticUserResponse' in on_load:
        # 格式1：有 staticUserResponse.candidates
        static_response = on_load.get('staticUserResponse', {})
        candidates = static_response.get('candidates', [])
        
        # 筛选指定语言的responses
        for candidate in candidates:
            selector = candidate.get('selector', {})
            response_lang = selector.get('lang', '')
            
            # 只处理匹配语言的responses
            if response_lang == lang:
                responses_to_process.extend(candidate.get('responses', []))
    elif 'responses' in on_load:
        # 格式2：step1 处理后，直接在 onLoad 下有 responses
        responses_to_process = on_load.get('responses', [])
    
    # 处理所有 responses
    for response in responses_to_process:
        # step1格式可能直接是payload内容，或者有payload字段
        if 'payload' in response:
            payload = response.get('payload', {})
        else:
            # step1处理后可能直接就是payload内容
            payload = response
        
        # 跳过空payload
        if not payload:
            continue
        
        # 提取 payload 中的表达式（在转换之前）
        if not isinstance(payload, str):
            exprs, all_input_variables = extract_payload_expressions(payload, all_input_variables)
            all_expressions.extend(exprs)

        # 生成唯一的节点名
        page_id = page.get('key') or page.get('pageId', '')
        node_name = gen_unique_node_name('text_node', page_id)
        
        # 获取 displayName
        display_name = page.get('value', {}).get('displayName') or page.get('displayName', '')
        
        # 保存完整的payload内容（包括所有字段：text, type, buttons, urls等）
        # 1. 先转换变量引用：$session.params.xxx → {{xxx}}
        # 2. 再将 payload 转换为json字符串，确保中文不乱码
        # write by senlin.deng 2025-12-29, 2025-12-30
        if not isinstance(payload, str):
            # 转换 payload 中的变量引用为模板格式
            payload = convert_payload_variables(payload)
            payload = json.dumps(payload, ensure_ascii=False)
        text_node = {
            "type": "textReply",
            "name": node_name,
            "title": f"Response_{display_name}",
            "payload": payload  # 完整的payload对象，例如：{"text": "...", "type": "message"}
        }
        
        # 同时保留plain_text格式以兼容workflow_generator
        plain_text_items = [
            {
                "text": payload,
                "id": node_name
            }
        ]

        text_node["plain_text"] = plain_text_items
        text_nodes.append(text_node)
    
    # write by senlin.deng 2026-01-15
    # 修复：responses中可能包含表达式code节点，需要先解析出来。
    # 如果有表达式，生成 code 节点
    code_node = None
    if all_expressions:
        # 去重表达式（按变量名去重）
        seen_vars = set()
        unique_expressions = []
        for var_name, code in all_expressions:
            if var_name not in seen_vars:
                seen_vars.add(var_name)
                unique_expressions.append((var_name, code))
        
        # 生成代码行
        code_lines = [f"{var_name} = {code}" for var_name, code in unique_expressions]
        output_variables = [var_name for var_name, _ in unique_expressions]
        # write by senlin.deng 2026-01-29
        # 计算需要作为输入参数的变量
        # 注意：某些变量可能同时是输入和输出（如 GET_FIELD 的情况）
        # 需要检查代码右侧是否引用了输出变量
        code_str = "\n".join(code_lines)
        args_set = set(all_input_variables)
        for out_var in output_variables:
            # 如果输出变量在代码右侧被引用（如 .get( 或 [ 操作），则保留为输入参数
            # 检查模式：变量名后面跟着 .get( 或 [
            if f"{out_var}.get(" in code_str or f"{out_var}[" in code_str:
                # 该变量在赋值右侧被引用，需要保留为输入参数
                pass
            else:
                # 该变量仅作为输出，从输入参数中移除
                args_set.discard(out_var)

        # 生成 code 节点
        page_id = page.get('key') or page.get('pageId', '')
        node_name = gen_unique_node_name('response_code', page_id)
        display_name = page.get('value', {}).get('displayName') or page.get('displayName', '')
        
        code_node = {
            "type": "code",
            "name": node_name,
            "title": f"ExpressionEval_{display_name}",
            "code": "\n".join(code_lines),
            "outputs": output_variables,
            "args": list(args_set)  # 输入变量去除输出变量
        }
        
        vprint(f"  - Generated response code node with {len(unique_expressions)} expressions")
        
    return code_node, text_nodes


def parse_parameter_actions(
    page: Dict[str, Any],
    gen_unique_node_name: Callable[[str, str], str]
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    解析 page 中的 setParameterActions，生成 code 节点
    
    Args:
        page: page配置字典
        gen_unique_node_name: 生成唯一节点名的函数
        
    Returns:
        (code节点, 输出变量列表)
    """
    # 支持两种格式：'value.onLoad' (转换后的格式) 和 'onLoad' (原始格式)
    on_load = page.get('value', {}).get('onLoad', {}) if 'value' in page else page.get('onLoad', {})
    parameter_actions = on_load.get('setParameterActions', [])
    
    if not parameter_actions:
        return None, []
    
    # 生成变量赋值代码
    code_lines = []
    output_variables = []
    input_variables = []  # 收集输入变量（从$引用中提取）
    
    for action in parameter_actions:
        parameter = action.get('parameter', '')
        value = action.get('value')  # 注意：不设默认值，保留 None
        
        # 使用通用解析函数处理值
        # 支持：变量引用、系统函数（如 GET_FIELD）、对象、null、数字、字符串等
        value_code, input_variables = parse_dialogflow_value(value, input_variables)
        code_lines.append(f"{parameter} = {value_code}")
        
        output_variables.append(parameter)
    
    # 生成code节点
    # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
    page_id = page.get('key') or page.get('pageId', '')
    node_name = gen_unique_node_name('code_node', page_id)
    
    code_node = {
        "type": "code",
        "name": node_name,
        "title": f"VariableAssignment_{page.get('value', {}).get('displayName', '') or page.get('displayName', '')}",
        "code": "\n".join(code_lines),
        "outputs": output_variables,
        "args": input_variables  # 添加输入变量
    }
    
    return code_node, output_variables


def collect_related_pages(
    page_id: str,
    page_id_map: Dict[str, Any],
    collected: Set[str],
    max_depth: int = 10,
    current_depth: int = 0
):
    """
    递归收集与该 page 相关的所有 pages（通过 transitionEvents 跳转）
    
    Args:
        page_id: 起始 page ID
        page_id_map: page_id 到 page 数据的映射
        collected: 已收集的 page ID 集合（会被修改）
        max_depth: 最大递归深度
        current_depth: 当前递归深度
    """
    if current_depth >= max_depth:
        return
    
    if page_id in collected:
        return
    
    collected.add(page_id)
    
    # 获取 page 数据
    page_data = page_id_map.get(page_id)
    if not page_data:
        # 尝试使用前8位匹配（page_id_map 的 key 可能是完整 UUID，而传入的可能是部分）
        page_id_prefix = page_id[:8] if len(page_id) >= 8 else page_id
        matching_pages = [pid for pid in page_id_map.keys() if pid[:8] == page_id_prefix]
        if matching_pages:
            actual_page_id = matching_pages[0]
            if actual_page_id not in collected:
                print(f'   [DEBUG] Using prefix match: {page_id[:8]}... -> {actual_page_id[:8]}...')
                collected.add(actual_page_id)
                page_data = page_id_map.get(actual_page_id)
                page_id = actual_page_id  # 更新 page_id 用于后续处理
            else:
                return
        else:
            print(f'   [DEBUG] Page {page_id[:8]}... not found in page_id_map (total pages: {len(page_id_map)})')
            return
    
    # 解析该 page 的 transitionEvents（支持两种数据结构）
    if 'value' in page_data:
        transition_events = page_data.get('value', {}).get('transitionEvents', [])
    else:
        transition_events = page_data.get('transitionEvents', [])
    
    for event in transition_events:
        handler = event.get('transitionEventHandler', {})
        target_page = handler.get('targetPageId')
        
        if target_page and target_page not in collected:
            collect_related_pages(
                target_page, page_id_map, collected, max_depth, current_depth + 1
            )

# write by senlin.deng 2026-01-14
# 解析 beforeTransition 中的 staticUserResponse，生成 text 节点
def parse_before_transition_responses(
    before_transition: Dict[str, Any],
    lang: str,
    gen_unique_node_name: Callable[[str, str], str],
    transition_id: str = ""
) -> List[Dict[str, Any]]:
    """
    解析 beforeTransition 中的 staticUserResponse，生成 text 节点
    复用 parse_responses 的核心逻辑
    
    Args:
        before_transition: beforeTransition 配置字典
        lang: 语言代码（en, zh-cn, zh-hant）
        gen_unique_node_name: 生成唯一节点名的函数
        transition_id: transition 的唯一标识（用于生成节点名）
        
    Returns:
        text节点列表
    """
    text_nodes = []
    
    # 支持两种 response 结构：
    # 1. staticUserResponse.candidates (原始导出格式)
    # 2. 直接的 responses 数组 (step1 处理后的格式)
    responses_to_process = []
    
    if 'staticUserResponse' in before_transition:
        # 格式1：有 staticUserResponse.candidates
        static_response = before_transition.get('staticUserResponse', {})
        candidates = static_response.get('candidates', [])
        
        # 筛选指定语言的responses
        for candidate in candidates:
            selector = candidate.get('selector', {})
            response_lang = selector.get('lang', '')
            
            # 只处理匹配语言的responses
            if response_lang == lang:
                responses_to_process.extend(candidate.get('responses', []))
    elif 'responses' in before_transition:
        # 格式2：step1 处理后，直接在 beforeTransition 下有 responses
        responses_to_process = before_transition.get('responses', [])
    
    # 处理所有 responses
    for response in responses_to_process:
        # step1格式可能直接是payload内容，或者有payload字段
        if 'payload' in response:
            payload = response.get('payload', {})
        else:
            # step1处理后可能直接就是payload内容
            payload = response
        
        # 跳过空payload
        if not payload:
            continue
        
        # 生成唯一的节点名（使用 transition_id 作为后缀）
        node_name = gen_unique_node_name('before_transition_text', transition_id)
        
        # 保存完整的payload内容（包括所有字段：text, type, buttons, urls等）
        # 1. 先转换变量引用：$session.params.xxx → {{xxx}}
        # 2. 再将 payload 转换为json字符串，确保中文不乱码
        if not isinstance(payload, str):
            # 转换 payload 中的变量引用为模板格式
            payload = convert_payload_variables(payload)
            payload = json.dumps(payload, ensure_ascii=False)
        
        text_node = {
            "type": "textReply",
            "name": node_name,
            "title": f"BeforeTransition_Response",
            "payload": payload  # 完整的payload对象，例如：{"text": "...", "type": "message"}
        }
        
        # 同时保留plain_text格式以兼容workflow_generator
        plain_text_items = [
            {
                "text": payload,
                "id": node_name
            }
        ]

        text_node["plain_text"] = plain_text_items
        text_nodes.append(text_node)
    
    return text_nodes


# write by senlin.deng 2026-01-14
# 将 payload 中的特定变量引用转换为模板格式
def convert_payload_variables(payload: Any) -> Any:
    """
    递归转换 payload 中的 Dialogflow 表达式为模板格式
    
    支持所有 parse_dialogflow_value 支持的表达式类型：
    - 系统函数：$sys.func.XXX(...) → {{variable_name}}
    - 变量引用：$session.params.xxx → {{xxx}}
    - 特例：$session.params.delay → {{delay}}
    
    Args:
        payload: payload 数据（可能是 dict、list、str 或其他类型）
        
    Returns:
        转换后的 payload
    """
    if isinstance(payload, dict):
        # 递归处理字典中的每个值
        return {key: convert_payload_variables(val) for key, val in payload.items()}
    
    elif isinstance(payload, list):
        # 递归处理列表中的每个元素
        return [convert_payload_variables(item) for item in payload]
    
    elif isinstance(payload, str):
        # 处理 $session.params.delay 这个特例
        if payload == '$session.params.delay':
            return '{{delay}}'
        
        # 处理字符串中的所有 Dialogflow 表达式
        # 包括：$sys.func.XXX(...) 和 $xxx 变量引用
        def find_and_replace_expressions(text):
            """在文本中查找并替换所有 Dialogflow 表达式"""
            result_parts = []
            i = 0
            
            while i < len(text):
                # 查找 $sys.func. 开头的系统函数表达式
                sys_func_pos = text.find('$sys.func.', i)
                
                # 查找 $ 开头的变量引用（但不包括 $sys.func.）
                var_ref_pos = text.find('$', i)
                if var_ref_pos != -1 and var_ref_pos + 10 <= len(text) and text[var_ref_pos:var_ref_pos+10] == '$sys.func.':
                    # 这是系统函数，跳过这个 $，继续查找下一个变量引用
                    var_ref_pos = text.find('$', var_ref_pos + 10)
                
                # 确定下一个要处理的表达式位置
                next_pos = None
                expr_type = None
                
                if sys_func_pos != -1 and (var_ref_pos == -1 or sys_func_pos < var_ref_pos):
                    # 先处理系统函数
                    next_pos = sys_func_pos
                    expr_type = 'sys_func'
                elif var_ref_pos != -1:
                    # 处理变量引用
                    next_pos = var_ref_pos
                    expr_type = 'var_ref'
                else:
                    # 没有找到更多表达式，添加剩余文本
                    result_parts.append(text[i:])
                    break
                
                # 添加表达式之前的文本
                result_parts.append(text[i:next_pos])
                
                if expr_type == 'sys_func':
                    # 处理系统函数：$sys.func.XXX(...)
                    # 查找函数名
                    func_start = next_pos + len('$sys.func.')
                    func_name_end = text.find('(', func_start)
                    
                    if func_name_end == -1:
                        # 没有找到左括号，保持原样
                        result_parts.append(text[next_pos:])
                        i = next_pos + 1
                        continue
                    
                    func_name = text[func_start:func_name_end]
                    
                    # 查找匹配的右括号
                    paren_start = func_name_end
                    paren_depth = 1
                    j = paren_start + 1
                    
                    while j < len(text) and paren_depth > 0:
                        if text[j] == '(':
                            paren_depth += 1
                        elif text[j] == ')':
                            paren_depth -= 1
                        j += 1
                    
                    if paren_depth == 0:
                        # 找到了完整的表达式
                        expr = text[next_pos:j]
                        
                        # 使用 parse_dialogflow_value 解析表达式
                        try:
                            input_vars = []
                            code, input_vars = parse_dialogflow_value(expr, input_vars)
                            
                            # 从解析结果中提取变量名
                            if input_vars:
                                # 对于 GET_FIELD，通常第二个参数（键）是我们要的变量
                                if func_name == 'GET_FIELD' and len(input_vars) >= 2:
                                    # GET_FIELD 的最后一个变量通常是键变量
                                    var_name = input_vars[-1]
                                else:
                                    # 其他函数使用第一个变量
                                    var_name = input_vars[0]
                                
                                # writed by senlin.deng 2026-01-13
                                # 将变量名转换为小写，确保一致性
                                var_name = var_name.lower()
                                
                                # 将表达式替换为模板格式
                                replacement = f'{{{{{var_name}}}}}'
                                result_parts.append(replacement)
                            else:
                                # 无法提取变量名，保持原样
                                result_parts.append(expr)
                        except Exception as e:
                            # 解析失败，保持原样
                            logger.debug(f"Failed to parse expression {expr}: {e}")
                            result_parts.append(expr)
                        
                        i = j
                    else:
                        # 没有找到完整的表达式，保持原样
                        result_parts.append(text[next_pos:])
                        break
                
                elif expr_type == 'var_ref':
                    # 处理变量引用：$xxx 或 $session.params.xxx
                    # 查找变量引用的结束位置
                    # 变量引用格式：$xxx 或 $session.params.xxx
                    j = next_pos + 1
                    
                    # 匹配变量引用模式：$[a-zA-Z][a-zA-Z0-9._-]*
                    # 或者 $session.params.[a-zA-Z0-9._-]+
                    if j < len(text) and text[j:j+8] == 'session.':
                        # 可能是 $session.params.xxx 格式
                        j += 8  # 跳过 'session.'
                        if j < len(text) and text[j:j+7] == 'params.':
                            j += 7  # 跳过 'params.'
                            # 继续匹配变量名部分
                            while j < len(text):
                                char = text[j]
                                if char.isalnum() or char in '._-':
                                    j += 1
                                else:
                                    break
                        else:
                            # 不是 params.，可能是其他格式，只匹配到 session.
                            j = next_pos + 8
                    else:
                        # 普通变量引用：$xxx
                        while j < len(text):
                            char = text[j]
                            # 变量名可以包含字母、数字、下划线、点号、连字符
                            if char.isalnum() or char in '._-':
                                j += 1
                            else:
                                break
                    
                    expr = text[next_pos:j]
                    
                    # 使用 parse_dialogflow_value 解析变量引用
                    try:
                        input_vars = []
                        code, input_vars = parse_dialogflow_value(expr, input_vars)
                        
                        if input_vars:
                            # 使用解析出的变量名
                            var_name = input_vars[0]
                            # writed by senlin.deng 2026-01-13
                            # 将变量名转换为小写，确保一致性
                            var_name = var_name.lower()
                            # 将变量引用替换为模板格式
                            replacement = f'{{{{{var_name}}}}}'
                            result_parts.append(replacement)
                        else:
                            # 无法提取变量名，尝试手动提取
                            if expr.startswith('$session.params.'):
                                var_name = expr.replace('$session.params.', '')
                            elif expr.startswith('$'):
                                var_name = expr[1:].split('.')[-1]
                            else:
                                var_name = expr
                            
                            # writed by senlin.deng 2026-01-13
                            # 将变量名转换为小写，确保一致性
                            var_name = var_name.lower()
                            
                            replacement = f'{{{{{var_name}}}}}'
                            result_parts.append(replacement)
                    except Exception as e:
                        # 解析失败，尝试手动提取
                        logger.debug(f"Failed to parse variable reference {expr}: {e}")
                        if expr.startswith('$session.params.'):
                            var_name = expr.replace('$session.params.', '')
                        elif expr.startswith('$'):
                            var_name = expr[1:].split('.')[-1]
                        else:
                            var_name = expr
                        
                        # writed by senlin.deng 2026-01-13
                        # 将变量名转换为小写，确保一致性
                        var_name = var_name.lower()
                        
                        replacement = f'{{{{{var_name}}}}}'
                        result_parts.append(replacement)
                    
                    i = j
            
            return ''.join(result_parts)
        
        # 替换所有表达式
        result = find_and_replace_expressions(payload)
        return result
    
    else:
        # 其他类型（int, float, bool, None）直接返回
        return payload




def normalize_condition_value(value: Any) -> Any:
    """
    标准化条件值，保留 'null' 关键字，避免被误认为空字符串
    
    Args:
        value: 条件值
        
    Returns:
        标准化后的值
    """
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() == 'null':
            return 'null'
        return stripped
    return value


def generate_setparameter_code_node(
    set_param_actions: List[Dict[str, Any]],
    page_id: str,
    intent_name: str,
    gen_unique_node_name: Callable[[str, str], str]
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    为 setParameterActions 生成 code 节点
    
    Args:
        set_param_actions: setParameterActions 列表
        page_id: page ID（用于生成唯一节点名）
        intent_name: intent名称（用于节点标题）
        gen_unique_node_name: 生成唯一节点名的函数
        
    Returns:
        (code节点, 输出变量列表)
    """
    if not set_param_actions:
        return None, []
    
    # 生成变量赋值代码
    code_lines = []
    output_variables = []
    input_variables = []
    
    for action in set_param_actions:
        parameter = action.get('parameter', '')
        value = action.get('value')  # 注意：不设默认值，保留 None
        
        # 使用通用解析函数处理值
        # 支持：变量引用、系统函数（如 GET_FIELD）、对象、null、数字、字符串等
        value_code, input_variables = parse_dialogflow_value(value, input_variables)
        code_lines.append(f"{parameter} = {value_code}")
        
        output_variables.append(parameter)
    
    # 生成code节点
    node_name = gen_unique_node_name('transition_code', page_id)
    title_suffix = f" ({intent_name})" if intent_name else ""
    
    code_node = {
        "type": "code",
        "name": node_name,
        "title": f"Set Parameters{title_suffix}",
        "code": "\n".join(code_lines),
        "outputs": output_variables,
        "args": input_variables
    }
    
    return code_node, output_variables

# 处理page条件判断
def parse_transition_events(
    page: Dict[str, Any],
    intents_mapping: Dict[str, str],
    intent_parameters_map: Dict[str, List[Dict[str, Any]]],
    gen_unique_node_name: Callable[[str, str], str],
    gen_variable_name: Callable[[], str],
    generate_setparameter_code_node_func: Callable,
    generate_intent_and_condition_nodes_func: Callable,
    entity_candidates: Dict[str, Dict[str, List[str]]] = None,
    lang: str = 'en',
    node_counter_ref: List[int] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parse transitionEvents from page and generate intent recognition and condition checking nodes
    
    Supports three patterns:
    1. Intent without parameters: RAG+CODE+condition
    2. Intent with parameters: RAG+CODE+condition+LLM+CODE+condition
    3. Pure condition branches: condition (or direct edges for is_always_true)
    
    Args:
        page: Page configuration dictionary
        intents_mapping: Intent ID to name mapping
        intent_parameters_map: Intent ID to parameters mapping
        gen_unique_node_name: Function to generate unique node names
        gen_variable_name: Function to generate variable names
        generate_setparameter_code_node_func: Function to generate setParameter code nodes
        generate_intent_and_condition_nodes_func: Function to generate intent and condition nodes
        entity_candidates: Entity candidates mapping (optional)
        lang: Language code (optional)
        node_counter_ref: Reference to node counter (list with single int) (optional)
        
    Returns:
        (list of nodes, list of condition branches, list of direct connections for is_always_true routes)
    """
    # 支持两种格式：'value.transitionEvents' (转换后的格式) 和 'transitionEvents' (原始格式)
    transition_events = page.get('value', {}).get('transitionEvents', []) if 'value' in page else page.get('transitionEvents', [])
    
    if not transition_events:
        return [], [], []
    
    # 收集所有的transitionEvent信息
    transition_info_list = []
    
    for event in transition_events:
        trigger_intent_id = event.get('triggerIntentId')
        condition = event.get('condition', {})
        handler = event.get('transitionEventHandler', {})
        
        # 获取目标
        target_page_id = handler.get('targetPageId')
        target_flow_id = handler.get('targetFlowId')
        
        # writed by senlin.deng 2026-01-14
        # 获取 beforeTransition 中的 setParameterActions 和 staticUserResponse
        before_transition = handler.get('beforeTransition', {})
        set_parameter_actions = before_transition.get('setParameterActions', [])
        
        # 解析 beforeTransition 中的 staticUserResponse（如果有）
        # 为每个 transition 生成唯一的标识符（使用 event name 或索引）
        event_name = event.get('name', f"transition_{len(transition_info_list)}")
        before_transition_text_nodes = parse_before_transition_responses(
            before_transition, lang, gen_unique_node_name, event_name
        )
        
        # 解析条件
        disjunction_values = []
        condition_string = event.get('conditionString', '')
        and_conditions_list = []
        mixed_and_or_condition = None  # 用于存储混合 AND+OR 条件
        
        # Check for literal "true" or "false" conditions first (highest priority)
        # write by senlin.deng 2026-01-20
        # 注意：不再检查 'not condition'，因为 conditionString 是 "true" 时可能同时存在 condition 对象
        if condition_string and condition_string.strip().lower() in ('true', 'false'):
            # Parse literal true/false conditions
            mixed_and_or_condition = parse_mixed_and_or_condition(condition_string)
            if not mixed_and_or_condition:
                # Fallback: ensure literal conditions still generate mixed-condition nodes
                literal_value = condition_string.strip().lower() == 'true'
                mixed_and_or_condition = {
                    "and_conditions": [],
                    "or_group": None,
                    "raw_condition": condition_string,
                    "is_literal": True,
                    "literal_value": literal_value
                }
            comparator = 'AND_OR_MIXED'
            rhs = {}
            lhs = {}
            condition = {}  # 清空 condition，避免后续重复解析
            logger.debug(f"    🔹 Detected literal condition: '{condition_string}' -> is_literal={mixed_and_or_condition.get('is_literal')}, literal_value={mixed_and_or_condition.get('literal_value')}")
        # Check for mixed AND + OR conditions (second priority)
        # Pattern: $page.params.status = "FINAL" AND ($session.params.PageInput = "x" OR $session.params.PageInput = "y")
        elif ' AND ' in condition_string and '(' in condition_string and ')' in condition_string and ' OR ' in condition_string and not condition:
            # Parse mixed AND + OR conditions
            mixed_and_or_condition = parse_mixed_and_or_condition(condition_string)
            if mixed_and_or_condition:
                comparator = 'AND_OR_MIXED'
                rhs = {}
                lhs = {}
            else:
                # 如果解析失败，回退到普通处理
                comparator = 'EQUALS'
                rhs = {}
                lhs = {}
        # Check for AND conditions (higher priority than OR)
        elif ' AND ' in condition_string and not condition:
            # Parse AND conditions
            and_parts = condition_string.split(' AND ')
            
            for part in and_parts:
                part = part.strip()
                match = re.match(r'\$session\.params\.([a-zA-Z0-9_-]+)\s*(!=|=|>|<|>=|<=)\s*("([^"]*)"|null|true|false|(\S+))', part)
                if match and match.group(1):
                    var_name = match.group(1).replace('-', '_')
                    operator = match.group(2)
                    value_raw = match.group(4) if match.group(4) is not None else (match.group(5) if match.group(5) else match.group(3))
                    value = normalize_condition_value(value_raw)
                    
                    operator_mapping = {'=': '=', '!=': '≠', '>': '>', '<': '<', '>=': '≥', '<=': '≤'}
                    mapped_operator = operator_mapping.get(operator, operator)
                    
                    and_conditions_list.append({
                        'variable': var_name,
                        'operator': mapped_operator,
                        'value': value
                    })
            
            comparator = 'AND_MULTIPLE'
            rhs = {}
            lhs = {}
            
        elif ' OR ' in condition_string and not condition:
            # Parse OR conditions
            if '(' in condition_string and ')' in condition_string:
                comparator = 'EQUALS'
                rhs = {}
                lhs = {}
            else:
                or_parts = condition_string.split(' OR ')
                or_conditions_list = []
                
                for part in or_parts:
                    part = part.strip()
                    match = re.match(r'\$session\.params\.([a-zA-Z0-9_-]+)\s*(!=|=|>|<|>=|<=)\s*("([^"]*)"|null|true|false|(\S+))', part)
                    if match and match.group(1):
                        var_name = match.group(1).replace('-', '_')
                        operator = match.group(2)
                        value_raw = match.group(4) if match.group(4) is not None else (match.group(5) if match.group(5) else match.group(3))
                        value = normalize_condition_value(value_raw)
                        
                        operator_mapping = {'=': '=', '!=': '≠', '>': '>', '<': '<', '>=': '≥', '<=': '≤'}
                        mapped_operator = operator_mapping.get(operator, operator)
                        
                        or_conditions_list.append({
                            'variable': var_name,
                            'operator': mapped_operator,
                            'value': value
                        })
                        disjunction_values.append(value)
                
                if or_conditions_list:
                    and_conditions_list = or_conditions_list
                    comparator = 'OR_MULTIPLE'
                else:
                    comparator = 'EQUALS'
                rhs = {}
                lhs = {}
        elif 'disjunction' in condition:
            # OR条件格式
            expressions = condition.get('disjunction', {}).get('expressions', [])
            for expr in expressions:
                restriction = expr.get('restriction', {})
                rhs_expr = restriction.get('rhs', {})
                if 'value' in rhs_expr:
                    disjunction_values.append(rhs_expr.get('value'))
                elif 'phrase' in rhs_expr:
                    phrase_values = rhs_expr.get('phrase', {}).get('values', [])
                    if phrase_values:
                        disjunction_values.append(phrase_values[0])
            
            if expressions:
                first_expr = expressions[0]
                restriction = first_expr.get('restriction', {})
                comparator = restriction.get('comparator', '')
                rhs = restriction.get('rhs', {})
                lhs = restriction.get('lhs', {})
            else:
                comparator = ''
                rhs = {}
                lhs = {}
        elif 'comparator' in condition:
            comparator = condition.get('comparator', '')
            rhs = condition.get('rhs', {})
            lhs = condition.get('lhs', {})
        else:
            restriction = condition.get('restriction', {})
            comparator = restriction.get('comparator', '')
            rhs = restriction.get('rhs', {})
            lhs = restriction.get('lhs', {})
        
        # 初始化transitionInfo
        transition_info = {
            "target_page_id": target_page_id,
            "target_flow_id": target_flow_id,
            "has_intent": False,
            "has_parameters": False,
            "has_condition": False,
            "intent_id": None,
            "intent_name": None,
            "parameters": [],
            "condition_variable": None,
            "condition_operator": None,
            "condition_value": None,
            "condition_values": disjunction_values,
            "and_conditions_list": and_conditions_list,
            "is_or_condition": False,
            "is_mixed_and_or": False,  # 新增：标记是否为混合 AND+OR 条件
            "mixed_and_or_condition": mixed_and_or_condition,  # 新增：存储解析后的混合条件
            "set_parameter_actions": set_parameter_actions,
            "before_transition_text_nodes": before_transition_text_nodes  # 添加 beforeTransition 的 text 节点列表
        }
        
        # 1. 检查是否有intent
        if trigger_intent_id:
            transition_info["has_intent"] = True
            transition_info["intent_id"] = trigger_intent_id
            transition_info["intent_name"] = intents_mapping.get(trigger_intent_id, trigger_intent_id)
            
            # 2. 检查该intent是否有parameters需要提取
            if trigger_intent_id in intent_parameters_map:
                transition_info["has_parameters"] = True
                transition_info["parameters"] = intent_parameters_map[trigger_intent_id]
        
        # 3. 检查是否有condition
        condition_value = None
        if isinstance(rhs, dict):
            condition_value = rhs.get('value')
        elif isinstance(rhs, str):
            condition_value = rhs
        
        # Only treat as always-true when it's an empty condition
        # (avoid misclassifying "$page.params.X = true" as always-true)
        is_always_true = (
            (not comparator and not lhs and not rhs)
        )
        
        # Check for mixed AND+OR, AND/OR conditions or regular conditions
        if mixed_and_or_condition:
            # 混合 AND+OR 条件
            transition_info["has_condition"] = True
            transition_info["is_mixed_and_or"] = True
        elif and_conditions_list:
            transition_info["has_condition"] = True
            transition_info["is_or_condition"] = (comparator == 'OR_MULTIPLE')
        elif comparator and comparator != 'GLOBAL' and not is_always_true:
            transition_info["has_condition"] = True
            
            # 提取左侧变量
            if 'member' in lhs:
                expressions = lhs.get('member', {}).get('expressions', [])
                if len(expressions) >= 3:
                    condition_var_raw = expressions[-1].get('value', '')
                    if isinstance(condition_var_raw, str) and condition_var_raw.startswith('$'):
                        var_ref = condition_var_raw[1:]
                        condition_var = var_ref.replace('.', '_').replace('-', '_')
                        transition_info["condition_variable"] = condition_var
                    else:
                        transition_info["condition_variable"] = condition_var_raw
            
            # 提取右侧值
            if 'value' in rhs:
                transition_info["condition_value"] = rhs.get('value')
            elif 'phrase' in rhs:
                phrase_values = rhs.get('phrase', {}).get('values', [])
                if phrase_values:
                    # writed by senlin.deng 2026-01-13
                    # 去除phrase_values[0]中的多余空格，使得单词间变成单空格，该节点为条件判断节点
                    transition_info["condition_value"] = " ".join(phrase_values[0].split())
            
            # 转换比较运算符
            operator_mapping = {
                'EQUALS': '=',
                'NOT_EQUALS': '≠',
                'GREATER_THAN': '>',
                'LESS_THAN': '<',
                'CONTAINS': 'contains'
            }
            transition_info["condition_operator"] = operator_mapping.get(comparator, '=')
        
        # 标记是否为始终为 true 的条件
        transition_info["is_always_true"] = is_always_true
        
        transition_info_list.append(transition_info)
    
    # writed by senlin.deng 2026-01-12
    # 将所有条件判断变量转换为小写，使得兼容googledialogflow的大小写不敏感的语法
    # 注意：如果 condition_variable 不存在，保持为 None，不要用 target_page_id 替代
    for idx, transition_info in enumerate(transition_info_list):
        if transition_info.get('condition_variable'):
            transition_info_list[idx]['condition_variable'] = transition_info['condition_variable'].lower()
    # logger.info(f"condition_variable: {[t['condition_variable'] for t in transition_info_list]}")
    
    # 调用生成节点的方法
    nodes, branches = generate_intent_and_condition_nodes_func(
        page, transition_info_list, 
        intents_mapping, intent_parameters_map,
        gen_unique_node_name, gen_variable_name,
        generate_setparameter_code_node_func,
        entity_candidates, lang, node_counter_ref
    )
    return nodes, branches, []


# -*- coding: utf-8 -*-
"""
Step 8: 将 step7_final 中的 workflow JSON 文件合并到 planning JSON 的 chatflow_list 中

功能：
1. 读取模板 planning JSON 文件
2. 读取 step7_final 目录下的所有 workflow JSON 文件
3. 将每个 workflow JSON 添加到 planning.resource.chatflow.chatflow_list 数组中
4. 保存合并后的 JSON 文件

作者：chenyu.zhu
日期：2025-12-17
"""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Dict, List, Any, Set

from logger_config import get_logger, is_verbose
logger = get_logger(__name__)

# 控制详细输出的开关；默认只保留少量 INFO 日志
VERBOSE = is_verbose()


def load_template_json(template_path: str) -> Dict[str, Any]:
    """
    加载模板 planning JSON 文件
    
    Args:
        template_path: 模板 JSON 文件路径
        
    Returns:
        模板 JSON 数据
    """
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"✅ 成功加载模板文件: {template_path}")
        return data
    except Exception as e:
        logger.error(f"❌ 加载模板文件失败: {e}")
        raise


def load_workflow_json(workflow_path: str) -> Dict[str, Any]:
    """
    加载单个 workflow JSON 文件
    
    Args:
        workflow_path: workflow JSON 文件路径
        
    Returns:
        workflow JSON 数据
    """
    try:
        with open(workflow_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # 尝试解析 JSON
        try:
            data = json.loads(content)
            return data
        except json.JSONDecodeError as je:
            logger.error(f"  ❌ JSON 格式错误 {os.path.basename(workflow_path)}")
            logger.error(f"     位置: 第 {je.lineno} 行, 第 {je.colno} 列")
            logger.error(f"     错误: {je.msg}")
            # 显示错误附近的内容
            lines = content.split('\n')
            if je.lineno <= len(lines):
                start_line = max(0, je.lineno - 3)
                end_line = min(len(lines), je.lineno + 2)
                logger.error(f"     上下文:")
                for i in range(start_line, end_line):
                    prefix = "  >>> " if i == je.lineno - 1 else "      "
                    logger.error(f"{prefix}{i+1:4d}: {lines[i][:100]}")
            return None
    except Exception as e:
        logger.warning(f"  ⚠️  加载文件失败 {workflow_path}: {e}")
        return None


def load_intents_mapping(intents_file: str) -> Dict[str, Dict[str, Any]]:
    """
    从 intents.json 文件加载意图信息（包括名称和相似问）
    
    Args:
        intents_file: intents.json 文件路径
        
    Returns:
        intent_id -> {name: intent_name, trainingPhrases: [...]} 的映射字典
    """
    intents_mapping = {}
    
    if not os.path.exists(intents_file):
        logger.warning(f"  ⚠️  intents 文件不存在: {intents_file}")
        return intents_mapping
    
    try:
        with open(intents_file, 'r', encoding='utf-8') as f:
            intents_data = json.load(f)
        
        intents_list = intents_data.get('intents', [])
        for intent in intents_list:
            intent_id = intent.get('id', '')
            intent_name = intent.get('displayName', '')
            training_phrases = intent.get('trainingPhrases', [])
            if intent_id:
                intents_mapping[intent_id] = {
                    'name': intent_name,
                    'trainingPhrases': training_phrases
                }
        
        logger.debug(f"  ✅ 加载了 {len(intents_mapping)} 个意图映射（包含相似问）")
    except Exception as e:
        logger.warning(f"  ⚠️  加载 intents 文件失败: {e}")
    
    return intents_mapping


def normalize_name(name: str) -> str:
    """
    规范化名称（用于匹配）
    将名称转换为小写，替换特殊字符为下划线
    
    Args:
        name: 原始名称
        
    Returns:
        规范化后的名称
    """
    import re
    normalized = re.sub(r'[<>:"/\\|?*\s]', '_', name)
    normalized = re.sub(r'_+', '_', normalized)
    normalized = normalized.strip('_').lower()
    return normalized


def get_flow_intents_from_exported_flow(
    exported_flow_file: str,
    intents_mapping: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """
    从 exported_flow 文件中获取 flow 层级的意图信息
    建立两种映射：
    1. normalized_intent_name -> {triggerIntentId, trainingPhrases, original_name, index}
    2. intent_index -> {triggerIntentId, trainingPhrases, original_name, normalized_name}
    
    Args:
        exported_flow_file: exported_flow JSON 文件路径
        intents_mapping: intent_id -> {name, trainingPhrases} 的映射
        
    Returns:
        {
            'by_name': normalized_intent_name -> {triggerIntentId, trainingPhrases, original_name, index},
            'by_index': intent_index -> {triggerIntentId, trainingPhrases, original_name, normalized_name}
        }
    """
    intent_name_to_info = {}
    intent_index_to_info = {}
    
    if not os.path.exists(exported_flow_file):
        logger.warning(f"  ⚠️  exported_flow 文件不存在: {exported_flow_file}")
        return {'by_name': intent_name_to_info, 'by_index': intent_index_to_info}
    
    try:
        with open(exported_flow_file, 'r', encoding='utf-8') as f:
            flow_data = json.load(f)
        
        # 获取 flow 层级的 transitionEvents
        transition_events = flow_data.get('flow', {}).get('flow', {}).get('transitionEvents', [])
        
        for idx, event in enumerate(transition_events, 1):
            trigger_intent_id = event.get('triggerIntentId', '')
            if trigger_intent_id:
                # 从 intents_mapping 获取意图信息
                intent_info = intents_mapping.get(trigger_intent_id, {})
                intent_name = intent_info.get('name', trigger_intent_id)
                training_phrases = intent_info.get('trainingPhrases', [])
                
                # 使用规范化后的名称作为 key（用于匹配 workflow 的 flow_name）
                normalized_name = normalize_name(intent_name)
                
                info_dict = {
                    'triggerIntentId': trigger_intent_id,
                    'trainingPhrases': training_phrases,
                    'original_name': intent_name,  # 保留原始名称
                    'index': idx  # 保存序号（从1开始）
                }
                
                intent_name_to_info[normalized_name] = info_dict
                intent_index_to_info[idx] = {
                    'triggerIntentId': trigger_intent_id,
                    'trainingPhrases': training_phrases,
                    'original_name': intent_name,
                    'normalized_name': normalized_name
                }
                
                # 调试信息：显示匹配的意图
                logger.debug(f"    📌 [{idx}] 意图: {intent_name} -> normalized: {normalized_name}, trainingPhrases: {len(training_phrases)} 个")
        
        logger.debug(f"  ✅ 从 exported_flow 获取了 {len(intent_name_to_info)} 个 flow 层级意图（包含相似问）")
    except Exception as e:
        logger.warning(f"  ⚠️  读取 exported_flow 文件失败: {e}")
    
    return {'by_name': intent_name_to_info, 'by_index': intent_index_to_info}


def extract_intent_name_from_filename(filename: str) -> str:
    """
    从 workflow 文件名中提取意图名称
    例如: generated_workflow_transactionservicing_downloadestatement.json
    返回: transactionservicing_downloadestatement
    
    Args:
        filename: workflow 文件名
        
    Returns:
        意图名称
    """
    # 移除前缀和后缀
    name = filename.replace('generated_workflow_', '').replace('.json', '')
    return name


def extract_intent_index_from_flow_name(flow_name: str) -> int:
    """
    从 flow_name 中提取序号（如果是 intent_10 格式）
    
    Args:
        flow_name: workflow 的 flow_name，例如 "intent_10"
        
    Returns:
        序号（从1开始），如果无法提取则返回 None
    """
    import re
    # 匹配 intent_数字 格式
    match = re.match(r'intent_(\d+)', flow_name)
    if match:
        return int(match.group(1))
    return None


def create_intention_list_from_chatflow(
    template_data: Dict[str, Any],
    global_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    根据 chatflow_list 自动创建 intention_list
    
    Args:
        template_data: planning JSON 数据
        
    Returns:
        更新后的 planning JSON 数据
    """
    # 获取 chatflow_list
    chatflow_list = template_data.get("planning", {}).get("resource", {}).get("chatflow", {}).get("chatflow_list", [])
    
    if not chatflow_list:
        logger.warning("  ⚠️  警告: chatflow_list 为空，无法创建 intention_list")
        return template_data
    
    # 创建 intention_list
    intention_list = []
    
    for idx, workflow in enumerate(chatflow_list, 1):
        flow_uuid = workflow.get("flow_uuid", "")
        flow_name = workflow.get("flow_name", "")
        
        if not flow_uuid:
            continue
        
        # 创建 intention 对象
        intention = {
            "intention_name": flow_name or f"intent_{idx}",
            "description": "",
            "positive_keywords_enable": False,
            "positive_keywords_list": "[]",
            "positive_keywords_type": 1,
            "regular_enable": False,
            "regular_str": "",
            "sft_model_enable": False,
            "sft_model_name": "",
            "sft_model_reponse_structure": "{}",
            "embedding_enable": True,
            "positive_examples": "[]",
            "negative_examples": "[]",
            "llm_enable": 0,
            "action_flow_uuid": flow_uuid,
            "action_flow_node_id": "",
            "negative_keywords_enable": False,
            "negative_keywords_list": "[]",
            "negative_keywords_type": 1,
            "negative_action_flow_uuid": "",
            "negative_action_flow_node_id": "",
            "action_pre_message_enable": False,
            "action_pre_message_type": 1,
            "action_pre_message_content": "",
            "action_pre_message_prompt": "",
            "sort_num": idx,
            "multi_action_jump_type": 1,  # 1 = 全局意图处理后回到上一个流程
            "multi_action_pre_message_enable": False,
            "multi_action_pre_message_type": 1,
            "multi_action_pre_message_content": "",
            "multi_action_pre_message_prompt": "",
            "is_active": True
        }
        
        intention_list.append(intention)
        
    # 更新 template_data
    template_data["planning"]["resource"]["intention_list"] = intention_list
    
    logger.debug(f"  ✅ 自动创建了 {len(intention_list)} 个 intention（基于 chatflow_list）")
    
    return template_data


def update_positive_examples(
    template_data: Dict[str, Any],
    workflow_files: List[str],
    step7_dir: str,
    intent_name_to_info: Dict[str, Dict[str, Any]],
    global_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    更新 intention_list 中的 positive_examples
    使用意图的 trainingPhrases（相似问）作为 positive_examples 的 value
    
    Args:
        template_data: planning JSON 数据
        workflow_files: workflow 文件名列表
        step7_dir: step7_final 目录路径
        intent_name_to_info: intent_name -> {triggerIntentId, trainingPhrases} 的映射
        
    Returns:
        更新后的 planning JSON 数据
    """
    # 获取 intention_list
    intention_list = template_data.get("planning", {}).get("resource", {}).get("intention_list", [])
    
    # 如果 intention_list 为空，根据 chatflow_list 自动创建
    if not intention_list:
        logger.debug("  📝 模板文件中 intention_list 为空，根据 chatflow_list 自动创建...")
        template_data = create_intention_list_from_chatflow(template_data, global_config)
        intention_list = template_data.get("planning", {}).get("resource", {}).get("intention_list", [])
        
        if not intention_list:
            logger.warning("  ⚠️  警告: 无法创建 intention_list")
            return template_data
    
    # 获取 chatflow_list 建立 flow_uuid -> workflow 的映射
    chatflow_list = template_data.get("planning", {}).get("resource", {}).get("chatflow", {}).get("chatflow_list", [])
    flow_uuid_to_workflow = {item.get("flow_uuid"): item for item in chatflow_list if item.get("flow_uuid")}
    
    # 建立 workflow 文件名 -> flow_uuid 的映射
    workflow_name_to_uuid = {}
    workflow_name_to_intents = {}  # 新增：记录每个 workflow 包含的 page-level intents
    
    for workflow_file in workflow_files:
        workflow_path = os.path.join(step7_dir, workflow_file)
        if os.path.exists(workflow_path):
            try:
                with open(workflow_path, 'r', encoding='utf-8') as f:
                    workflow_data = json.load(f)
                flow_uuid = workflow_data.get("flow_uuid")
                if flow_uuid:
                    intent_name = extract_intent_name_from_filename(workflow_file)
                    workflow_name_to_uuid[intent_name] = flow_uuid
                    
                    # 【新增】对于 intent_X 类型的 workflow，提取 page_intents
                    if intent_name and intent_name.startswith('intent_'):
                        page_intents_set = set()
                        nodes = workflow_data.get("nodes", [])
                        for node in nodes:
                            if node.get("type") == "knowledgeAssignment":
                                page_intents = node.get("config", {}).get("rag_config", {}).get("page_intents", [])
                                for pi in page_intents:
                                    if pi:
                                        page_intents_set.add(pi)
                        
                        if page_intents_set:
                            workflow_name_to_intents[intent_name] = list(page_intents_set)
                            logger.debug(f"  📋 {intent_name} 包含 {len(page_intents_set)} 个 page-level intents")
            except Exception as e:
                logger.warning(f"  ⚠️  读取 workflow 文件失败 {workflow_file}: {e}")
    
    logger.debug(f"\n📝 更新 intention_list 中的 positive_examples...")
    logger.debug(f"   找到 {len(intention_list)} 个 intention")
    logger.debug(f"   找到 {len(workflow_name_to_uuid)} 个 workflow 映射")
    logger.debug(f"   找到 {len(workflow_name_to_intents)} 个包含 page-level intents 的 workflow\n")
    
    # 确保所有 intention 的 multi_action_jump_type 都是 1（全局意图处理后回到上一个流程）
    for intention in intention_list:
        if intention.get("multi_action_jump_type", 0) != 1:
            intention["multi_action_jump_type"] = 1
    
    updated_count = 0
    total_examples_added = 0
    
    # 构建 intent_name -> intent_info 映射（从 intents.json）
    intents_by_name = {}
    if intent_name_to_info and 'by_name' in intent_name_to_info:
        intents_by_name = intent_name_to_info['by_name']
    
    for intention in intention_list:
        action_flow_uuid = intention.get("action_flow_uuid", "")
        if not action_flow_uuid:
            continue
        
        # 找到对应的 workflow
        workflow = flow_uuid_to_workflow.get(action_flow_uuid)
        if not workflow:
            continue
        
        # 从 workflow 中获取 flow_name
        flow_name = workflow.get("flow_name", "")
        if not flow_name:
            continue
        
        # 尝试通过序号匹配（如果是 intent_10 格式）
        intent_info = None
        intent_index = extract_intent_index_from_flow_name(flow_name)
        
        if intent_index is not None:
            # 通过序号匹配
            intent_index_map = intent_name_to_info.get('by_index', {})
            intent_info = intent_index_map.get(intent_index)
            if intent_info:
                logger.debug(f"  ✅ 通过序号匹配: {flow_name} -> transitionEvent[{intent_index}]")
        
        # 如果序号匹配失败，尝试通过名称匹配
        if not intent_info:
            normalized_flow_name = normalize_name(flow_name)
            intent_name_map = intent_name_to_info.get('by_name', {})
            intent_info = intent_name_map.get(normalized_flow_name)
            if intent_info:
                logger.debug(f"  ✅ 通过名称匹配: {flow_name} -> {intent_info.get('original_name', '')}")
        
        # 【新增】如果还没有找到，且是 intent_X 类型，尝试从 page_intents 获取 training phrases
        if not intent_info and flow_name.startswith('intent_'):
            page_intents = workflow_name_to_intents.get(flow_name, [])
            if page_intents:
                # 合并所有 page-level intents 的 training phrases
                combined_phrases = []
                for page_intent in page_intents:
                    # 规范化名称（转为小写，去下划线）
                    normalized = normalize_name(page_intent)
                    page_info = intents_by_name.get(normalized)
                    if page_info:
                        combined_phrases.extend(page_info.get('trainingPhrases', []))
                    else:
                        # 也尝试精确匹配
                        page_info = intents_by_name.get(page_intent)
                        if page_info:
                            combined_phrases.extend(page_info.get('trainingPhrases', []))
                
                if combined_phrases:
                    intent_info = {
                        'trainingPhrases': combined_phrases,
                        'original_name': f"{flow_name} (combined from {len(page_intents)} page intents)"
                    }
                    logger.debug(f"  📦 {flow_name}: 合并了 {len(page_intents)} 个 page-level intents，共 {len(combined_phrases)} 个 training phrases")
        
        if not intent_info:
            logger.warning(f"  ⚠️  未找到意图映射: {flow_name}")
            # 显示可用的意图名称帮助调试
            intent_name_map = intent_name_to_info.get('by_name', {})
            if intent_name_map:
                available_names = list(intent_name_map.keys())
                logger.debug(f"     可用的意图名称 ({len(available_names)} 个): {available_names[:10]}")
            continue
        
        training_phrases = intent_info.get('trainingPhrases', [])
        if not training_phrases:
            logger.warning(f"  ⚠️  意图 {flow_name} 没有 trainingPhrases")
            continue
        
        # 解析现有的 positive_examples
        positive_examples_str = intention.get("positive_examples", "[]")
        try:
            if isinstance(positive_examples_str, str):
                positive_examples = json.loads(positive_examples_str)
            else:
                positive_examples = positive_examples_str
        except:
            positive_examples = []
        
        # 获取现有的 value 集合（用于去重）
        existing_values = {ex.get("value", "") for ex in positive_examples if isinstance(ex, dict)}
        existing_ids = {ex.get("id", "") for ex in positive_examples if isinstance(ex, dict)}
        
        # 为每个 trainingPhrase 添加 positive_example
        examples_added = 0
        for phrase in training_phrases:
            # 跳过已存在的 value
            if phrase in existing_values:
                continue
            
            # 生成新的 ID（确保不重复）
            new_id = short_id()
            while new_id in existing_ids:
                new_id = short_id()
            
            # 添加新的 positive_example
            positive_examples.append({
                "id": new_id,
                "value": phrase  # 使用相似问作为 value
            })
            
            existing_values.add(phrase)
            existing_ids.add(new_id)
            examples_added += 1
        
        if examples_added > 0:
            # 更新 intention
            intention["positive_examples"] = json.dumps(positive_examples, ensure_ascii=False)
            
            updated_count += 1
            total_examples_added += examples_added
            logger.debug(f"  ✅ 更新 {intention.get('intention_name', '未知')}: 添加 {examples_added} 个 positive_examples (意图: {flow_name})")
        else:
            logger.debug(f"  ⏭️  跳过 {intention.get('intention_name', '未知')} (所有相似问已存在)")
    
    logger.debug(f"\n📊 更新完成:")
    logger.debug(f"   ✅ 成功更新: {updated_count} 个 intention")
    logger.debug(f"   📝 总共添加: {total_examples_added} 个 positive_examples")
    
    return template_data


def short_id():
    """生成短 ID（类似 EdfMIK03J77ZhSLJ6_OKZ 格式）"""
    import base64
    raw = uuid.uuid4().bytes
    return base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=\n').replace('-', '_').replace('+', '_')[:21]


def normalize_emb_language(data: Any) -> None:
    """
    统一将所有 emb_language 为 'english' 的地方改成 'en'
    就地修改传入的字典 / 列表
    """
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "emb_language" and isinstance(value, str) and value.lower() == "english":
                data[key] = "en"
            else:
                normalize_emb_language(value)
    elif isinstance(data, list):
        for item in data:
            normalize_emb_language(item)

# variable_数字 替换为 last_user_response
# write by senlin.deng
# 2025-12-23
def replace_variable_pattern(data: Any, pattern: str, replacement: str) -> int:
    """
    递归遍历数据，将匹配正则模式的变量名替换为指定值
    
    Args:
        data: 要处理的数据
        pattern: 正则表达式模式（如 r"^variable_\\d+$" 匹配 variable_0, variable_1 等）
        replacement: 替换后的变量名
        
    Returns:
        替换的次数
    """
    count = 0
    
    if isinstance(data, dict):
        # 1. 处理 variables 列表中的 variable_name
        if "variables" in data and isinstance(data["variables"], list):
            for var in data["variables"]:
                if isinstance(var, dict) and "variable_name" in var:
                    old_name = var["variable_name"]
                    if re.match(pattern, old_name):
                        var["variable_name"] = replacement
                        logger.debug(f"  🔄 变量定义: {old_name} → {replacement}")
                        count += 1
        
        # 2. 处理 variable_assign 字段
        if "variable_assign" in data and data["variable_assign"]:
            old_name = data["variable_assign"]
            if isinstance(old_name, str) and re.match(pattern, old_name):
                data["variable_assign"] = replacement
                logger.debug(f"  🔄 variable_assign: {old_name} → {replacement}")
                count += 1
        
        # 3. 处理 outputs 列表（可能是字符串列表或对象列表）
        if "outputs" in data and isinstance(data["outputs"], list):
            for i, item in enumerate(data["outputs"]):
                if isinstance(item, str) and re.match(pattern, item):
                    data["outputs"][i] = replacement
                    logger.debug(f"  🔄 outputs: {item} → {replacement}")
                    count += 1
                elif isinstance(item, dict):
                    # 处理对象形式的 outputs，如 {"name": "variable_23", ...}
                    if "name" in item and isinstance(item["name"], str) and re.match(pattern, item["name"]):
                        old_name = item["name"]
                        item["name"] = replacement
                        logger.debug(f"  🔄 outputs.name: {old_name} → {replacement}")
                        count += 1
        
        # 4. 处理 args 列表（可能是字符串列表或对象列表）
        if "args" in data and isinstance(data["args"], list):
            for i, item in enumerate(data["args"]):
                if isinstance(item, str) and re.match(pattern, item):
                    data["args"][i] = replacement
                    logger.debug(f"  🔄 args: {item} → {replacement}")
                    count += 1
                elif isinstance(item, dict):
                    # 处理对象形式的 args，如 {"name": "variable_23", "default_value": "...", "type": "string"}
                    if "name" in item and isinstance(item["name"], str) and re.match(pattern, item["name"]):
                        old_name = item["name"]
                        item["name"] = replacement
                        logger.debug(f"  🔄 args.name: {old_name} → {replacement}")
                        count += 1
        
        # 5. 处理 code 字段中的代码（替换代码中的变量名）
        if "code" in data and isinstance(data["code"], str):
            code_content = data["code"]
            # 使用单词边界匹配代码中的变量名，避免部分替换
            # 构建匹配代码中变量名的正则（去掉 ^ 和 $ 锚点，添加单词边界）
            code_pattern = pattern.replace("^", "").replace("$", "")
            # 添加单词边界 \b 来匹配完整的变量名
            code_pattern_with_boundary = r"\b(" + code_pattern + r")\b"
            
            def replace_code_var(m):
                nonlocal count
                old_var = m.group(1)
                count += 1
                logger.debug(f"  🔄 code 中变量: {old_var} → {replacement}")
                return replacement
            
            new_code = re.sub(code_pattern_with_boundary, replace_code_var, code_content)
            if new_code != code_content:
                data["code"] = new_code
        
        # 6. 处理字符串值中的变量引用（多种格式）
        for key, value in list(data.items()):
            if isinstance(value, str):
                new_value = value
                
                # 6.1 处理 {{variable_name}} 模板引用
                if "{{" in new_value:
                    def replace_template(m):
                        nonlocal count
                        var_name = m.group(1)
                        if re.match(pattern, var_name):
                            count += 1
                            logger.debug(f"  🔄 模板引用: {{{{{var_name}}}}} → {{{{{replacement}}}}}")
                            return "{{" + replacement + "}}"
                        return m.group(0)
                    
                    new_value = re.sub(r"\{\{(\w+)\}\}", replace_template, new_value)
                
                # 6.2 处理 data-id="variable_数字" HTML 属性格式
                if 'data-id=' in new_value:
                    def replace_data_id(m):
                        nonlocal count
                        quote = m.group(1)  # 引号类型（" 或 '）
                        var_name = m.group(2)
                        if re.match(pattern, var_name):
                            count += 1
                            logger.debug(f"  🔄 data-id: {var_name} → {replacement}")
                            return f'data-id={quote}{replacement}{quote}'
                        return m.group(0)
                    
                    new_value = re.sub(r'data-id=(["\'])(\w+)\1', replace_data_id, new_value)
                
                # 6.3 处理转义的 data-id=\"variable_数字\" 格式（JSON 中的转义引号）
                if 'data-id=\\' in new_value:
                    def replace_escaped_data_id(m):
                        nonlocal count
                        var_name = m.group(1)
                        if re.match(pattern, var_name):
                            count += 1
                            logger.debug(f"  🔄 data-id(escaped): {var_name} → {replacement}")
                            return f'data-id=\\"{replacement}\\"'
                        return m.group(0)
                    
                    new_value = re.sub(r'data-id=\\"(\w+)\\"', replace_escaped_data_id, new_value)
                
                if new_value != value:
                    data[key] = new_value
        
        # 7. 递归处理嵌套结构
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                count += replace_variable_pattern(value, pattern, replacement)
                
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, str) and re.match(pattern, item):
                data[i] = replacement
                logger.debug(f"  🔄 列表项: {item} → {replacement}")
                count += 1
            elif isinstance(item, (dict, list)):
                count += replace_variable_pattern(item, pattern, replacement)
    
    return count


def find_variables_by_node_type(
    data: Any, 
    node_types: List[str] = None,
    pattern: str = r"^variable_\d+$"
) -> Set[str]:
    """
    递归查找指定类型节点中匹配模式的 variable_assign 变量名
    
    节点结构示例：
    {
        "type": "knowledgeAssignment",  # 节点类型
        "config": {
            "title": "Knowledge Base Retrieval",
            "variable_assign": "variable_87",
            ...
        }
    }
    
    write by senlin.deng 2025-12-29
    
    Args:
        data: 要处理的数据
        node_types: 要筛选的节点类型列表，如 ["knowledgeAssignment", "code"]
                    默认为 ["knowledgeAssignment"]
        pattern: 正则表达式模式（默认匹配 variable_数字 格式）
        
    Returns:
        指定类型节点中匹配模式的变量名集合
    """
    if node_types is None:
        node_types = ["knowledgeAssignment"]
    
    found_variables: Set[str] = set()
    
    if isinstance(data, dict):
        # 检查是否是目标类型的节点
        node_type = data.get("type")
        if node_type in node_types:
            # 优先检查 config.variable_assign（cybertron 格式）
            config = data.get("config", {})
            var_assign = config.get("variable_assign")
            if var_assign and isinstance(var_assign, str) and re.match(pattern, var_assign):
                found_variables.add(var_assign)
                logger.debug(f"  📚 发现 {node_type} 节点变量: {var_assign}")
            # 也检查顶层 variable_assign（step2 生成格式）
            elif "variable_assign" in data:
                var_assign = data.get("variable_assign")
                if var_assign and isinstance(var_assign, str) and re.match(pattern, var_assign):
                    found_variables.add(var_assign)
                    logger.debug(f"  📚 发现 {node_type} 节点变量（顶层）: {var_assign}")
        
        # 递归处理嵌套结构
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                found_variables.update(find_variables_by_node_type(value, node_types, pattern))
                
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                found_variables.update(find_variables_by_node_type(item, node_types, pattern))
    
    return found_variables


def build_exact_match_pattern(variable_names: Set[str]) -> str:
    """
    将变量名集合转换为精确匹配的正则表达式模式
    
    例如：{'variable_87', 'variable_123'} → '^(variable_87|variable_123)$'
    
    write by senlin.deng 2025-12-29
    
    Args:
        variable_names: 变量名集合
        
    Returns:
        正则表达式模式字符串
    """
    if not variable_names:
        return r"^$"  # 匹配空字符串（实际上不会匹配任何变量名）
    
    # 转义特殊字符并用 | 连接
    escaped_names = [re.escape(name) for name in sorted(variable_names)]
    return r"^(" + "|".join(escaped_names) + r")$"


def deduplicate_variables_in_chatflow_list(data: Dict[str, Any]) -> int:
    """
    对 chatflow_list 中每个 flow 的 variables 进行去重
    根据 variable_name 去重，如果有重复只保留一个
    去重后的变量 description 设为空字符串，from_node 设为 null
    
    write by senlin.deng 2025-12-24
    
    Args:
        data: planning JSON 数据
        
    Returns:
        去重的变量数量
    """
    total_removed = 0
    
    # 获取 chatflow_list
    chatflow_list = data.get("planning", {}).get("resource", {}).get("chatflow", {}).get("chatflow_list", [])
    
    if not chatflow_list:
        logger.debug("  ⚠️ chatflow_list 为空，跳过变量去重")
        return 0
    
    for flow in chatflow_list:
        flow_name = flow.get("flow_name", "Flow_Unknown")
        variables = flow.get("variables", [])
        
        if not variables or not isinstance(variables, list):
            continue
        
        # 使用字典去重（根据 variable_name）
        seen_names = {}
        unique_variables = []
        duplicates_in_flow = 0
        
        # 第一遍：统计每个变量名出现的次数
        var_name_count = {}
        for var in variables:
            if isinstance(var, dict):
                var_name = var.get("variable_name", "")
                var_name_count[var_name] = var_name_count.get(var_name, 0) + 1
        
        # 第二遍：去重处理
        for var in variables:
            if not isinstance(var, dict):
                # 非字典类型的元素，跳过并记录警告
                logger.warning(f"    ⚠️ 跳过非字典类型变量: {type(var).__name__} (flow: {flow_name})")
                continue
            
            var_name = var.get("variable_name", "")
            
            # 如果没有 variable_name，跳过
            if not var_name:
                logger.warning(f"    ⚠️ 跳过无名变量 (flow: {flow_name})")
                continue
            
            # # 去除所有 last_user_response 变量, 因为系统存在
            # update by senlin.deng 2025-12-31
            # 变量替换成LLM_response、KB_response，保留last_user_response
            # if var_name == "last_user_response":
            #     # 去除所有 last_user_response 变量
            #     duplicates_in_flow += 1
            #     logger.debug(f"    🗑️ 去除变量: {var_name} (flow: {flow_name})")
            #     continue

            if var_name in seen_names:
                # 发现重复，跳过这个变量
                duplicates_in_flow += 1
                logger.debug(f"    🔄 去重变量: {var_name} (flow: {flow_name})")
            else:
                # 第一次出现，保留这个变量
                seen_names[var_name] = True
                
                # 检查是否有重复：如果该变量名出现次数 > 1，则清空 description 和 from_node
                if var_name_count.get(var_name, 1) > 1:
                    # 有重复，设置 description 为空，from_node 为 null
                    deduplicated_var = {
                        "variable_name": var_name,
                        "type": var.get("type", "text"),
                        "description": "",
                        "from_node": None,
                        "lang": var.get("lang", "en")
                    }
                    unique_variables.append(deduplicated_var)
                else:
                    # 不重复，保留原始内容
                    unique_variables.append(var)
        
        if duplicates_in_flow > 0:
            flow["variables"] = unique_variables
            total_removed += duplicates_in_flow
            logger.debug(f"  ✅ {flow_name}: 去重 {duplicates_in_flow} 个变量，剩余 {len(unique_variables)} 个")
    
    return total_removed


def merge_workflows_to_planning(
    template_data: Dict[str, Any],
    workflow_files: List[str],
    step7_dir: str
) -> Dict[str, Any]:
    """
    将 workflow JSON 文件合并到 planning JSON 的 chatflow_list 中
    
    Args:
        template_data: 模板 planning JSON 数据
        workflow_files: workflow JSON 文件名列表
        step7_dir: step7_final 目录路径
        
    Returns:
        合并后的 planning JSON 数据
    """
    # 获取 chatflow_list
    chatflow_list = template_data.get("planning", {}).get("resource", {}).get("chatflow", {}).get("chatflow_list", [])
    
    if not chatflow_list:
        logger.warning("⚠️  警告: 模板文件中没有找到 chatflow_list，将创建新的列表")
        chatflow_list = []
    
    logger.debug(f"\n📋 当前 chatflow_list 中有 {len(chatflow_list)} 个 workflow")
    logger.debug(f"📦 准备添加 {len(workflow_files)} 个 workflow 文件\n")
    
    # 预先计算 existing_uuids，避免每次循环都重新计算
    existing_uuids = {item.get("flow_uuid") for item in chatflow_list if item.get("flow_uuid")}
    
    added_count = 0
    skipped_count = 0
    
    for workflow_file in sorted(workflow_files):
        workflow_path = os.path.join(step7_dir, workflow_file)
        
        if not os.path.exists(workflow_path):
            logger.warning(f"  ⚠️  文件不存在: {workflow_file}")
            skipped_count += 1
            continue
        
        # 加载 workflow JSON
        workflow_data = load_workflow_json(workflow_path)
        if workflow_data is None:
            skipped_count += 1
            continue

        # 根据目录路径更新 lang 和 llm_model
        detected_lang = None
        if 'step7_final' in step7_dir:
            if os.sep in step7_dir and 'step7_final' in step7_dir:
                parts = step7_dir.split('step7_final')
                if len(parts) > 1:
                    lang_part = parts[1].lstrip(os.sep).split(os.sep)[0]
                    if lang_part in ['en', 'zh', 'zh-hant']:
                        detected_lang = lang_part
                    elif lang_part == 'zh-cn':
                        detected_lang = 'zh'  # zh-cn目录当作zh处理

        if detected_lang:
            # 语言代码已经是规范化的
            normalized_lang = detected_lang

            # 更新 intention_info 中的 lang 和 llm_model
            if "intention_info" in workflow_data:
                workflow_data["intention_info"]["lang"] = normalized_lang
                # llm_model 统一使用 bge-m3
                workflow_data["intention_info"]["llm_model"] = "bge-m3"
                logger.debug(f"  🔧 更新 workflow 语言设置: lang={normalized_lang}, llm_model={workflow_data['intention_info']['llm_model']}")

            # 更新根级别的 lang
            workflow_data["lang"] = normalized_lang
            logger.debug(f"  🔧 更新根级别语言: {normalized_lang}")

            # 保存更新后的 workflow 文件
            try:
                with open(workflow_path, 'w', encoding='utf-8') as f:
                    json.dump(workflow_data, f, ensure_ascii=False)
                logger.debug(f"  💾 已保存更新后的 workflow 文件: {workflow_file}")
            except Exception as e:
                logger.warning(f"  ⚠️  保存 workflow 文件失败 {workflow_file}: {e}")

        # 检查是否已存在（通过 flow_uuid）
        flow_uuid = workflow_data.get("flow_uuid")
        if flow_uuid:
            if flow_uuid in existing_uuids:
                logger.debug(f"  ⏭️  跳过 {workflow_file} (flow_uuid 已存在: {flow_uuid[:8]}...)")
                skipped_count += 1
                continue
            # 添加到 existing_uuids，避免后续重复添加
            existing_uuids.add(flow_uuid)
        
        # 添加 workflow 到 chatflow_list
        chatflow_list.append(workflow_data)
        added_count += 1
        
        flow_name = workflow_data.get("flow_name", "未知")
        logger.debug(f"  ✅ 已添加: {workflow_file} (flow_name: {flow_name})")
    
    # 更新 template_data
    template_data["planning"]["resource"]["chatflow"]["chatflow_list"] = chatflow_list
    
    logger.debug(f"\n📊 合并完成:")
    logger.debug(f"   ✅ 成功添加: {added_count} 个 workflow")
    logger.debug(f"   ⏭️  跳过: {skipped_count} 个 workflow")
    logger.debug(f"   📋 总计: {len(chatflow_list)} 个 workflow 在 chatflow_list 中")
    
    return template_data


def find_exported_flow_file() -> str:
    """
    查找 exported_flow 文件
    优先从 input 文件夹查找，然后从项目根目录查找
    
    Returns:
        exported_flow 文件路径，如果找不到返回 None
    """
    import glob
    
    # 先检查 input 文件夹
    input_dir = "input"
    if os.path.exists(input_dir):
        flow_files = glob.glob(os.path.join(input_dir, 'exported_flow_*.json'))
        if flow_files:
            return flow_files[0]
    
    # 然后检查项目根目录
    flow_files = glob.glob('exported_flow_*.json')
    if flow_files:
        return flow_files[0]
    
    return None


def extract_project_name_from_flow_file(flow_file: str) -> str:
    """
    从 exported_flow 文件名中提取项目名称
    例如: exported_flow_TXNAndSTMT_Deeplink.json -> TXNAndSTMT_Deeplink
    
    Args:
        flow_file: exported_flow 文件路径
        
    Returns:
        项目名称
    """
    if not flow_file:
        return "unknown"
    
    filename = os.path.basename(flow_file)
    
    # 如果文件名包含 'exported_flow_' 前缀，移除它
    if filename.startswith('exported_flow_'):
        name = filename.replace('exported_flow_', '').replace('.json', '')
    else:
        # 如果不是标准格式，尝试从JSON文件内容读取flow名称
        try:
            with open(flow_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 尝试从 flow.displayName 获取名称
                flow_name = data.get('flow', {}).get('flow', {}).get('displayName', '')
                if flow_name:
                    # 规范化名称（移除特殊字符）
                    name = re.sub(r'[^\w\s-]', '', flow_name).strip()
                    name = re.sub(r'[-\s]+', '_', name)
                    logger.debug(f"从文件内容提取项目名称: {name}")
                    return name
        except Exception as e:
            logger.warning(f"无法从文件内容提取项目名称: {e}")
        
        # 如果都失败，使用文件名（移除.json后缀和可能的时间戳/UUID）
        name = filename.replace('.json', '')
        # 如果文件名看起来像 UUID_timestamp 格式，只取第一部分
        if '_' in name and len(name) > 40:  # UUID长度通常是32-36字符
            logger.debug(f"检测到临时文件名格式，使用简化名称")
            name = "migrated_flow"
    
    return name


def main(
    template_json_path: str = None,
    step7_dir: str = None,
    output_path: str = None,
    exported_flow_file: str = None,
    task_output_dir: str = None,
    global_config: Dict[str, Any] = None
):
    """
    主函数：合并 workflow JSON 文件到 planning JSON
    
    Args:
        template_json_path: 模板 planning JSON 文件路径
        step7_dir: step7_final 目录路径
        output_path: 输出文件路径
        exported_flow_file: exported_flow 文件路径（用于提取项目名称）
    """
    logger.info("Step 8: 合并 Workflow 到 Planning JSON")
    
    # 1. 设置默认模板路径（从 input 文件夹读取）
    input_dir = "input"
    if template_json_path is None:
        # 查找 input 文件夹中的 JSON 文件（排除 exported_flow 和 agent 相关文件）
        if os.path.exists(input_dir):
            input_files = [
                f for f in os.listdir(input_dir) 
                if f.endswith('.json') 
                and not f.startswith('exported_flow')
                and not f.startswith('agent')  # 排除所有 agent 开头的文件（agent.json, agent-zh-hant.json等）
                and 'input' not in f.lower()
            ]
            if input_files:
                template_json_path = os.path.join(input_dir, input_files[0])
                logger.debug(f"📄 自动找到模板文件: {template_json_path}")
            else:
                # 如果 input 文件夹为空，使用默认路径
                default_template = os.path.join(input_dir, "自主规划-test-1762829278.json")
                logger.debug(f"⚠️  input 文件夹为空，使用默认路径: {default_template}")
                logger.debug(f"💡 请将模板文件放入 {input_dir}/ 文件夹")
                template_json_path = default_template
        else:
            # 如果 input 文件夹不存在，使用默认路径
            default_template = os.path.join(input_dir, "自主规划-test-1762829278.json")
            logger.debug(f"⚠️  input 文件夹不存在，使用默认路径: {default_template}")
            template_json_path = default_template
    
    if step7_dir is None:
        step7_dir = "output/step7_final"
    
    # 3. 查找 exported_flow 文件以提取项目名称
    if exported_flow_file is None:
        exported_flow_file = find_exported_flow_file()
    
    project_name = "unknown"
    if exported_flow_file:
        project_name = extract_project_name_from_flow_file(exported_flow_file)
        logger.info(f"📋 项目名称: {project_name} (来自 {os.path.basename(exported_flow_file)})")
    else:
        logger.debug(f"⚠️  未找到 exported_flow_*.json 文件，使用默认项目名称")
    
    # 4. 生成输出文件名（基于项目名称和语言）
    if output_path is None:
        template_name = os.path.splitext(os.path.basename(template_json_path))[0]
        
        # 从 step7_dir 路径中提取语言（如果路径包含语言信息）
        lang = None
        if 'step7_final' in step7_dir:
            # 例如: output/step7_final/en -> en
            # 或者: output/step7_final_en -> en (兼容旧格式)
            if os.sep in step7_dir and 'step7_final' in step7_dir:
                # 新格式: output/step7_final/en
                parts = step7_dir.split('step7_final')
                if len(parts) > 1:
                    lang_part = parts[1].lstrip(os.sep).split(os.sep)[0]
                    if lang_part in ['en', 'zh', 'zh-hant']:
                        lang = lang_part
                    elif lang_part == 'zh-cn':
                        lang = 'zh'  # zh-cn目录当作zh处理
            elif 'step7_final_' in step7_dir:
                # 旧格式: output/step7_final_en
                parts = step7_dir.split('step7_final_')
                if len(parts) > 1:
                    lang = parts[1].split(os.sep)[0]
        
        output_dir = "output/step8_final"
        if lang:
            output_dir = os.path.join("output", "step8_final", lang)
        
        os.makedirs(output_dir, exist_ok=True)
        # 使用项目名称和语言（如果有）
        if lang:
            output_path = os.path.join(output_dir, f"{template_name}_{project_name}_{lang}_merged.json")
        else:
            output_path = os.path.join(output_dir, f"{template_name}_{project_name}_merged.json")
    
    # 5. 检查路径
    logger.info(f"📄 使用模板文件: {template_json_path}")
    logger.info(f"📂 扫描 Workflow 目录: {step7_dir}")
    logger.info(f"📁 输出文件: {output_path}")
    logger.debug(f"   输出文件路径长度: {len(output_path)} 字符")
    logger.debug(f"   输出文件绝对路径: {os.path.abspath(output_path) if output_path else 'N/A'}")
    logger.debug(f"   输出目录: {os.path.dirname(output_path) if output_path else 'N/A'}")
    
    if not os.path.exists(template_json_path):
        logger.error(f"❌ 模板文件不存在: {template_json_path}")
        logger.info(f"💡 请将模板文件放入 {input_dir}/ 文件夹")
        logger.info(f"💡 可用的模板文件应该是: agent.json, agent-zh-hant.json, agent-zh-cn.json, agent_EN.json")
        return
    
    if not os.path.exists(step7_dir):
        logger.error(f"❌ step7_final 目录不存在: {step7_dir}")
        return
    
    # 3. 加载模板 JSON
    logger.debug(f"\n🔄 正在加载模板 JSON...")
    try:
        template_data = load_template_json(template_json_path)
    except Exception as e:
        logger.error(f"❌ 加载模板失败，文件可能存在 JSON 格式错误")
        logger.error(f"   模板文件: {template_json_path}")
        logger.error(f"   错误信息: {str(e)}")
        raise

    # 3.1 更新agent的配置
    try:
        # 全局配置微调模型名
        template_data['planning']['basic_config']['intent_sft_model_name'] = global_config.get('sft_model_name', '') if global_config.get('use_sft_model', False) else ""
        # 全局配置chat_model
        template_data['planning']['basic_config']['chat_model'] = global_config.get('llmcodemodel', 'qwen3-30b-a3b')
        # 全局意图Rerank开关与模型
        template_data['planning']['resource']['chatflow']['intent_embedding_rerank_enable'] = True
        # 全局配置LLM Rerank开关与模型
        template_data['planning']['resource']['chatflow']['intent_embedding_llm_enable'] = True
        template_data['planning']['resource']['chatflow']['intent_embedding_llm_model_name'] = global_config.get('llmcodemodel', 'qwen3-30b-a3b')
        template_data['planning']['resource']['chatflow']['intent_final_llm_model_name'] = global_config.get('llmcodemodel', 'qwen3-30b-a3b')
    except Exception as e:
        logger.error(f"❌ 更新Agent全局配置错误: {e}")
        logger.error(f"   错误信息: {str(e)}")
        raise 
    
    # 4. 查找所有 workflow JSON 文件
    logger.debug(f"\n📂 扫描目录: {step7_dir}")
    workflow_files = [
        f for f in os.listdir(step7_dir)
        if f.endswith('.json') and f.startswith('generated_workflow_')
    ]
    
    if not workflow_files:
        logger.warning(f"⚠️  在 {step7_dir} 中未找到 generated_workflow_*.json 文件")
        return
    
    logger.debug(f"✅ 找到 {len(workflow_files)} 个 workflow 文件")
    
    # 5. 合并 workflow 到 planning JSON
    try:
        merged_data = merge_workflows_to_planning(template_data, workflow_files, step7_dir)
        if merged_data is None:
            logger.error("❌ merge_workflows_to_planning 返回 None，无法继续")
            return
        logger.debug(f"✅ 合并完成，merged_data 类型: {type(merged_data)}")
    except Exception as e:
        logger.error(f"❌ 合并 workflow 失败: {e}")
        import traceback
        logger.error(f"   错误详情:\n{traceback.format_exc()}")
        return
    
    # 6. 自动创建 intention_list（如果为空）
    logger.debug(f"\n📝 检查并创建 intention_list...")
    intention_list = merged_data.get("planning", {}).get("resource", {}).get("intention_list", [])
    
    if not intention_list:
        logger.debug(f"  📝 模板文件中 intention_list 为空，根据 chatflow_list 自动创建...")
        merged_data = create_intention_list_from_chatflow(merged_data, global_config)
        intention_list = merged_data.get("planning", {}).get("resource", {}).get("intention_list", [])
        
        if intention_list:
            logger.debug(f"  ✅ 自动创建了 {len(intention_list)} 个 intention")
        else:
            logger.warning(f"  ⚠️  警告: 无法创建 intention_list（chatflow_list 可能为空）")
    else:
        logger.debug(f"  ✅ intention_list 已存在，包含 {len(intention_list)} 个 intention")
    
    # 7. 更新 positive_examples
    logger.debug(f"\n📝 更新 positive_examples...")
    
    # 使用传入的 exported_flow_file 或重新查找
    if exported_flow_file is None:
        exported_flow_file = find_exported_flow_file()
    
    # 构建 intents 文件路径（支持 task_id 隔离目录）
    if task_output_dir:
        # 新格式：output/{task_id}/step1_processed/intents_*.json
        # 从 step7_dir 推断语言（如果可能）
        detected_lang = 'en'  # 默认值
        if 'step7_final' in step7_dir:
            # 尝试从路径中提取语言
            if os.sep in step7_dir and 'step7_final' in step7_dir:
                # 例如: output/b45a06bc.../step7_final/en -> en
                parts = step7_dir.split('step7_final')
                if len(parts) > 1:
                    lang_part = parts[1].lstrip(os.sep).split(os.sep)[0]
                    if lang_part in ['en', 'zh', 'zh-hant']:
                        detected_lang = lang_part
                    elif lang_part == 'zh-cn':
                        detected_lang = 'zh'  # zh-cn目录当作zh处理
            elif 'step7_final_' in step7_dir:
                # 旧格式: output/step7_final_en
                parts = step7_dir.split('step7_final_')
                if len(parts) > 1:
                    lang_part = parts[1].split(os.sep)[0]
                    if lang_part in ['en', 'zh', 'zh-hant']:
                        detected_lang = lang_part
                    elif lang_part == 'zh-cn':
                        detected_lang = 'zh'  # zh-cn目录当作zh处理
        
        intents_files = [
            os.path.join(task_output_dir, 'step1_processed', f'intents_{detected_lang}.json'),
            os.path.join(task_output_dir, 'step1_processed', 'intents_en.json'),
            os.path.join(task_output_dir, 'step1_processed', 'intents_zh.json'),
            os.path.join(task_output_dir, 'step1_processed', 'intents_zh-hant.json'),
            os.path.join(task_output_dir, 'step0_extracted', 'intents.json'),
        ]
    else:
        # 旧格式：output/step1_processed/intents_*.json（兼容）
        intents_files = [
            'output/step1_processed/intents_en.json',
            'output/step0_extracted/intents.json',
            'intents.json'
        ]
    
    intents_file = None
    for f in intents_files:
        if os.path.exists(f):
            intents_file = f
            logger.debug(f"   找到 intents 文件: {f}")
            break
    
    if not intents_file:
        logger.warning(f"   ⚠️  未找到 intents 文件，尝试的路径:")
        for f in intents_files:
            logger.debug(f"      - {f}")
    
    if exported_flow_file and intents_file:
        logger.debug(f"   使用 exported_flow: {exported_flow_file}")
        logger.debug(f"   使用 intents: {intents_file}")
        
        # 加载 intents 映射
        intents_mapping = load_intents_mapping(intents_file)
        
        # 获取 flow 层级的意图（包含相似问）
        # 返回格式: {'by_name': {...}, 'by_index': {...}}
        intent_name_to_info = get_flow_intents_from_exported_flow(exported_flow_file, intents_mapping)
        
        # 更新 positive_examples
        merged_data = update_positive_examples(
            merged_data,
            workflow_files,
            step7_dir,
            intent_name_to_info,
            global_config
        )
    else:
        logger.warning(f"  ⚠️  未找到 exported_flow 或 intents 文件，跳过 positive_examples 更新")
        if not exported_flow_file:
            logger.warning(f"     - 未找到 exported_flow_*.json")
        if not intents_file:
            logger.warning(f"     - 未找到 intents.json")
    
    # 7. 替换 agent_info.description 和 basic_config.robot_name 中的 "test" 为文件名
    output_filename = os.path.splitext(os.path.basename(output_path))[0]  # 获取文件名（不含扩展名）
    agent_info = merged_data.get("planning", {}).get("agent_info", {})
    basic_config = merged_data.get("planning", {}).get("basic_config", {})
    
    if agent_info.get("description") == "test":
        agent_info["description"] = output_filename
        logger.info(f"  🔧 更新 agent_info.description: test → {output_filename}")
    
    if basic_config.get("robot_name") == "test":
        basic_config["robot_name"] = output_filename
        logger.info(f"  🔧 更新 basic_config.robot_name: test → {output_filename}")
    
    # 7.1 统一 emb_language 字段：如果为 "english" 则改为 "en"
    logger.debug(f"\n📝 检查并规范 emb_language 字段（english → en）...")
    normalize_emb_language(merged_data)
    logger.debug(f"  ✅ emb_language 规范化完成")
    
    # 7.2 将 variable_数字 替换为指定名称
    # write by senlin.deng 2025-12-23, updated 2025-12-29
    # 分两步处理：
    # 1. 先找出指定类型节点中的 variable_数字 变量名，替换为自定义名称
    # 2. 其他 variable_数字 替换为 last_user_response
    
    pattern = r"^variable_\d+$"  # 匹配 variable_ 后跟数字的变量名
    
    # Step 1: 查找指定类型节点中的变量名
    # 可配置要筛选的节点类型列表，如 ["knowledgeAssignment", "code", "llmVariableAssignment"]
    target_node_types = ["knowledgeAssignment"]  # 可自定义节点类型
    kb_variables = find_variables_by_node_type(merged_data, target_node_types, pattern)
    if kb_variables:
        # 这些节点的变量替换为自定义名称（可修改此处的替换名称）
        kb_replacement = "KB_response"  # 变量的替换名称，可自定义
        # 复用 replace_variable_pattern，将变量名集合转换为精确匹配的正则模式
        kb_pattern = build_exact_match_pattern(kb_variables)
        kb_replace_count = replace_variable_pattern(merged_data, kb_pattern, kb_replacement)
        logger.info(f"  ✅ 节点变量替换完成: {kb_variables} → {kb_replacement}，共替换 {kb_replace_count} 处")
    else:
        logger.debug(f"\n📚 未发现 {target_node_types} 节点中的 variable_数字 变量")
    
    # 大模型赋值变量替换
    target_node_types = ["llmVariableAssignment"]  # 可自定义节点类型
    llm_variables = find_variables_by_node_type(merged_data, target_node_types, pattern)
    if llm_variables:
        # 这些节点的变量替换为自定义名称（可修改此处的替换名称）
        llm_replacement = "LLM_response"  # 变量的替换名称，可自定义
        # 复用 replace_variable_pattern，将变量名集合转换为精确匹配的正则模式
        llm_pattern = build_exact_match_pattern(llm_variables)
        llm_replace_count = replace_variable_pattern(merged_data, llm_pattern, llm_replacement)
        logger.info(f"  ✅ 节点变量替换完成: {llm_variables} → {llm_replacement}，共替换 {llm_replace_count} 处")
    else:
        logger.debug(f"\n📚 未发现 {target_node_types} 节点中的 variable_数字 变量")

    # Step 2: 其他 variable_数字 替换为 last_user_response
    other_replace_count = replace_variable_pattern(
        merged_data, 
        pattern=pattern,
        replacement="last_user_response"
    )
    logger.info(f"  ✅ 其他变量名替换完成，共替换 {other_replace_count} 处")
    
    # # 调试：检查替换是否生效（可以注释掉）
    # if replace_count == 0:
    #     logger.warning(f"  ⚠️ 没有找到匹配 variable_数字 的变量名，请检查数据结构")
    
    # 7.3 对 chatflow_list 中的变量进行去重
    # write by senlin.deng 2025-12-24
    logger.info(f"\n📝 处理变量去重: 根据 variable_name 去重...")
    dedup_count = deduplicate_variables_in_chatflow_list(merged_data)
    logger.info(f"  ✅ 变量去重完成，共去除 {dedup_count} 个重复变量")
    
    # 8. 保存合并后的 JSON
    logger.debug(f"\n💾 保存合并后的 JSON 文件...")
    
    # 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            logger.debug(f"✅ 输出目录已创建/存在: {output_dir}")
        except Exception as e:
            logger.error(f"❌ 创建输出目录失败: {output_dir}")
            logger.error(f"   错误: {e}")
            return
    
    # 规范化路径（解决 Windows 路径问题）
    output_path = os.path.normpath(output_path)
    logger.debug(f"📁 规范化后的输出路径: {output_path}")
    
    # 检查并缩短文件名（Windows 路径限制 260 字符，但实际可能更短）
    abs_output_path = os.path.abspath(output_path)
    if len(abs_output_path) > 250:  # 留一些余量
        logger.warning(f"⚠️  文件路径过长 ({len(abs_output_path)} 字符)，Windows 可能无法创建")
        logger.warning(f"   尝试缩短文件名...")
        # 缩短文件名：保留前50个字符 + 后缀
        dir_part = os.path.dirname(output_path)
        file_part = os.path.basename(output_path)
        name_part, ext = os.path.splitext(file_part)
        # 如果文件名太长，截断并添加哈希值
        if len(name_part) > 100:
            import hashlib
            name_hash = hashlib.md5(name_part.encode()).hexdigest()[:8]
            name_part = name_part[:80] + '_' + name_hash
        file_part = name_part + ext
        output_path = os.path.join(dir_part, file_part)
        abs_output_path = os.path.abspath(output_path)
        logger.info(f"   新路径: {output_path} (长度: {len(abs_output_path)})")
    
    # 最终验证目录是否存在
    final_output_dir = os.path.dirname(abs_output_path)
    if not os.path.exists(final_output_dir):
        logger.error(f"❌ 输出目录不存在: {final_output_dir}")
        logger.error(f"   尝试创建目录...")
        try:
            os.makedirs(final_output_dir, exist_ok=True)
            if not os.path.exists(final_output_dir):
                logger.error(f"❌ 目录创建失败: {final_output_dir}")
                return
            logger.info(f"✅ 目录创建成功: {final_output_dir}")
        except Exception as e:
            logger.error(f"❌ 创建目录失败: {e}")
            return
    
    try:
        # 使用绝对路径确保文件可以创建
        # 注意：abs_output_path 可能已经在上面计算过了
        if 'abs_output_path' not in locals() or abs_output_path is None:
            abs_output_path = os.path.abspath(output_path)
        logger.debug(f"📁 绝对路径: {abs_output_path}")
        logger.debug(f"📁 路径长度: {len(abs_output_path)} 字符")
        logger.debug(f"📁 文件名: {os.path.basename(abs_output_path)}")
        
        # 确保父目录存在（再次检查，防止并发问题）
        parent_dir = os.path.dirname(abs_output_path)
        if not os.path.exists(parent_dir):
            logger.warning(f"⚠️  父目录不存在，尝试创建: {parent_dir}")
            os.makedirs(parent_dir, exist_ok=True)
        
        # 尝试创建文件（先以写入模式打开，确保可以创建）
        logger.info(f"📝 开始写入文件到: {abs_output_path}")
        logger.debug(f"   父目录: {parent_dir}")
        logger.debug(f"   父目录存在: {os.path.exists(parent_dir)}")
        
        # 确保目录存在
        os.makedirs(parent_dir, exist_ok=True)
        
        # 写入文件
        logger.debug(f"   准备写入 JSON 数据（大小约: {len(json.dumps(merged_data, ensure_ascii=False))} 字符）")
        with open(abs_output_path, 'w', encoding='utf-8') as f:
            json.dump(merged_data, f, ensure_ascii=False)
            # 确保数据写入磁盘
            f.flush()
            os.fsync(f.fileno())
        
        # 强制刷新文件系统缓存
        import sys
        sys.stdout.flush()
        
        # 验证文件是否真的创建成功
        if os.path.exists(abs_output_path):
            file_size = os.path.getsize(abs_output_path)
            logger.info(f"✅ 成功保存到: {output_path} (大小: {file_size} 字节)")
            logger.debug(f"   文件路径: {abs_output_path}")
        else:
            logger.error(f"❌ 文件写入后不存在: {abs_output_path}")
            logger.error(f"   父目录: {parent_dir}")
            logger.error(f"   父目录存在: {os.path.exists(parent_dir)}")
            logger.error(f"   尝试列出目录内容: {os.listdir(parent_dir) if os.path.exists(parent_dir) else '目录不存在'}")
            return
    except FileNotFoundError as e:
        logger.error(f"❌ 保存文件失败（文件或目录不存在）: {e}")
        logger.error(f"   输出路径: {output_path}")
        logger.error(f"   绝对路径: {os.path.abspath(output_path) if output_path else 'N/A'}")
        logger.error(f"   输出目录: {final_output_dir}")
        logger.error(f"   目录是否存在: {os.path.exists(final_output_dir) if final_output_dir else 'N/A'}")
        import traceback
        logger.error(f"   错误详情:\n{traceback.format_exc()}")
        return
    except PermissionError as e:
        logger.error(f"❌ 保存文件失败（权限不足）: {e}")
        logger.error(f"   输出路径: {output_path}")
        return
    except Exception as e:
        logger.error(f"❌ 保存文件失败: {e}")
        logger.error(f"   输出路径: {output_path}")
        import traceback
        logger.error(f"   错误详情:\n{traceback.format_exc()}")
        return
    
    logger.info("\n" + "=" * 70)
    logger.info("🎉 Step 8 完成！")
    logger.info("=" * 70)
    logger.info(f"📄 输出文件: {output_path}")
    logger.info(f"📋 chatflow_list 中包含 {len(merged_data['planning']['resource']['chatflow']['chatflow_list'])} 个 workflow")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()

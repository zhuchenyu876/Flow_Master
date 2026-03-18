"""
工作流生成器
===========

从配置生成完整的 workflow JSON 文件

作者：chenyu.zhu
日期：2025-12-17
"""

import uuid
import json
import base64

from logger_config import get_logger
logger = get_logger(__name__)


def gen_id():
    return str(uuid.uuid4())

def short_id():
    raw = uuid.uuid4().bytes
    return base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=\n')


def _get_fallback_message(lang: str = 'en') -> str:
    """
    根据语言获取 fallback 消息

    Args:
        lang: 语言代码

    Returns:
        fallback 消息文本
    """
    # 根据语言返回对应的 fallback 消息
    fallback_messages = {
        'zh': "抱歉，我未能完全理解您的意思，您可以再详细说明一下吗？",
        'zh-hk': "唔好意思，我未能理解你嘅意思，可以再讲详细啲吗？",
        'zh-hant': "唔好意思，我未能理解你嘅意思，可以再讲详细啲吗？",
        'en': "Sorry, I didn't quite understand that. Could you please clarify your request?",
    }

    # 默认返回英文
    return fallback_messages.get(lang, fallback_messages['en'])


def _update_existing_fallback_messages(workflow: dict, language: str) -> None:
    """
    更新工作流中现有的英文fallback消息为本地化版本

    Args:
        workflow: 工作流数据
        language: 目标语言代码
    """
    # 英文fallback消息映射
    english_fallbacks = {
        "I didn't get that. Can you repeat?": _get_fallback_message(language),
        "Sorry, I didn't get that. Can you rephrase?": _get_fallback_message(language),
        "I didn't get that. Can you say it again?": _get_fallback_message(language),
    }

    # 遍历所有节点，更新文本内容
    nodes = workflow.get('nodes', [])
    for node in nodes:
        if node.get('type') == 'textReply':
            config = node.get('config', {})
            plain_text = config.get('plain_text', [])
            for text_item in plain_text:
                text_content = text_item.get('text', '')
                # 检查是否包含英文fallback消息
                for english_msg, localized_msg in english_fallbacks.items():
                    if f"'text': '{english_msg}'" in text_content:
                        updated_text = text_content.replace(
                            f"'text': '{english_msg}'",
                            f"'text': '{localized_msg}'"
                        )
                        text_item['text'] = updated_text
                        logger.debug(f"Updated fallback message from '{english_msg}' to '{localized_msg}'")
                        break


# ============= Helpers: Rich mention span for variables =============
def _to_rich_span(var_name: str) -> str:
     return (
        f'<span class="rich-mention__span" data-id="{var_name}" '
        f'data-name="{{{{{var_name}}}}}"" contenteditable="false" '
        f'style="color: rgb(55, 171, 255); user-select: all;">{{{{{var_name}}}}}</span>'
    )

def _extract_var_name(s):
    if not isinstance(s, str):
        return None
    text = s.strip()
    if not text.startswith("$"):
        return None
    # 支持 $delay 或 $session.params.delay → 取最后一段作为变量名
    return text[1:].split(".")[-1]


def _transform_vars(obj):
    # 递归把字符串中的 $var / $a.b.c 转成 rich-mention span
    if isinstance(obj, dict):
        return {k: _transform_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_transform_vars(x) for x in obj]
    if isinstance(obj, str):
        var = _extract_var_name(obj)
        return _to_rich_span(var) if var else obj
    return obj


class NodePositionManager:
    """节点位置管理器，自动计算节点位置避免重叠"""

    def __init__(self, start_x=100, start_y=1089, spacing_x=400, spacing_y=300):
        self.start_x = start_x
        self.start_y = start_y
        self.spacing_x = spacing_x
        self.spacing_y = spacing_y
        self.nodes_per_row = 10  # 每行最多10个节点
        self.current_index = 0

        # 特殊节点的固定位置
        self.special_positions = {
            "start": {"x": -187, "y": 1124}
        }

    def get_position(self, node_type="default", node_index=None):
        """
        获取节点位置
        :param node_type: 节点类型，start节点有固定位置
        :param node_index: 节点索引，用于计算位置
        :return: 位置字典 {"x": x, "y": y}
        """
        if node_type == "start":
            return self.special_positions["start"].copy()

        # 使用传入的索引或当前索引
        index = node_index if node_index is not None else self.current_index

        # 计算行列位置
        row = index // self.nodes_per_row
        col = index % self.nodes_per_row

        # 计算坐标
        x = self.start_x + col * self.spacing_x
        y = self.start_y + row * self.spacing_y

        # 更新当前索引
        if node_index is None:
            self.current_index += 1

        return {"x": x, "y": y}

    def reset(self):
        """重置索引计数器"""
        self.current_index = 0


class VariableManager:
    """变量管理器，处理节点间的变量传递"""

    def __init__(self):
        self.variables = {}  # 存储所有变量
        self.node_outputs = {}  # 存储每个节点的输出变量

    def register_variable(self, var_name, var_type="text", description="", from_node=None, lang="en"):
        """注册一个变量"""
        self.variables[var_name] = {
            "variable_name": var_name,
            "type": var_type,
            "description": description,
            "from_node": from_node,
            "lang": lang
        }

    def register_node_output(self, node_id, output_vars, lang="en"):
        """注册节点的输出变量"""
        if isinstance(output_vars, str):
            output_vars = [output_vars]
        self.node_outputs[node_id] = output_vars

        # 同时注册到全局变量
        for var in output_vars:
            self.register_variable(var, from_node=node_id, lang=lang)

    def get_all_variables(self):
        """获取所有变量的列表格式（去重，确保每个变量名只出现一次）"""
        # VariableManager 使用字典存储，理论上不应该有重复
        # 但为了安全起见，确保返回的列表中每个 variable_name 都是唯一的
        seen_names = set()
        result = []
        for var_dict in self.variables.values():
            var_name = var_dict.get('variable_name')
            if var_name and var_name not in seen_names:
                seen_names.add(var_name)
                result.append(var_dict)
        return result


class EdgeManager:
    """边管理器，处理节点间的连接关系"""

    def __init__(self):
        self.edges = []
        self.condition_mappings = {}  # 存储condition node的condition_id映射
        self.semantic_judgment_with_pure_conditions = {}  # 存储有纯条件路由的 semanticJudgment 节点信息

    def register_condition_node(self, node_name, condition_mappings):
        """注册condition节点的condition_id映射"""
        self.condition_mappings[node_name] = condition_mappings
    
    def register_semantic_judgment_with_pure_conditions(self, node_name, fallback_direct_target, pure_condition_node):
        """
        注册有纯条件路由的 semanticJudgment 节点
        
        Args:
            node_name: semanticJudgment 节点名称
            fallback_direct_target: Fallback 分支的直接目标节点（通常是 Pure Condition Routing 节点）
            pure_condition_node: 纯条件判断节点名称（与 fallback_direct_target 相同）
        """
        self.semantic_judgment_with_pure_conditions[node_name] = {
            "fallback_direct_target": fallback_direct_target,
            "pure_condition_node": pure_condition_node
        }

    def create_edge_from_config(self, edge_config, node_name_map):
        """根据配置创建edge（支持node名称）"""
        source_name = edge_config.get("source_node")
        target_name = edge_config.get("target_node")
        connection_type = edge_config.get("connection_type", "default")

        # 检查节点名称是否存在
        if source_name not in node_name_map or target_name not in node_name_map:
            # writed by senlin.deng 2026-01-17
            # 忽略无效的节点名称，避免生成错误的边
            # logger.warning(f"Invalid node names in edge config: {source_name} -> {target_name}")
            return None

        source = node_name_map[source_name]
        target = node_name_map[target_name]

        if connection_type == "condition":
            # 处理condition连接
            condition_id = edge_config.get("condition_id")
            if not condition_id:
                logger.warning(f"Missing condition_id for condition edge: {source_name} -> {target_name}")
                return None
            return self._create_condition_edge(source, target, condition_id)
        else:
            # 默认连接
            return self._create_default_edge(source, target)

    def _create_default_edge(self, source, target):
        """创建默认edge"""
        edge_id = f"vueflow__edge-{source['node']['id']}{source['handle']}-{target['node']['id']}{target['target_handle']}"

        return {
            "id": edge_id,
            "type": "custom",
            "source": source["node"]["id"],
            "target": target["node"]["id"],
            "sourceHandle": source["handle"],
            "targetHandle": target["target_handle"],
            "data": {
                "hovering": False
            },
            "label": "",
            "sourceX": source["node"]["position"]["x"] + 100,
            "sourceY": source["node"]["position"]["y"] + 50,
            "targetX": target["node"]["position"]["x"] + 100,
            "targetY": target["node"]["position"]["y"] + 50,
            "zIndex": 0,
            "animated": False
        }

    def _create_condition_edge(self, source, target, condition_id):
        """创建condition edge，使用内部句柄ID，而非业务性condition_id字符串"""
        # 将业务性的 condition_id（例如 intent_1）映射为该条件节点的内部句柄ID
        handle_id = condition_id
        mappings = source.get("condition_mappings", {})
        if condition_id in mappings:
            handle_id = mappings[condition_id]

        edge_id = f"vueflow__edge-{source['node']['id']}{handle_id}-{target['node']['id']}{target['target_handle']}"

        return {
            "id": edge_id,
            "type": "custom",
            "source": source["node"]["id"],
            "target": target["node"]["id"],
            "sourceHandle": handle_id,
            "targetHandle": target["target_handle"],
            "data": {
                "hovering": False
            },
            "label": "",
            "sourceX": source["node"]["position"]["x"] + 100,
            "sourceY": source["node"]["position"]["y"] + 50,
            "targetX": target["node"]["position"]["x"] + 100,
            "targetY": target["node"]["position"]["y"] + 50,
            "zIndex": 0,
            "animated": False
        }


def main(
    workflow_config='workflow_config.json',
    nodes_config='nodes_config.json',
    variables_config='variables.json',
    edge_config='edge_config.json',
    output_file='generated_workflow.json',
    language='en',
    global_configs={}
):
    """
    根据4个JSON配置文件生成完整的workflow

    Args:
        workflow_config: 工作流配置文件路径（包含workflow_name和workflow_info）
        nodes_config: 节点配置文件路径（包含nodes）
        variables_config: 变量配置文件路径（包含variables）
        edge_config: 边配置文件路径（包含edges）
        output_file: 输出文件路径（默认为generated_workflow.json）

    Returns:
        完整的workflow字典
    """

    # 读取workflow配置
    with open(workflow_config, 'r', encoding='utf-8') as f:
        workflow_data = json.load(f)
    
    # 读取nodes配置
    with open(nodes_config, 'r', encoding='utf-8') as f:
        nodes_data = json.load(f)
    
    # 读取variables配置
    with open(variables_config, 'r', encoding='utf-8') as f:
        variables_data = json.load(f)
    
    # 读取edge配置
    with open(edge_config, 'r', encoding='utf-8') as f:
        edges_config = json.load(f)
    
    # 合并配置
    config = {
        "workflow_name": workflow_data.get("workflow_name", "Generated Workflow"),
        "workflow_info": workflow_data.get("workflow_info", {}),
        "nodes": nodes_data.get("nodes", []),
        "variables": variables_data.get("variables", {})
    }

    # 初始化管理器
    position_manager = NodePositionManager()
    variable_manager = VariableManager()
    edge_manager = EdgeManager()

    # 获取配置信息
    node_configs = config.get("nodes", [])
    workflow_name = config.get("workflow_name", "Generated Workflow")
    workflow_info = config.get("workflow_info", {})

    # 处理用户定义的变量（step4生成的variables.json现在包含lang字段）
    user_variables = config.get("variables", {})
    for var_name, var_info in user_variables.items():
        if isinstance(var_info, str):
            # 向后兼容：旧格式的variables.json
            variable_manager.register_variable(var_name, description=var_info, lang=language)
        elif isinstance(var_info, dict):
            # step4生成的完整变量配置，包含lang字段
            variable_manager.register_variable(
                var_name,
                var_type=var_info.get("type", "text"),
                description=var_info.get("description", var_name),
                lang=var_info.get("lang", language)  # 优先使用step4设置的lang
            )

    # 固定的模板结构
    intention_uuid = gen_id()
    workflow_template = {
        "created_by": workflow_info.get("created_by", "chenyu.zhu@brgroup.com"),
        "modified_by": workflow_info.get("modified_by", "chenyu.zhu@brgroup.com"),
        "flow_uuid": gen_id(),
        "start_node_uuid": "start00000000000000000000",
        "intention_uuid": intention_uuid,
        "flow_name": workflow_name,
        "description": workflow_info.get("description", f"Auto-generated workflow: {workflow_name}"),
        "nodes": [],
        "edges": [],
        "buttons": [],
        "config": {
            "position": [0, 0],
            "zoom": 1,
            "viewport": {"x": 0, "y": 0, "zoom": 1}
        },
        "intention_info": {
            "created_by": workflow_info.get("created_by", "chenyu.zhu@brgroup.com"),
            "modified_by": workflow_info.get("modified_by", "chenyu.zhu@brgroup.com"),
            "intention_uuid": intention_uuid,
            "intention_name": workflow_info.get("intention_name", workflow_name),
            "description": workflow_info.get("intent_description", f"Auto-generated workflow: {workflow_name}"),
            "positive_examples": workflow_info.get("positive_examples", [
                {"id": short_id(), "value": "credit review report"}
            ]),
            "negative_examples": workflow_info.get("negative_examples", [
                {"id": short_id(), "value": "give some suggestion"}
            ]),
            "lang": language,
            "llm_model": "bge-m3",
            "copy_count": 2,
            "is_official": True,
            "is_store": False,
            "tag_id": 4,
            "release_version": "chatflow_20250516170541569435_001",
            "store_version": "chatflow_20250222003042441979_002",
            "release_time": "2025-02-22 00:31:54",
            "release_user": 211
        },
        "entities": [],
        "lang": language,
        "variables": [],
        "categories": [],
        "position": [0, 0],
        "zoom": 1,
        "viewport": {"x": 0, "y": 0, "zoom": 1}
    }

    # 内部函数：创建节点
    def _create_llm_variable_assignment(config, node_index, language):
        node_id = gen_id()
        source_handle = gen_id()
        block_id = gen_id()
        position = position_manager.get_position("default", node_index)

        # 处理变量分配
        variable_assign = config.get("variable_assign", f"result_{node_index}")
        variable_manager.register_node_output(node_id, variable_assign, lang=language)

        func_node = {
            "id": node_id,
            "type": "llmVariableAssignment",
            "initialized": False,
            "position": {"x": -96, "y": 244},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "title": config.get("title", f"LLM Variable Assignment {node_index}"),
                "prompt_template": config.get("prompt_template", ""),
                "variable_assign": variable_assign,
                "llm_config": {
                    "rag_correlation_threshold": config.get("rag_correlation_threshold", 65),
                    "rag_max_reference_knowledge_num": config.get("rag_max_reference_knowledge_num", 3),
                    "divergence": config.get("divergence", 2),
                    "prompt": config.get("prompt", ""),
                    "llm_name": config.get("llm_name", "azure-gpt-4o"),
                    "rag_question": "<span class=\"rich-mention__span\" data-id=\"last_user_response\" data-name=\"{{last_user_response}}\" contenteditable=\"false\" style=\"color: rgb(55, 171, 255); user-select: all;\">{{last_user_response}}</span>",  # 强制使用 rich-mention 格式
                    "rag_range": "",
                    "rag_enabled": "",
                    "knowledge_base_ids": [int(kb_id) if isinstance(kb_id, str) else kb_id for kb_id in config.get("knowledge_base_ids", [])],
                    "ai_tag_list": [],
                    "knowledge_search_flag": config.get("knowledge_search_flag", False),
                    "chat_history_flag": global_configs.get("enable_short_memory", False),
                    "chat_history_count": global_configs.get("short_chat_count", 5),
                    "ltm_enabled": False,
                    "ltm_search_range": "0",
                    "ltm_robot_ids": [],
                    "ltm_question": "",
                    "ltm_recall_count": 5
                },
                "desc": config.get("desc", config.get("title", f"LLM Variable Assignment {node_index}"))
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": config.get("title", f"LLM Variable Assignment {node_index}"),
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        return func_node, block_node, node_id

    def _create_llm_reply(config, node_index):
        node_id = gen_id()
        source_handle = gen_id()
        block_id = gen_id()
        position = position_manager.get_position("default", node_index)

        func_node = {
            "id": node_id,
            "type": "llMReply",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "desc": config.get("description", ""),
                "prompt_template": config.get("prompt_template", ""),
                "llm_config": {
                    "rag_correlation_threshold": config.get("rag_correlation_threshold", 65),
                    "rag_max_reference_knowledge_num": 3,
                    "divergence": 2,
                    "prompt": "",
                    "llm_name": config.get("llm_name", "Fix2-72B-Instruct-AWQ"),
                    "rag_question": "",
                    "rag_range": "",
                    "rag_enabled": "",
                    "knowledge_base_ids": [],
                    "knowledge_search_flag": False,
                    "chat_history_flag": global_configs.get("enable_short_memory", False),
                    "chat_history_count": global_configs.get("short_chat_count", 5),
                    "ltm_enabled": False,
                    "ltm_search_range": "0",
                    "ltm_robot_ids": [],
                    "ltm_question": "",
                    "ltm_recall_count": 5,
                    "verify_enable": False,
                    "verify_count": 5,
                    "verify_constraints": "",
                    "main_condition_id": gen_id(),
                    "other_condition_id": gen_id()
                },
                "title": config.get("title", f"LLM Reply {node_index}")
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": config.get("title", f"LLM Reply {node_index}"),
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        return func_node, block_node, node_id

    def _create_knowledge_assignment(config, node_index, language):
        node_id = gen_id()
        source_handle = gen_id()
        block_id = gen_id()
        position = position_manager.get_position("default", node_index)

        # 处理变量分配
        variable_assign = config.get("variable_assign", f"knowledge_result_{node_index}")
        variable_manager.register_node_output(node_id, variable_assign, lang=language)

        func_node = {
            "id": node_id,
            "type": "knowledgeAssignment",
            "initialized": False,
            "position": {"x": -534, "y": 1928},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "title": config.get("title", f"Knowledge Assignment {node_index}"),
                "variable_assign": variable_assign,
                "rag_config": {
                    "rag_correlation_threshold": config.get("rag_correlation_threshold", 65),
                    "rag_max_reference_knowledge_num": config.get("rag_max_reference_knowledge_num", 1),
                    "rag_question": "<span class=\"rich-mention__span\" data-id=\"last_user_response\" data-name=\"{{last_user_response}}\" contenteditable=\"false\" style=\"color: rgb(55, 171, 255); user-select: all;\">{{last_user_response}}</span>",  # 强制使用 rich-mention 格式
                    "rag_range": "",
                    "knowledge_base_ids": [int(kb_id) if isinstance(kb_id, str) else kb_id for kb_id in config.get("knowledge_base_ids", [])],
                    "ai_tag_list": [],
                    "knowledge_search_flag": config.get("knowledge_search_flag", True),
                    "keyword_enable": False,
                    "keywords": "",
                    "page_intents": config.get("page_intents", [])  # 保留 page_intents 字段
                },
                "desc": config.get("desc", config.get("title", f"Knowledge Assignment {node_index}"))
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": config.get("title", f"Knowledge Assignment {node_index}"),
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        return func_node, block_node, node_id

    def _create_code_node(config, node_index, language):
        node_id = gen_id()
        source_handle = gen_id()
        block_id = gen_id()
        position = position_manager.get_position("default", node_index)

        # 处理输出变量
        outputs = config.get("outputs", [])
        formatted_outputs = []
        # writed by senlin.deng 2026-01-14
        # 判断是否为 setParameterActions 生成的 code 节点（通过 title 判断）
        # 用于处理：preset 与 路由中的preset变量名敏感问题，输入参数也需要注意
        title = config.get("title", "")
        is_setparameter_node = (
            title.startswith("VariableAssignment_") or 
            title.startswith("Set Parameters") or
            title.startswith("Set Parameters before transition") or
            title.startswith("ExpressionEval") or
            title.startswith("Parse Slot Parameters")
        )
        
        if outputs:
            for output in outputs:
                if isinstance(output, str):
                    # 如果是 setParameterActions 生成的节点，将 variable_assign 转换为小写
                    variable_assign_value = output.lower() if is_setparameter_node else output
                    formatted_outputs.append({
                        "name": output,
                        "type": "string",
                        "variable_assign": variable_assign_value
                    })
                elif isinstance(output, dict):
                    formatted_outputs.append(output)
            variable_manager.register_node_output(node_id, [out.get("variable_assign", out.get("name")) for out in
                                                            formatted_outputs], lang=language)

        # 处理参数
        args = config.get("args", [])
        formatted_args = []
        if args:
            for arg in args:
                if isinstance(arg, str):
                    # 如果是 setParameterActions 生成的节点，将 arg 转换为小写
                    arg_value = arg.lower() if is_setparameter_node else arg
                    formatted_args.append({
                        "name": arg,
                        "default_value": f"​<span class=\"rich-mention__span\" data-id=\"{arg}\" data-name=\"{{{{{arg_value}}}}}\" contenteditable=\"false\" style=\"color: rgb(55, 171, 255); user-select: all;\">{{{{{arg_value}}}}}</span>​",
                        "type": "string"
                    })
                elif isinstance(arg, dict):
                    formatted_args.append(arg)

        # 修复代码内容
        original_code = config.get("code", "")
        
        # 检查代码是否已经是完整的函数
        # 需要检查是否包含 "def main" (可能前面有 import 语句)
        code_lines = [line for line in original_code.split('\n') if line.strip()]
        has_def_main = any(line.strip().startswith("def main") for line in code_lines)

        # 如果用户提供的代码不是完整的函数，则包装成完整的main函数
        if not has_def_main:
            # 构建参数列表
            param_list = ", ".join([f"{arg}: str" for arg in args]) if args else ""

            # 构建返回字典的键值对
            return_items = []
            for output in outputs:
                var_name = output if isinstance(output, str) else output.get("name", "")
                return_items.append(f'        "{var_name}": {var_name}')

            return_dict = "{\n" + ",\n".join(return_items) + "\n    }"

            # 检查是否需要 import re (用于 LLM JSON 解析)
            needs_import_re = "re.findall" in original_code or "re.search" in original_code
            imports = "import re\n" if needs_import_re else ""

            # write by senlin.deng 2026-01-29
            # 检查输入参数中哪些需要 eval()（用于 .get() 操作的字典参数）
            # 如果代码中有 变量名.get( 的模式，且该变量是输入参数，则需要先 eval
            eval_lines = []
            for arg in args:
                # 检查代码中是否有 arg.get( 的模式
                if f"{arg}.get(" in original_code:
                    eval_lines.append(f"    try:")
                    eval_lines.append(f"        {arg} = eval({arg})")
                    eval_lines.append(f"    except:")
                    eval_lines.append(f"        {arg} = {{}}")
            
            eval_block = "\n".join(eval_lines) + "\n" if eval_lines else ""

            # 包装成完整的代码 - 简洁版本，删除冗余的辅助函数
            wrapped_code = f'''{imports}def main({param_list}) -> dict:
    # 执行用户代码
{eval_block}{chr(10).join('    ' + line for line in original_code.split(chr(10)) if line.strip())}
    
    # 返回所有输出变量
    return {return_dict}'''
        else:
            # 如果用户已经提供了完整的函数，直接使用
            wrapped_code = original_code

        func_node = {
            "id": node_id,
            "type": "code",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "title": config.get("title", f"Code Node {node_index}"),
                "desc": config.get("description", ""),
                "code": wrapped_code,
                "code_language": config.get("code_language", "python3"),
                "outputs": formatted_outputs,
                "args": formatted_args
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": config.get("title", f"Code Node {node_index}"),
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        return func_node, block_node, node_id

    def _create_condition(config, node_index):
        node_id = gen_id()
        source_handle = gen_id()
        block_id = gen_id()
        position = position_manager.get_position("default", node_index)

        # 处理条件配置
        if_else_conditions = config.get("if_else_conditions", [])
        formatted_conditions = []

        # 生成内部用于连边的 handle id，并记录从原始 condition_id 到 handle_id 的映射
        original_id_to_handle = {}
        for condition in if_else_conditions:
            original_id = condition.get("condition_id") or gen_id()
            # 转换子条件中的变量占位为富文本 span
            cond_list = condition.get("conditions", [])
            cond_list = _transform_vars(cond_list)
            handle_id = gen_id()
            original_id_to_handle[original_id] = handle_id

            condition_config = {
                "condition_name": condition.get("condition_name", "默认条件"),
                "logical_operator": condition.get("logical_operator", "and"),
                "conditions": cond_list,
                "condition_action": condition.get("condition_action", []),
                # 使用内部 handle_id 作为真正的输出句柄
                "condition_id": handle_id
            }
            formatted_conditions.append(condition_config)

        # 如果没有提供条件，添加默认的Other条件
        if not any(cond.get("logical_operator") == "other" for cond in formatted_conditions):
            formatted_conditions.append({
                "condition_id": gen_id(),
                "condition_name": "Other",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": []
            })

        func_node = {
            "id": node_id,
            "type": "condition",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "if_else_conditions": formatted_conditions,
                "title": config.get("title", f"Condition {node_index}")
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": config.get("title", f"Condition {node_index}"),
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        # 将映射挂到 block_node，供外部注册
        block_node["original_id_to_handle"] = original_id_to_handle

        return func_node, block_node, node_id

    def _create_text_reply(config, node_index):
        node_id = gen_id()
        source_handle = gen_id()
        block_id = gen_id()
        position = position_manager.get_position("default", node_index)

        # 优先使用 payload 字段（完整的响应对象）
        payload = config.get("payload")
        formatted_plain_text = []
        
        if payload:
            # 将 payload 中的 $var / $a.b.c 转为富文本 span
            payload = _transform_vars(payload)
            # 如果有 payload，使用完整的 payload 对象
            # payload 包含 {"text": "...", "type": "message"} 等完整信息
            formatted_plain_text.append({
                "text": str(payload),
                "id": gen_id(),
                # "payload": payload  # 保存完整的 payload
            })
        else:
            # 兼容旧格式：使用 plain_text 字段
            plain_text = config.get("plain_text", [])
            
            if plain_text:
                for text_item in plain_text:
                    if isinstance(text_item, str):
                        formatted_plain_text.append({
                            "text": text_item.get("text", ""),
                            "id": gen_id()
                        })
                    elif isinstance(text_item, dict):
                        formatted_plain_text.append({
                            "text": text_item.get("text", ""),
                            "id": text_item.get("id", gen_id())
                        })
            else:
                # 默认文本内容
                formatted_plain_text.append({
                    "text": config.get("text", "默认回复内容"),
                    "id": gen_id()
                })

        func_node = {
            "id": node_id,
            "type": "textReply",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "async_run": config.get("async_run", False),
                "plain_text": formatted_plain_text,
                "rich_text": config.get("rich_text", []),
                "title": config.get("title", f"Text Reply {node_index}")
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": config.get("title", f"Text Reply {node_index}"),
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        return func_node, block_node, node_id

    def _create_capture_user_reply(config, node_index, language):
        node_id = gen_id()
        source_handle = gen_id()
        block_id = gen_id()
        position = position_manager.get_position("default", node_index)

        # 处理变量分配
        variable_assign = config.get("variable_assign", f"user_response_{node_index}")
        variable_manager.register_node_output(node_id, variable_assign, lang=language)

        func_node = {
            "id": node_id,
            "type": "captureUserReply",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "variable_assign": variable_assign,
                "enable_global_intention": global_configs.get("enable_global_intent", False),
                "title": config.get("title", f"Capture User Reply {node_index}")
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": config.get("title", f"Capture User Reply {node_index}"),
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        return func_node, block_node, node_id

    def _create_jump_agent(config, node_index):
        node_id = gen_id()
        source_handle = gen_id()
        block_id = gen_id()
        position = position_manager.get_position("default", node_index)

        # 从 config 中读取配置，如果没有则使用默认值
        jump_type = config.get("jump_type", "flow")
        jump_robot_id = config.get("jump_robot_id", "")
        jump_robot_name = config.get("jump_robot_name", "")
        jump_carry_history_number = config.get("jump_carry_history_number", 5)
        jump_flow_name = config.get("jump_flow_name", "")
        jump_flow_uuid = config.get("jump_flow_uuid", "")  # 这是 targetFlowId
        jump_carry_userinput = config.get("jump_carry_userinput", True)
        
        # 生成 title: 优先使用 config 中的 title，否则使用 jump_flow_name，最后使用 jump_flow_uuid
        title = config.get("title", "")
        if not title:
            if jump_flow_name:
                title = f"jump_to_{jump_flow_name}"
            elif jump_flow_uuid:
                title = f"jump_to_{jump_flow_uuid[:8]}"
            else:
                title = f"jump_to_{node_index}"

        func_node = {
            "id": node_id,
            "type": "jump",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "jump_type": jump_type,
                "jump_robot_id": jump_robot_id,
                "jump_robot_name": jump_robot_name,
                "jump_carry_histories": False,
                "jump_carry_history_number": jump_carry_history_number,
                "jump_flow_name": jump_flow_name,
                "jump_flow_uuid": jump_flow_uuid,  # 使用 targetFlowId
                "jump_carry_userinput": jump_carry_userinput,
                "title": title
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": title,
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        return func_node, block_node, node_id

    def _create_semantic_judgment(config, node_index, language):
        """
        创建语义判断节点 (semanticJudgment)
        用于替代 kb → code → condition 的意图识别流程
        """
        node_id = config.get("id") or gen_id()
        source_handle = gen_id()
        block_id = config.get("blockId") or gen_id()
        position = position_manager.get_position("default", node_index)

        # 获取语义条件配置
        semantic_conditions = config.get("config", {}).get("semantic_conditions", [])
        default_condition = config.get("config", {}).get("default_condition", {})
        global_config = config.get("config", {}).get("global_config", {})
        
        # 处理 semantic_conditions，生成内部 handle id 映射
        original_id_to_handle = {}
        formatted_conditions = []
        
        for condition in semantic_conditions:
            original_id = condition.get("condition_id") or gen_id()
            handle_id = gen_id()
            original_id_to_handle[original_id] = handle_id
            
            # 构建格式化的 condition，使用新的 handle_id
            formatted_condition = {
                "condition_id": handle_id,
                "name": condition.get("name", ""),
                "desc": condition.get("desc", ""),
                "refer_questions": condition.get("refer_questions", [{"id": gen_id(), "question": ""}]),
                "positive_examples": condition.get("positive_examples", []),
                "negative_examples": condition.get("negative_examples", [{"id": gen_id(), "question": ""}]),
                "condition_config": condition.get("condition_config", {
                    "keyword_enable": False,
                    "keywords": [],
                    "keyword_type": 1,
                    "regular_enable": False,
                    "regular_str": "",
                    "sft_model_enable": False,
                    "sft_model_name": "",
                    "sft_model_reponse_structure": {},
                    "sft_model_reponse_structure_value": "",
                    "llm_enable": False,
                    "embedding_enable": True
                })
            }
            formatted_conditions.append(formatted_condition)
        
        # 处理 default_condition
        default_original_id = default_condition.get("condition_id") or gen_id()
        default_handle_id = gen_id()
        original_id_to_handle[default_original_id] = default_handle_id
        
        formatted_default_condition = {
            "condition_id": default_handle_id,
            "name": default_condition.get("name", "Other"),
            "desc": default_condition.get("desc", ""),
            "refer_questions": default_condition.get("refer_questions", []),
            "condition_config": default_condition.get("condition_config", {
                "keyword_enable": False,
                "keywords": [],
                "keyword_type": 0,
                "regular_enable": False,
                "regular_str": "",
                "sft_model_enable": False,
                "sft_model_name": "",
                "sft_model_reponse_structure": {"label": "", "value": ""},
                "llm_enable": True,
                "embedding_enable": False
            })
        }
        
        # 设置 embedding_language
        embedding_language = language if language else "en"
        
        # 构建 global_config，确保包含所有必需字段
        formatted_global_config = {
            "is_chatflow": global_config.get("is_chatflow", True),
            "confidence": global_config.get("confidence", 50),
            "is_start_intent": global_config.get("is_start_intent", 0),
            "embedding_model_name": global_config.get("embedding_model_name", "bge-m3"),
            "embedding_rerank_enable": global_config.get("embedding_rerank_enable", False),
            "embedding_rerank_model_name": global_config.get("embedding_rerank_model_name", ""),
            "embedding_rerank_confidence": global_config.get("embedding_rerank_confidence", 0),
            "embedding_llm_enable": False,
            "allow_update_embedding": global_config.get("allow_update_embedding", True),
            "embedding_confidence": global_config.get("embedding_confidence", 0),
            "embedding_llm_model_name": global_configs.get('llmcodemodel', 'qwen3-30b-a3b'),
            "embedding_llm_prompt": global_config.get("embedding_llm_prompt", ""),
            "embedding_llm_return_count": global_config.get("embedding_llm_return_count", 0),
            "embedding_language": global_config.get("embedding_language", embedding_language)
        }

        func_node = {
            "id": node_id,
            "type": "semanticJudgment",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": source_handle,
                "showToolBar": False
            },
            "blockId": block_id,
            "hidden": True,
            "config": {
                "semantic_conditions": formatted_conditions,
                "default_condition": formatted_default_condition,
                "global_config": formatted_global_config,
                "title": config.get("config", {}).get("title", config.get("title", f"Semantic Judgment {node_index}"))
            }
        }

        block_node = {
            "id": block_id,
            "type": "block",
            "initialized": False,
            "position": position,
            "data": {
                "label": config.get("title", f"Semantic Judgment {node_index}"),
                "include_node_ids": [node_id],
                "showToolBar": False
            }
        }

        # 将映射挂到 block_node，供外部注册（类似 condition 节点）
        block_node["original_id_to_handle"] = original_id_to_handle

        return func_node, block_node, node_id

    # 处理节点生成
    nodes = []
    edges = []
    node_name_map = {}  # 改为使用节点名称映射

    for i, node_config in enumerate(node_configs):
        node_type = node_config.get("type")
        node_name = node_config.get("name", f"node_{i}")  # 支持自定义节点名称

        if node_type == "start":
            start_position = position_manager.get_position("start")
            start_node = {
                "id": "start00000000000000000000",
                "type": "start",
                "initialized": False,
                "position": start_position,
                "data": {
                    "label": "Start",
                    "showToolBar": False
                }
            }
            nodes.append(start_node)
            node_name_map[node_name] = {
                "node": start_node,
                "handle": "start00000000000000000000",
                "target_handle": "start00000000000000000000"
            }

        elif node_type == "llmVariableAssignment":
            func_node, block_node, node_id = _create_llm_variable_assignment(node_config, i, language)
            nodes.extend([func_node, block_node])
            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"]
            }

        elif node_type == "llMReply":
            func_node, block_node, node_id = _create_llm_reply(node_config, i)
            nodes.extend([func_node, block_node])
            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"]
            }

        elif node_type == "knowledgeAssignment":
            func_node, block_node, node_id = _create_knowledge_assignment(node_config, i, language)
            nodes.extend([func_node, block_node])
            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"]
            }

        elif node_type == "code":
            func_node, block_node, node_id = _create_code_node(node_config, i, language)
            nodes.extend([func_node, block_node])
            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"]
            }

        elif node_type == "condition":
            func_node, block_node, node_id = _create_condition(node_config, i)
            nodes.extend([func_node, block_node])

            # 提取原始 condition_id -> 句柄 id 的映射（来自 _create_condition 内部生成）
            condition_mappings = block_node.get("original_id_to_handle", {})

            # 注册到edge_manager
            edge_manager.register_condition_node(node_name, condition_mappings)

            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"],
                "condition_mappings": condition_mappings
            }

        elif node_type == "textReply":
            func_node, block_node, node_id = _create_text_reply(node_config, i)
            nodes.extend([func_node, block_node])
            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"]
            }

        elif node_type == "captureUserReply":
            func_node, block_node, node_id = _create_capture_user_reply(node_config, i, language)
            nodes.extend([func_node, block_node])
            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"]
            }
        elif node_type == "jump":
            func_node, block_node, node_id = _create_jump_agent(node_config, i)
            nodes.extend([func_node, block_node])
            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"]
            }

        elif node_type == "semanticJudgment":
            func_node, block_node, node_id = _create_semantic_judgment(node_config, i, language)
            nodes.extend([func_node, block_node])
            
            # 提取原始 condition_id -> 句柄 id 的映射（类似 condition 节点）
            condition_mappings = block_node.get("original_id_to_handle", {})
            
            # 注册到 edge_manager（用于处理语义判断分支的边连接）
            edge_manager.register_condition_node(node_name, condition_mappings)
            
            # writed by senlin.deng 2026-01-17
            # 检查是否有纯条件路由混合的情况
            has_pure_conditions = node_config.get("_has_pure_conditions", False)
            pure_condition_node = node_config.get("_pure_condition_node", None)
            fallback_direct_target = None
            
            # 从 _internal_branches 中查找 Fallback 分支的 _direct_target
            internal_branches = node_config.get("_internal_branches", [])
            for branch in internal_branches:
                if branch.get("condition_name") == "Fallback" and "_direct_target" in branch:
                    fallback_direct_target = branch.get("_direct_target")
                    break
            
            # 如果有纯条件路由，注册到 edge_manager
            if has_pure_conditions and fallback_direct_target and pure_condition_node:
                edge_manager.register_semantic_judgment_with_pure_conditions(
                    node_name, fallback_direct_target, pure_condition_node
                )
                logger.debug(f"  🔀 注册混合路由: {node_name} -> Fallback直接连接: {fallback_direct_target}")
            
            node_name_map[node_name] = {
                "node": block_node,
                "handle": func_node["data"]["sourceHandle"],
                "target_handle": func_node["id"],
                "condition_mappings": condition_mappings,
                "_has_pure_conditions": has_pure_conditions,
                "_pure_condition_node": pure_condition_node,
                "_fallback_direct_target": fallback_direct_target
            }

    # 修改edge生成逻辑，支持节点名称连接
    if edges_config and "edges" in edges_config:
        # 使用自定义edge配置
        for edge_config in edges_config["edges"]:
            edge = edge_manager.create_edge_from_config(edge_config, node_name_map)
            if edge:
                edges.append(edge)
    else:
        # 使用默认顺序连接（按节点配置顺序）
        node_names = list(node_name_map.keys())
        for i in range(len(node_names) - 1):
            source_name = node_names[i]
            target_name = node_names[i + 1]
            source = node_name_map[source_name]
            target = node_name_map[target_name]
            edge = edge_manager._create_default_edge(source, target)
            edges.append(edge)
    
    # writed by senlin.deng 2026-01-17
    # 处理 condition 节点的 _next_node 字段（例如 Pure Condition Routing 的 Other 分支）
    for node_config in node_configs:
        node_name = node_config.get("name")
        node_type = node_config.get("type")
        
        if node_type == "condition" and node_name in node_name_map:
            condition_node_info = node_name_map[node_name]
            if_else_conditions = node_config.get("if_else_conditions", [])
            
            for condition_branch in if_else_conditions:
                # 检查是否有 _next_node 字段
                next_node_name = condition_branch.get("_next_node")
                if next_node_name and next_node_name in node_name_map:
                    condition_id = condition_branch.get("condition_id")
                    next_node_info = node_name_map[next_node_name]
                    
                    # 生成 condition edge: Condition Node → Next Node
                    edge = edge_manager._create_condition_edge(
                        condition_node_info, next_node_info, condition_id
                    )
                    edges.append(edge)
                    logger.debug(f"  ✅ 生成 Condition 分支边: {node_name} [{condition_branch.get('condition_name')}] → {next_node_name}")
    
    # writed by senlin.deng 2026-01-19
    # 修复：纯路由条件节点的解析、Intent路由与Condition路由混合的条件节点解析
    # 处理 code 节点的 _next_node 字段（例如 Combined Mixed Condition Check → Pure Condition Routing）
    for node_config in node_configs:
        node_name = node_config.get("name")
        node_type = node_config.get("type")
        
        if node_type == "code" and node_name in node_name_map:
            next_node_name = node_config.get("_next_node")
            if next_node_name and next_node_name in node_name_map:
                code_node_info = node_name_map[node_name]
                next_node_info = node_name_map[next_node_name]
                
                # 生成 default edge: Code Node → Next Node
                edge = edge_manager._create_default_edge(code_node_info, next_node_info)
                edges.append(edge)
                logger.debug(f"  ✅ 生成 Code 节点边: {node_name} → {next_node_name}")
    
    # writed by senlin.deng 2026-01-17, updated 2026-01-19
    # 处理 semanticJudgment 节点的混合路由（Intent + 纯条件路由）
    # 为 Fallback 分支生成直接边连接：
    # - 如果有混合条件代码节点：SemanticJudgment → Combined Mixed Condition Check（代码节点到 Pure Condition 的边在上面处理）
    # - 如果没有混合条件：SemanticJudgment → Pure Condition Node
    for semantic_node_name, mixed_routing_info in edge_manager.semantic_judgment_with_pure_conditions.items():
        fallback_direct_target = mixed_routing_info["fallback_direct_target"]
        pure_condition_node_name = mixed_routing_info["pure_condition_node"]
        
        if semantic_node_name not in node_name_map:
            logger.warning(f"  ⚠️ SemanticJudgment 节点 {semantic_node_name} 不在 node_name_map 中")
            continue
        
        # 使用 fallback_direct_target 作为目标（可能是代码节点，也可能是 pure_condition_node）
        if fallback_direct_target not in node_name_map:
            logger.warning(f"  ⚠️ Fallback 目标节点 {fallback_direct_target} 不在 node_name_map 中")
            continue
        
        semantic_node_info = node_name_map[semantic_node_name]
        fallback_target_info = node_name_map[fallback_direct_target]
        
        # 找到 Fallback 的 condition_id（从 semantic_node 的配置中获取）
        fallback_condition_id = None
        for node_config in node_configs:
            if node_config.get("name") == semantic_node_name:
                internal_branches = node_config.get("_internal_branches", [])
                for branch in internal_branches:
                    if branch.get("condition_name") == "Fallback":
                        fallback_condition_id = branch.get("condition_id")
                        break
                break
        
        if fallback_condition_id:
            # 使用 condition edge 直接连接 SemanticJudgment → Fallback 目标（代码节点或 Pure Condition Node）
            fallback_edge = edge_manager._create_condition_edge(
                semantic_node_info, fallback_target_info, fallback_condition_id
            )
            edges.append(fallback_edge)
            if fallback_direct_target != pure_condition_node_name:
                logger.debug(f"  ✅ 生成 Fallback 边: {semantic_node_name} → {fallback_direct_target} (通过混合条件代码节点)")
            else:
                logger.debug(f"  ✅ 生成 Fallback 边: {semantic_node_name} → {fallback_direct_target} (直接连接)")
        else:
            logger.warning(f"  ⚠️ 未找到 Fallback condition_id for {semantic_node_name}")

    # 填充变量到workflow
    workflow_template["variables"] = variable_manager.get_all_variables()

    # 组装最终workflow
    workflow_template["nodes"] = nodes
    workflow_template["edges"] = edges

    # 更新现有英文fallback消息为本地化版本
    _update_existing_fallback_messages(workflow_template, language)

    # 保存到文件
    output_json = workflow_template
    
    logger.debug(f'Step 6: 生成 workflow {workflow_name} - {len(nodes)} nodes, {len(edges)} edges')
    
    # 保存到文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_json, f, indent=2, ensure_ascii=False)
    
    return {
        "result": output_json,
        "output_file": output_file
    }


def generate_multiple_workflows(workflow_list_file: str = 'generated_workflows.json'):
    """
    为多个 workflow 生成最终的 JSON 文件
    
    Args:
        workflow_list_file: 包含 workflow 名称列表的 JSON 文件
    """
    import os
    
    logger.info('='*60)
    logger.info('🔄 Multi-Workflow Generator Tool')
    logger.info('='*60)
    
    # 1. 读取 workflow 列表
    if not os.path.exists(workflow_list_file):
        logger.warning(f'⚠️  Warning: {workflow_list_file} not found')
        logger.debug('   Falling back to single workflow mode...')
        main(
            workflow_config='workflow_config.json',
            nodes_config='nodes_config.json',
            variables_config='variables.json',
            edge_config='edge_config.json',
            output_file='generated_workflow.json'
        )
        return
    
    with open(workflow_list_file, 'r', encoding='utf-8') as f:
        workflow_data = json.load(f)
    
    workflows = workflow_data.get('workflows', [])
    
    if not workflows:
        logger.warning(f'⚠️  No workflows found in {workflow_list_file}')
        return
    
    logger.debug(f'\n📊 Found {len(workflows)} workflows to generate')
    logger.info('='*60)
    
    # 2. 为每个 workflow 生成最终 JSON
    success_count = 0
    failed_count = 0
    
    for idx, workflow_name in enumerate(workflows, 1):
        logger.info(f'\n[{idx}/{len(workflows)}] Generating workflow: {workflow_name}')
        logger.info('-'*60)
        
        # 从 workflow_list_file 路径中提取语言
        # 例如: output/step6_final/zh-hant/generated_workflows.json -> zh-hant
        language = 'en'  # 默认值
        workflow_list_dir = os.path.dirname(workflow_list_file)
        if 'step6_final' in workflow_list_dir:
            parts = workflow_list_dir.split('step6_final')
            if len(parts) > 1:
                lang_part = parts[1].lstrip(os.sep).split(os.sep)[0]
                if lang_part in ['en', 'zh', 'zh-hant']:
                    language = lang_part
                elif lang_part == 'zh-cn':
                    language = 'zh'  # zh-cn目录当作zh处理

        # 文件路径
        nodes_file = f'output/step2_workflow_config/{language}/nodes_config_{workflow_name}.json'
        variables_file = f'output/step4_variables/{language}/variables_{workflow_name}.json'
        edge_file = f'output/step2_workflow_config/{language}/edge_config_{workflow_name}.json'
        output_file = f'output/step6_final/{language}/generated_workflow_{workflow_name}.json'
        
        # 检查必需文件是否存在
        missing_files = []
        for file in [nodes_file, variables_file, edge_file]:
            if not os.path.exists(file):
                missing_files.append(file)
        
        if missing_files:
            logger.error(f'   ❌ Missing files for {workflow_name}: {", ".join(missing_files)}')
            failed_count += 1
            continue
        
        try:
            # 使用 workflow_config.json 作为模板（如果存在）
            workflow_config_file = 'workflow_config.json'
            if not os.path.exists(workflow_config_file):
                logger.debug(f'   ⚠️  Warning: {workflow_config_file} not found for {workflow_name}')
                logger.debug('   Creating default workflow config...')
                # 创建默认配置
                default_workflow_config = {
                    "workflow_name": workflow_name,
                    "workflow_info": {
                        "name": workflow_name.replace('_', ' ').title(),
                        "description": f"Auto-generated workflow for {workflow_name}",
                        "version": "1.0.0"
                    }
                }
                temp_workflow_config_file = f'workflow_config_{workflow_name}.json'
                with open(temp_workflow_config_file, 'w', encoding='utf-8') as f:
                    json.dump(default_workflow_config, f, ensure_ascii=False, indent=2)
                workflow_config_file = temp_workflow_config_file
            
            # 生成 workflow
            workflow = main(
                workflow_config=workflow_config_file,
                nodes_config=nodes_file,
                variables_config=variables_file,
                edge_config=edge_file,
                output_file=output_file,
                language=language
            )
            
            logger.debug(f'   ✅ Generated: {output_file}')
            success_count += 1
            
        except Exception as e:
            logger.error(f'   ❌ Error generating {workflow_name}: {e}', exc_info=True)
            failed_count += 1
    
    # 3. 总结
    logger.info('\n' + '='*60)
    logger.info('✅ Multi-Workflow Generation Completed!')
    logger.info('='*60)
    logger.info(f'Total workflows: {len(workflows)}')
    logger.info(f'Successfully generated: {success_count}')
    logger.info(f'Failed: {failed_count}')
    logger.info('='*60)


if __name__ == "__main__":
    # 多 workflow 模式
    generate_multiple_workflows('output/step2_workflow_config/generated_workflows.json')

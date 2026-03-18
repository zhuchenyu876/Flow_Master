"""
工作流转换工具 (Workflow Converter) - Multi-Workflow版本
=======================================================
功能：
1. 处理 Flow 层级：为每个 triggerIntent 创建独立的 workflow
   - 解析 flow.flow.transitionEvents 中的所有意图
   - 为每个意图生成独立的工作流（不包含 flow 层级的意图识别节点）
   - 工作流结构：start → pages 层级的节点
   - 注意：flow 层级的意图识别节点（capture → kb → code → condition）将在外层配置
   - 输出格式：nodes_config_{intent_name}.json, edge_config_{intent_name}.json

2. 处理 Page 层级：处理 fulfillments.json 中各个 pages 的配置
   - 支持四种transitionEvent情况：
     a. 有intent无condition：capture → kb → code(extract intent) → condition → target_pages
     b. 有intent有condition（链式判断）：
        capture → kb → code(extract intent)
          ↓
        intent_check1(是否intent1?)
        ├─ 是 → LLM(提取condition变量) → CODE(解析) → param_condition(判断参数值) → target_pages
        └─ 否 → intent_check2(是否intent2?) → ...
        
        说明：condition中的变量才是需要LLM提取的！每个intent独立判断，意图匹配才进入参数提取
     c. 纯条件分支（无intent）：condition → target_pages
     d. intent单独抽取+多个condition分支（新增）：
        第1个route是intent触发（无target），后续routes是condition判断（判断session参数）
        capture → kb → code(extract intent) → check_intent(判断是否匹配intent)
          ├─ 是 → llm(提取参数) → code(解析) → param_condition(判断参数值) → target_pages
          └─ 否 → fallback
        
        说明：intent和condition判断不在同一个分支里，intent被单独抽出来作为触发器，
              后续的condition routes基于session参数（如ccla-cccl-01）进行判断
              注意：不需要第二个capture，直接用第一个capture的结果传给LLM

2025-12：新增说明（不改原有含义，仅补充现行主流程要点）：
- 主流程 convert_to_multiple_workflows：每个 triggerIntent 生成独立 workflow（page-level）。
- 如果该 intent 在 intent_parameters_map 中定义了 parameters（需要抽槽），在 workflow 开头插入：
    start -> capture_user_response -> llm_extract_params -> parse_params
  然后再接 page 流程；兼容 beforeTransition 的 setParameterActions。
- flow 层级的 capture → kb → code → condition 不再内嵌在单个 workflow，入口路由需在外层配置。
"""

import json
import os
import uuid
from typing import Dict, List, Any, Tuple, Callable
from collections import defaultdict

from logger_config import get_logger, is_verbose
from step2.flow_utils import (
    extract_flow_slots,
    extract_flow_conditions,
    extract_flow_conditions_for_event,
)
from step2.intent_chain_builder import build_flow_slot_chain, build_flow_condition_chain
from step2.page_processor import (
    parse_responses,
    parse_parameter_actions,
    collect_related_pages,
    normalize_condition_value,
    generate_setparameter_code_node,
    parse_transition_events,
    parse_dialogflow_value,
    generate_mixed_condition_code_node,
    generate_combined_mixed_condition_code_node
)
from step2.page_slot_extractor import build_page_slot_chain
from step2.post_processor import filter_invalid_edges, remove_empty_condition_nodes
from step2.ner_node_generator import (
    create_ner_generator,
    SemanticNERNodeGenerator,
    LLMNERNodeGenerator,
    generate_ner_edges
)
logger = get_logger(__name__)

# 控制详细输出的开关
# 可通过环境变量 VERBOSE_MODE=true 开启
VERBOSE = is_verbose()

def vprint(*args, **kwargs):
    """只在 VERBOSE 模式下才输出到日志的调试信息"""
    if VERBOSE:
        try:
            message = " ".join(str(a) for a in args)
        except Exception:
            message = " ".join(map(str, args))
        logger.debug(message)

# 默认情况下，所有裸露的 print 也视为调试日志，而不是直接打印到控制台
# 这样可以避免 step2 在正常模式下输出大量行；如果需要详细信息，可以设置
#   VERBOSE_MODE=true 或 LOG_LEVEL=DEBUG
if not VERBOSE:
    def print(*args, **kwargs):  # type: ignore[override]
        try:
            message = " ".join(str(a) for a in args)
        except Exception:
            message = " ".join(map(str, args))
        logger.debug(message)


class WorkflowConverter:
    """工作流转换器类 V4 - 支持三种transitionEvent情况"""
    
    def __init__(self, intents_mapping: Dict[str, str] = None, intent_parameters_file: str = 'intent_parameters.json', 
        language: str = 'en', intent_recognition_version: int = 1, intents_training_phrases: Dict[str, List[str]] = None,
        global_config: Dict[str, Any] = None):
        """
        初始化转换器

        Args:
            intents_mapping: 意图ID到意图名称的映射字典（可选）
            intent_parameters_file: intent parameters映射文件路径
            language: 语言代码 ('en', 'zh', 'zh-hant')
            intent_recognition_version: 意图识别版本 (1=kb+code+condition, 2=semanticJudgment)
            intents_training_phrases: 意图名称到训练短语列表的映射（用于版本2的语义判断节点）
        """
        self.intents_mapping = intents_mapping or {}
        self.intent_recognition_version = intent_recognition_version
        self.intents_training_phrases = intents_training_phrases or {}
        self.node_counter = 0
        self.variable_counter = 0
        # 实体候选值映射与语言（用于 LLM 提示增强）
        self.entity_candidates = {}
        # 实体类型映射：存储 entity 的 kind 类型（KIND_MAP, KIND_REGEXP 等）
        self.entity_kinds = {}
        # step1 处理后的实体数据（包含 synonyms）
        self.entities_with_synonyms = {}  # displayName -> {lang -> [{value, synonyms}]}
        self.lang = language
        self.global_config = global_config or {}
        
        # 加载intent parameters映射
        self.intent_parameters_map = {}  # intent_id -> parameters列表
        logger.debug(f'WorkflowConverter: 开始加载 intent parameters 文件: {intent_parameters_file}')
        try:
            # 检查文件是否存在
            if not os.path.exists(intent_parameters_file):
                logger.warning(f'WorkflowConverter: {intent_parameters_file} not found, all intents treated as having no parameters')
            else:
                with open(intent_parameters_file, 'r', encoding='utf-8') as f:
                    params_data = json.load(f)
                    # 检查数据格式
                    if not isinstance(params_data, dict):
                        logger.warning(f'WorkflowConverter: {intent_parameters_file} 格式错误，期望 dict，实际为 {type(params_data).__name__}')
                    else:
                        intents_with_params = params_data.get('intentsWithParameters', [])
                        if not isinstance(intents_with_params, list):
                            logger.warning(f'WorkflowConverter: intentsWithParameters 格式错误，期望 list，实际为 {type(intents_with_params).__name__}')
                        else:
                            for intent in intents_with_params:
                                if isinstance(intent, dict):
                                    intent_id = intent.get('id')
                                    if intent_id:
                                        self.intent_parameters_map[intent_id] = intent.get('parameters', [])
                            logger.debug(f'WorkflowConverter: ✅ 加载完成 {len(self.intent_parameters_map)} intents with parameters')
        except FileNotFoundError:
            logger.warning(f'WorkflowConverter: {intent_parameters_file} not found, all intents treated as having no parameters')
        except json.JSONDecodeError as e:
            logger.error(f'WorkflowConverter: ❌ JSON 解析失败 {intent_parameters_file}: {str(e)}')
            logger.error(f'   错误位置: line {e.lineno}, column {e.colno}')
        except Exception as e:
            logger.error(f'WorkflowConverter: ❌ Failed to load {intent_parameters_file}: {str(e)}')
            import traceback
            logger.error(f'   错误详情: {traceback.format_exc()}')
        
        # 初始化 NER 节点生成器（根据 ner_version 配置）
        self.ner_version = self.global_config.get('ner_version', 'llm')
        self.ner_generator = None  # 延迟初始化，等 entities_with_synonyms 加载后再创建
        logger.debug(f'WorkflowConverter: NER版本配置: {self.ner_version}')

    def _init_ner_generator(self):
        """延迟初始化 NER 节点生成器（需要在 entities_with_synonyms 加载后调用）"""
        if self.ner_generator is None:
            self.ner_generator = create_ner_generator(
                ner_version=self.ner_version,
                global_config=self.global_config,
                entities_with_synonyms=self.entities_with_synonyms,
                entity_candidates=self.entity_candidates
            )
            logger.debug(f'WorkflowConverter: ✅ NER生成器已初始化: {type(self.ner_generator).__name__}')
        return self.ner_generator

    

    def get_fallback_message(self) -> str:
        """
        根据语言返回相应的兜底回复消息

        Returns:
            格式化的fallback消息字符串
        """
        fallback_messages = {
            'en': "I didn't get that. Can you repeat?",
            'zh': "抱歉，我没理解您的意思，您可以再详细说明一下吗？",
            'zh-hant': "唔好意思，我冇理解您嘅意思，您可以再講詳細啲嗎？"
        }

        message = fallback_messages.get(self.lang, fallback_messages['en'])
        # 字符串转化成json字符串，确保中文不乱码
        # write by senlin.deng 2025-12-30
        res = {'text': str(message), 'type': 'message'}
        return json.dumps(res, ensure_ascii=False)

    @staticmethod
    def _normalize_condition_value(value: Any) -> Any:
        """
        标准化条件值，保留 'null' 关键字，避免被误认为空字符串
        """
        if value is None:
            return ""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.lower() == 'null':
                return 'null'
            return stripped
        return value

    @staticmethod
    def _is_always_true_condition(condition: Dict[str, Any]) -> bool:
        """
        Only treat as always-true when condition is empty (no comparator/lhs/rhs).
        """
        if not condition:
            return True
        restriction = condition.get('restriction', condition)
        if not isinstance(restriction, dict):
            return False
        comparator = restriction.get('comparator') or condition.get('comparator')
        lhs = restriction.get('lhs') or condition.get('lhs')
        rhs = restriction.get('rhs') or condition.get('rhs')
        condition_value = rhs.get('value') if isinstance(rhs, dict) else None
        return (not comparator and not lhs and not rhs)

    def _can_add_edge(self, source: str, target: str, all_nodes: list = None, jump_node_names: set = None) -> bool:
        """
        检查是否可以添加边
        
        Args:
            source: 源节点名称
            target: 目标节点名称
            all_nodes: 所有节点列表（可选，用于检查节点类型）
            jump_node_names: jump 节点名称集合（可选，如果提供则直接使用）
            
        Returns:
            True 如果可以添加，False 如果不可以
        """
        # 如果 source 和 target 相同，不能添加（自环）
        if source == target:
            return False
        
        # 如果没有提供 jump_node_names，从 all_nodes 中收集
        if jump_node_names is None and all_nodes:
            jump_node_names = {node.get('name') for node in all_nodes if node.get('type') == 'jump'}
        elif jump_node_names is None:
            jump_node_names = set()
        
        # 如果 source 是 jump 节点，不能添加（jump 节点不能有出边）
        if source in jump_node_names:
            return False
        
        # 如果 source 和 target 都是 jump 节点，不能添加（jump 到 jump）
        if source in jump_node_names and target in jump_node_names:
            return False
        
        return True

    def _safe_append_edge(self, edges: list, source: str, target: str, connection_type: str = "default", condition_id: str = None, all_nodes: list = None, jump_node_names: set = None, verbose: bool = True) -> bool:
        """
        安全地添加边，在添加之前检查是否允许
        
        Args:
            edges: 边列表
            source: 源节点名称
            target: 目标节点名称
            connection_type: 连接类型（default 或 condition）
            condition_id: 条件ID（可选）
            all_nodes: 所有节点列表（可选，用于检查节点类型）
            jump_node_names: jump 节点名称集合（可选，如果提供则直接使用）
            verbose: 是否打印警告信息
            
        Returns:
            True 如果成功添加，False 如果不允许添加
        """
        # 检查是否可以添加边
        if not self._can_add_edge(source, target, all_nodes, jump_node_names):
            if verbose:
                print(f'  ⚠️  Warning: Skipping invalid edge (jump to jump or self-loop): {source} -> {target}')
            return False
        
        # 创建边对象
        edge = {
            "source_node": source,
            "target_node": target,
            "connection_type": connection_type
        }
        if condition_id:
            edge["condition_id"] = condition_id
        
        edges.append(edge)
        return True

    def _filter_invalid_edges(self, all_edges: list, all_nodes: list) -> list:
        """
        后处理：过滤掉所有无效的边（jump 到 jump、jump 节点作为 source、自环）
        
        Args:
            all_edges: 所有边的列表
            all_nodes: 所有节点的列表
            
        Returns:
            过滤后的边列表
        """
        # 收集所有 jump 节点名称
        jump_node_names = {node.get('name') for node in all_nodes if node.get('type') == 'jump'}
        
        filtered_edges = []
        removed_count = 0
        jump_to_jump_count = 0
        jump_as_source_count = 0
        self_loop_count = 0
        
        for edge in all_edges:
            source = edge.get('source_node', '')
            target = edge.get('target_node', '')
            
            # 过滤掉自环
            if source == target:
                removed_count += 1
                self_loop_count += 1
                continue
            
            # 过滤掉 jump 节点作为 source 的边（jump 节点不能有出边）
            # 这包括所有 jump 到 jump 的边
            if source in jump_node_names:
                removed_count += 1
                jump_as_source_count += 1
                # 如果 target 也是 jump 节点，记录为 jump-to-jump
                if target in jump_node_names:
                    jump_to_jump_count += 1
                continue
            
            # 如果 source 不是 jump 节点，但 target 是 jump 节点，这是允许的（jump 节点可以作为 target）
            # 所以不需要过滤
            
            filtered_edges.append(edge)
        
        if removed_count > 0:
            print(f'   ⚠️  Removed {removed_count} invalid edges:')
            if jump_to_jump_count > 0:
                print(f'      - {jump_to_jump_count} jump-to-jump edges')
            if jump_as_source_count > 0:
                print(f'      - {jump_as_source_count} edges with jump node as source')
            if self_loop_count > 0:
                print(f'      - {self_loop_count} self-loops')
        
        return filtered_edges

    def _load_entity_candidates(self, flow_file: str):
        """
        用于提示词中的候选标准值
        从 flow 导出文件收集实体各语言候选值，形成映射："@DisplayName" -> { lang -> [values] }
        """
        
        try:
            with open(flow_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.entity_candidates = {}

            def collect(obj):
                if isinstance(obj, dict):
                    if obj.get('kind') == 'KIND_MAP' and obj.get('entries') is not None and obj.get('displayName'):
                        display = obj.get('displayName')
                        key = f"@{display}"
                        for ent in obj.get('entries', []):
                            # writed by senlin.deng 2026-01-15
                            # 修复：实体候选值中文简体、繁体在提示词中显示不全的问题。
                            # 将 zh-hk 映射到 zh-hant，zh-cn 映射到 zh
                            lang = ent.get('lang') or ''
                            if lang == 'zh-hk':
                                lang = 'zh-hant'
                            elif lang == 'zh-cn':
                                lang = 'zh'
                            val = ent.get('value')
                            if not val:
                                continue
                            # writed by senlin.deng 2026-01-13
                            # 去除value中的多余空格，使得单词间变成单空格
                            if isinstance(val, str):
                                val = ' '.join(val.split())
                            self.entity_candidates.setdefault(key, {}).setdefault(lang, []).append(val)
                    for v in obj.values():
                        collect(v)
                elif isinstance(obj, list):
                    for it in obj:
                        collect(it)

            collect(data)
            print(f"Loaded entity candidates for {len(self.entity_candidates)} entities")
        except Exception as e:
            print(f"Warning: failed to load entity candidates from {flow_file}: {e}")
            self.entity_candidates = {}
    
    def _load_entities_with_synonyms(self, entities_file: str):
        """
        从 step1 处理后的 entities 文件加载实体数据（包含 synonyms）
        
        Args:
            entities_file: step1 输出的 entities 文件路径（如 entities_zh.json）
        
        结构示例:
        {
            "entities": [
                {
                    "displayName": "Common_Card",
                    "entries": [
                        {"value": "ATMcard", "synonyms": ["atm card", "ATM卡", ...], "lang": "zh"},
                        ...
                    ]
                },
                ...
            ]
        }
        
        生成的映射结构: displayName -> {lang -> [{value, synonyms}]}
        """
        try:
            with open(entities_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            entities_list = data.get('entities', [])
            for entity in entities_list:
                display_name = entity.get('displayName', '')
                if not display_name:
                    continue
                
                entries = entity.get('entries', [])
                for entry in entries:
                    value = entry.get('value', '')
                    synonyms = entry.get('synonyms', [])
                    lang = entry.get('lang', 'en')
                    
                    if display_name not in self.entities_with_synonyms:
                        self.entities_with_synonyms[display_name] = {}
                    if lang not in self.entities_with_synonyms[display_name]:
                        self.entities_with_synonyms[display_name][lang] = []
                    
                    self.entities_with_synonyms[display_name][lang].append({
                        'value': value,
                        'synonyms': synonyms
                    })
            
            print(f"Loaded entities with synonyms for {len(self.entities_with_synonyms)} entities")
        except FileNotFoundError:
            print(f"Warning: entities file not found: {entities_file}")
            self.entities_with_synonyms = {}
        except Exception as e:
            print(f"Warning: failed to load entities with synonyms from {entities_file}: {e}")
            self.entities_with_synonyms = {}
    
    def _generate_unique_node_name(self, base_name: str, page_id: str = "") -> str:
        """生成唯一的节点名称"""
        if page_id:
            unique_suffix = f"{page_id[:8]}_{self.node_counter}"
        else:
            unique_suffix = f"{self.node_counter}"
        self.node_counter += 1
        return f"{base_name}_{unique_suffix}"
    
    def _generate_variable_name(self) -> str:
        """生成唯一的变量名"""
        self.variable_counter += 1
        return f"variable_{self.variable_counter}"
    
    def _create_jump_to_main_agent_node(self, node_name: str) -> Dict[str, Any]:
        """
        创建 Jump to Main Agent 节点（替代原来的 Fallback Message 节点）
        
        Args:
            node_name: 节点名称
            
        Returns:
            jump_to_main_agent 节点字典
        """
        return {
            "type": "jump",
            "name": node_name,
            "title": "Jump to Main Agent",
            "jump_type": "robot_direct",
            "jump_robot_id": None,
            "jump_robot_name": "",
            "jump_carry_history_number": 5,
            "jump_carry_histories": True,
            "jump_flow_name": "",
            "jump_flow_uuid": "",
            "jump_carry_userinput": True
        }
    
    def _generate_setparameter_code_node(self, set_param_actions: List[Dict[str, Any]], page_id: str = "", intent_name: str = "") -> Tuple[Dict[str, Any], List[str]]:
        """
        为 setParameterActions 生成 code 节点
        
        Args:
            set_param_actions: setParameterActions 列表
            page_id: page ID（用于生成唯一节点名）
            intent_name: intent名称（用于节点标题）
            
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
            value = action.get('value', '')
            
            if isinstance(value, str) and value.startswith('$'):
                # 是变量引用，需要处理
                # 示例：$request.user-utterance → request_user_utterance
                # 示例：$session.params.accountType → session_params_accountType
                var_ref = value[1:]  # 去掉 $
                
                # 将变量引用转换为 Python 变量名（将 . 和 - 替换为 _）
                # 保留完整的路径信息，以便正确引用变量
                input_var_name = var_ref.replace('.', '_').replace('-', '_')
                
                if input_var_name not in input_variables:
                    input_variables.append(input_var_name)
                
                code_lines.append(f"{parameter} = {input_var_name}")
            elif isinstance(value, str):
                # 是字符串字面值
                code_lines.append(f'{parameter} = "{value}"')
            else:
                # 是数字或其他类型
                code_lines.append(f"{parameter} = {value}")
            
            output_variables.append(parameter)
        
        # 生成code节点
        node_name = self._generate_unique_node_name('transition_code', page_id)
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
    
    # 注意：此方法已废弃，请使用 page_processor.py 中的 parse_responses 方法
    # write by senlin.deng 2025-12-29
    def parse_responses(self, page: Dict[str, Any], lang: str = "en") -> List[Dict[str, Any]]:
        """
        解析 page 中的 responses，生成 text 节点
        
        Args:
            page: page配置字典
            lang: 语言代码（en, zh-cn, zh-hant）
            
        Returns:
            text节点列表
        """
        text_nodes = []
        
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
            
            # 生成唯一的节点名
            page_id = page.get('key') or page.get('pageId', '')
            node_name = self._generate_unique_node_name('text_node', page_id)
            
            # 获取 displayName
            display_name = page.get('value', {}).get('displayName') or page.get('displayName', '')
            
            # 保存完整的payload内容（包括所有字段：text, type, buttons, urls等）
            payload = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            text_node = {
                "type": "textReply",
                "name": node_name,
                "title": f"Response_{display_name}",
                "payload": payload  # 完整的payload对象，例如：{"text": "...", "type": "message"}
            }
            
            # 同时保留plain_text格式以兼容workflow_generator
            # 根据payload类型提取text内容用于显示
            response_type = payload.get('type', '')
            # 将 payload 转换为json字符串，确保中文不乱码
            # write by senlin.deng 2025-12-29
            plain_text_items = [
                {
                    "text": payload,
                    "id": node_name
                }
            ]
            
            # 注释掉的代码：根据 response_type 提取不同内容（如果需要可以取消注释）
            # if response_type == 'message' and 'text' in payload:
            #     plain_text_items.append({
            #         "text": payload.get('text', ''),
            #         "id": node_name
            #     })
            # elif response_type == 'buttonList' and 'buttons' in payload:
            #     text_content = payload.get('text', '')
            #     buttons = payload.get('buttons', [])
            #     button_text = f"{text_content}\n\nButtons: " + ", ".join(buttons) if text_content else "Buttons: " + ", ".join(buttons)
            #     plain_text_items.append({"text": button_text, "id": node_name})
            
            text_node["plain_text"] = plain_text_items
            text_nodes.append(text_node)
    
        return text_nodes
    
    def parse_parameter_actions(self, page: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """
        解析 page 中的 setParameterActions，生成 code 节点
        
        Args:
            page: page配置字典
            
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
            value = action.get('value', '')
            
            # 生成Python代码
            # 注意：value可能是字符串、数字或变量引用（如 $session.params.xxx, $request.user-utterance）
            if isinstance(value, str) and value.startswith('$'):
                # 是变量引用，需要处理
                # 示例：$request.user-utterance → request_user_utterance
                # 示例：$session.params.accountType → session_params_accountType
                var_ref = value[1:]  # 去掉 $
                
                # 将变量引用转换为 Python 变量名（将 . 和 - 替换为 _）
                # 保留完整的路径信息，以便正确引用变量
                input_var_name = var_ref.replace('.', '_').replace('-', '_')
                
                # 添加到输入变量列表（去重）
                if input_var_name not in input_variables:
                    input_variables.append(input_var_name)
                
                # 生成赋值代码
                code_lines.append(f"{parameter} = {input_var_name}")
            elif isinstance(value, str):
                # 是字符串字面值
                code_lines.append(f'{parameter} = "{value}"')
            else:
                # 是数字或其他类型
                code_lines.append(f"{parameter} = {value}")
            
            output_variables.append(parameter)
        
        # 生成code节点
        # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
        page_id = page.get('key') or page.get('pageId', '')
        node_name = self._generate_unique_node_name('code_node', page_id)
        
        code_node = {
            "type": "code",
            "name": node_name,
            "title": f"VariableAssignment_{page.get('value', {}).get('displayName', '') or page.get('displayName', '')}",
            "code": "\n".join(code_lines),
            "outputs": output_variables,
            "args": input_variables  # 添加输入变量
        }
        
        return code_node, output_variables
    
    # 注意：parse_transition_events 已经从 step2.page_processor 导入并使用
    # 这个类方法已经不再需要，因为逻辑已经迁移到 page_processor.py
    # 保留此注释以避免混淆
    
    # writed by senlin.deng 2026-01-17
    # 处理版本二中 Intent 路由和纯条件路由混合的情况
    def _generate_semantic_judgment_with_pure_conditions(
        self,
        page: Dict[str, Any],
        transition_info_list: List[Dict[str, Any]],
        gen_unique_node_name: Callable[[str, str], str] = None,
        gen_variable_name: Callable[[], str] = None,
        generate_setparameter_code_node_func: Callable = None,
        intents_mapping: Dict[str, str] = None,
        intent_parameters_map: Dict[str, List[Dict[str, Any]]] = None,
        entity_candidates: Dict[str, Dict[str, List[str]]] = None,
        lang: str = 'en',
        node_counter_ref: List[int] = None,
        has_any_parameter: bool = False
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        执行流程：
        1. 如果用户触发了Intent，走对应的意图分支
        2. 如果用户未触发任何Intent，Semantic Judgement 路由到 Fallback
        3. Fallback 分支连接到纯条件判断节点，检查纯条件路由
        
        Args:
            page: page配置
            transition_info_list: transitionEvent信息列表（包含Intent路由和纯条件路由）
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            generate_setparameter_code_node_func: 生成setParameter代码节点的函数
            intents_mapping: 意图ID到名称的映射
            intent_parameters_map: 意图参数映射
            entity_candidates: 实体候选值映射
            lang: 语言代码
            node_counter_ref: 节点计数器引用
            has_any_parameter: 是否有参数需要提取
            
        Returns:
            (节点列表, 条件分支列表)
        """
        import uuid
        import base64
        
        def short_id():
            """生成短 ID（类似 EdfMIK03J77ZhSLJ6_OKZ 格式）"""
            raw = uuid.uuid4().bytes
            return base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=\n').replace('-', '_').replace('+', '_')[:21]
        
        if gen_unique_node_name is None:
            gen_unique_node_name = self._generate_unique_node_name
        if gen_variable_name is None:
            gen_variable_name = self._generate_variable_name
        if generate_setparameter_code_node_func is None:
            generate_setparameter_code_node_func = self._generate_setparameter_code_node
        if intents_mapping is None:
            intents_mapping = self.intents_mapping
        if intent_parameters_map is None:
            intent_parameters_map = self.intent_parameters_map
        if entity_candidates is None:
            entity_candidates = self.entity_candidates
        if lang is None or lang == '':
            lang = self.lang
        if node_counter_ref is None:
            node_counter_ref = [self.node_counter]
            
        nodes = []
        condition_branches = []
        
        # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
        page_id = page.get('key') or page.get('pageId', '')
        
        # 分离 Intent 路由和纯条件路由
        intent_routes = [t for t in transition_info_list if t['has_intent']]
        pure_condition_routes = [t for t in transition_info_list if not t['has_intent'] and t['has_condition']]
        
        logger.debug(f"  🔀 检测到混合路由: {len(intent_routes)} 个Intent路由, {len(pure_condition_routes)} 个纯条件路由")
        
        # 1. 生成capture节点 - 收集用户输入
        capture_variable = "last_user_response"
        capture_node_name = gen_unique_node_name('capture_input', page_id)
        capture_node = {
            "type": "captureUserReply",
            "name": capture_node_name,
            "title": "Capture User Input",
            "variable_assign": capture_variable
        }
        nodes.append(capture_node)
        
        # 2. 生成语义判断节点（只包含 Intent 路由）
        semantic_node_name = gen_unique_node_name('semantic_judgment', page_id)
        semantic_node_id = str(uuid.uuid4())
        
        # 构建 semantic_conditions（只包含 Intent）
        semantic_conditions = []
        intent_to_condition_id = {}  # intent_name -> condition_id
        intent_to_trans_info_list = {}  # intent_name -> [trans_info1, trans_info2, ...]
        processed_intents = set()
        
        for idx, trans_info in enumerate(intent_routes, 1):
            intent_name = trans_info.get('intent_name', '')
            intent_id = trans_info.get('intent_id', '')
            
            # 收集所有相同意图的 trans_info
            if intent_name not in intent_to_trans_info_list:
                intent_to_trans_info_list[intent_name] = []
            intent_to_trans_info_list[intent_name].append(trans_info)
            
            # 如果这个意图已经处理过，跳过
            if intent_name in processed_intents:
                logger.debug(f"    ⏭️ 意图 '{intent_name}' 已存在，跳过重复添加（将合并条件分支）")
                continue
            
            processed_intents.add(intent_name)
            
            # 从 intents_training_phrases 获取训练短语
            training_phrases = self.intents_training_phrases.get(intent_name, [])
            
            # 如果没有找到，尝试用 intent_id 查找
            if not training_phrases and intent_id:
                training_phrases = self.intents_training_phrases.get(intent_id, [])
            
            # 如果还是没有找到，尝试大小写不敏感匹配
            if not training_phrases:
                intent_name_lower = intent_name.lower()
                for key, phrases in self.intents_training_phrases.items():
                    if key.lower() == intent_name_lower:
                        training_phrases = phrases
                        logger.debug(f"    🔄 通过大小写不敏感匹配找到: '{key}' -> '{intent_name}'")
                        break
            
            # 调试日志
            if training_phrases:
                logger.debug(f"    ✅ 意图 '{intent_name}' 找到 {len(training_phrases)} 个训练短语")
            else:
                logger.warning(f"    ⚠️ 意图 '{intent_name}' (id={intent_id}) 没有找到训练短语！")
            
            # 构建 positive_examples
            positive_examples = []
            for phrase in training_phrases:
                positive_examples.append({
                    "id": short_id(),
                    "question": phrase
                })
            
            # 生成 condition_id
            condition_id = str(uuid.uuid4())
            intent_to_condition_id[intent_name] = condition_id
            
            # 构建单个 semantic_condition
            semantic_condition = {
                "condition_id": condition_id,
                "name": intent_name,
                "desc": "",
                "refer_questions": [
                    {
                        "id": short_id(),
                        "question": ""
                    }
                ],
                "positive_examples": positive_examples,
                "negative_examples": [
                    {
                        "id": short_id(),
                        "question": ""
                    }
                ],
                "condition_config": {
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
                }
            }
            semantic_conditions.append(semantic_condition)
        
        # 构建 default_condition（Fallback）
        default_condition_id = str(uuid.uuid4())
        default_condition = {
            "condition_id": default_condition_id,
            "name": "Fallback_Intent",
            "desc": "",
            "refer_questions": [],
            "condition_config": {
                "keyword_enable": False,
                "keywords": [],
                "keyword_type": 0,
                "regular_enable": False,
                "regular_str": "",
                "sft_model_enable": False,
                "sft_model_name": "",
                "sft_model_reponse_structure": {
                    "label": "",
                    "value": ""
                },
                "llm_enable": True,
                "embedding_enable": False
            }
        }
        
        # 根据语言设置 embedding_language
        embedding_language = lang if lang else "en"
        
        # 构建完整的语义判断节点
        semantic_node = {
            "id": semantic_node_id,
            "type": "semanticJudgment",
            "name": semantic_node_name,
            "title": "Intent Recognition (Semantic)",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": str(uuid.uuid4()),
                "showToolBar": False
            },
            "blockId": str(uuid.uuid4()),
            "hidden": True,
            "config": {
                "semantic_conditions": semantic_conditions,
                "default_condition": default_condition,
                "global_config": {
                    "is_chatflow": True,
                    "confidence": self.global_config.get("semantic_confidence", 50),
                    "is_start_intent": 0,
                    "embedding_model_name": "bge-m3",
                    "embedding_rerank_enable": True,
                    "embedding_rerank_model_name": "bge-reranker-v2-m3",
                    "embedding_rerank_confidence": 0,
                    "embedding_llm_enable": False,
                    "allow_update_embedding": True,
                    "embedding_confidence": 0,
                    "embedding_llm_model_name": self.global_config.get('llmcodemodel', 'qwen3-30b-a3b'),
                    "embedding_llm_prompt": "",
                    "embedding_llm_return_count": 0,
                    "embedding_language": embedding_language
                },
                "title": "Intent Recognition"
            }
        }
        nodes.append(semantic_node)
        
        # 3. 为每个 Intent 生成参数提取链（如果有参数或条件）
        # 检查是否有参数需要提取
        has_any_parameter_for_extraction = False
        for trans_info in intent_routes:
            intent_id = trans_info.get('intent_id', '')
            parameters = intent_parameters_map.get(intent_id, [])
            if parameters or trans_info.get('has_condition'):
                has_any_parameter_for_extraction = True
                break
        
        if has_any_parameter_for_extraction:
            # 复用版本一的参数提取逻辑
            intent_variable = "intent"  # 虚拟变量
            
            nodes_param, branches_param = self._generate_parameter_extraction_nodes(
                page, intent_routes, intent_variable,
                intents_mapping=intents_mapping,
                intent_parameters_map=intent_parameters_map,
                gen_unique_node_name=gen_unique_node_name,
                gen_variable_name=gen_variable_name,
                generate_setparameter_code_node_func=generate_setparameter_code_node_func,
                entity_candidates=entity_candidates,
                lang=lang,
                node_counter_ref=node_counter_ref,
                skip_intent_check=True
            )
            
            # writed by senlin.deng 2026-01-22
            # 修复：为生成的 LLM、CODE、param_condition 节点设置 from_semantic_condition_id 属性
            # 这样边生成代码能正确识别这些节点并生成 param_condition 分支到 target_page 的边
            for node in nodes_param:
                node_type = node.get('type', '')
                title = node.get('title', '')
                # 从 title 中提取 intent_name，然后找到对应的 semantic_condition_id
                if node_type in ('llmVariableAssignment', 'code') and 'for ' in title:
                    intent_name_from_title = title.split('for ')[-1]
                    cond_id = intent_to_condition_id.get(intent_name_from_title)
                    if cond_id:
                        node['from_semantic_condition_id'] = cond_id
                elif node_type == 'condition' and ('Parameter Routing for' in title or 'Parameter Routing (' in title):
                    # param_condition 节点：从 title 中提取 intent_name
                    if 'for ' in title:
                        intent_name_from_title = title.split('for ')[-1]
                        cond_id = intent_to_condition_id.get(intent_name_from_title)
                        if cond_id:
                            node['from_semantic_condition_id'] = cond_id
            
            nodes.extend(nodes_param)
            condition_branches.extend(branches_param)
            
            # 记录 condition_id 到下一个节点的映射
            intent_check_nodes = [n for n in nodes_param if n.get('type') == 'condition' and 'Check if Intent is' in n.get('title', '')]
            condition_id_to_next_node = {}
            
            for intent_name, cond_id in intent_to_condition_id.items():
                for check_node in intent_check_nodes:
                    if intent_name in check_node.get('title', ''):
                        for branch in check_node.get('if_else_conditions', []):
                            branch_id = branch.get('condition_id', '')
                            if branch_id.startswith('is_') and intent_name in branch_id:
                                next_node = branch.get('target_node')
                                if next_node:
                                    condition_id_to_next_node[cond_id] = next_node
                                break
                        break
            
            semantic_node["_condition_id_to_next_node"] = condition_id_to_next_node
            semantic_node["_reuse_v1_parameter_extraction"] = True
        else:
            # 没有参数的情况（直接路由到目标）
            # 为有 setParameterActions 或 beforeTransition 的 transition 生成节点
            transition_code_nodes = {}
            transition_text_nodes = {}
            for trans_info in intent_routes:
                intent_name = trans_info['intent_name']
                condition_id_for_intent = intent_to_condition_id.get(intent_name)
                if not condition_id_for_intent:
                    continue
                
                # 处理 setParameterActions
                set_param_actions = trans_info.get('set_parameter_actions', [])
                if set_param_actions:
                    code_node, _ = generate_setparameter_code_node_func(
                        set_param_actions, page_id, intent_name
                    )
                    if code_node:
                        nodes.append(code_node)
                        transition_code_nodes[condition_id_for_intent] = code_node['name']
                
                # 处理 beforeTransition.staticUserResponse
                before_transition_text_nodes = trans_info.get('before_transition_text_nodes', [])
                if before_transition_text_nodes:
                    text_node_names = []
                    for text_node in before_transition_text_nodes:
                        nodes.append(text_node)
                        text_node_names.append(text_node['name'])
                    transition_text_nodes[condition_id_for_intent] = text_node_names
            
            # 生成Intent路由的条件分支（记录分支信息，但不生成condition节点）
            for trans_info in intent_routes:
                intent_name = trans_info['intent_name']
                condition_id = intent_to_condition_id.get(intent_name)
                if not condition_id:
                    continue
                
                branch = {
                    "condition_id": condition_id,
                    "condition_name": f"Intent_{intent_name}",
                    "logical_operator": "other",
                    "conditions": [],
                    "condition_action": [],
                    "target_page_id": trans_info['target_page_id'],
                    "target_flow_id": trans_info['target_flow_id'],
                    "transition_code_node": transition_code_nodes.get(condition_id),
                    "transition_text_nodes": transition_text_nodes.get(condition_id, []),
                    "is_always_true": trans_info.get('is_always_true', False),
                    "from_semantic_node": semantic_node_name
                }
                condition_branches.append(branch)
            
            semantic_node["_reuse_v1_parameter_extraction"] = False
            semantic_node["_intent_to_condition_id"] = intent_to_condition_id
        
        # 4. 为纯条件路由生成条件判断节点
        # 这些节点连接到 Fallback 分支
        pure_condition_node_name = gen_unique_node_name('pure_condition_routing', page_id)
        pure_condition_branches = []
        
        # 4.1 检测是否有混合 AND+OR 条件
        # writed by senlin.deng 2026-01-19
        mixed_conditions_with_index = []
        for idx, trans_info in enumerate(pure_condition_routes, 1):
            if trans_info.get('is_mixed_and_or') and trans_info.get('mixed_and_or_condition'):
                mixed_conditions_with_index.append((idx, trans_info['mixed_and_or_condition']))
        
        has_mixed_conditions = len(mixed_conditions_with_index) > 0
        combined_mixed_code_node = None
        combined_output_var = None
        index_to_condition_value = {}
        
        # 4.2 如果有混合条件，生成 Combined Mixed Condition Check 代码节点
        if has_mixed_conditions:
            combined_mixed_code_node, combined_output_var, index_to_condition_value = generate_combined_mixed_condition_code_node(
                mixed_conditions_with_index, page_id, gen_unique_node_name
            )
            nodes.append(combined_mixed_code_node)
            logger.debug(f"  🔀 检测到 {len(mixed_conditions_with_index)} 个混合 AND+OR 条件，生成 Combined Mixed Condition Check 节点: {combined_mixed_code_node['name']}")
        
        # 4.3 为每个纯条件路由生成分支
        for idx, trans_info in enumerate(pure_condition_routes, 1):
            condition_id = str(uuid.uuid4())
            
            # 构建条件
            conditions_list = []
            
            # 检查是否是混合 AND+OR 条件
            if trans_info.get('is_mixed_and_or') and idx in [i for i, _ in mixed_conditions_with_index]:
                # 混合条件：使用代码节点的输出变量来判断
                condition_value = index_to_condition_value.get(idx, str(idx))
                conditions_list = [{
                    "condition_type": "variable",
                    "comparison_operator": "=",
                    "condition_value": condition_value,
                    "condition_variable": combined_output_var
                }]
                logical_operator = 'and'
                logger.debug(f"    📝 混合条件分支 {idx}: {combined_output_var} = {condition_value}")
            elif trans_info['and_conditions_list']:
                # AND/OR 条件
                for cond in trans_info['and_conditions_list']:
                    conditions_list.append({
                        "condition_type": "variable",
                        "comparison_operator": cond['operator'],
                        "condition_value": str(cond['value']),
                        "condition_variable": cond['variable']
                    })
                logical_operator = 'or' if trans_info.get('is_or_condition') else 'and'
            elif trans_info['has_condition']:
                # 单一条件
                conditions_list.append({
                    "condition_type": "variable",
                    "comparison_operator": trans_info['condition_operator'],
                    "condition_value": str(trans_info['condition_value']),
                    "condition_variable": trans_info['condition_variable']
                })
                logical_operator = 'and'
            else:
                logical_operator = 'other'
            
            # 处理 setParameterActions 和 beforeTransition
            code_node_name = None
            text_node_names = []
            
            set_param_actions = trans_info.get('set_parameter_actions', [])
            if set_param_actions:
                code_node, _ = generate_setparameter_code_node_func(
                    set_param_actions, page_id, f"condition_{idx}"
                )
                if code_node:
                    nodes.append(code_node)
                    code_node_name = code_node['name']
            
            before_transition_text_nodes = trans_info.get('before_transition_text_nodes', [])
            if before_transition_text_nodes:
                for text_node in before_transition_text_nodes:
                    nodes.append(text_node)
                    text_node_names.append(text_node['name'])
            
            branch = {
                "condition_id": condition_id,
                "condition_name": f"Pure Condition Route {idx}",
                "logical_operator": logical_operator,
                "conditions": conditions_list,
                "condition_action": [],
                "target_page_id": trans_info['target_page_id'],
                "target_flow_id": trans_info['target_flow_id'],
                "transition_code_node": code_node_name,
                "transition_text_nodes": text_node_names,
                "is_always_true": trans_info.get('is_always_true', False)
            }
            pure_condition_branches.append(branch)
        
        # 5. 先生成 Jump to Main Agent 节点（Pure Condition Routing 的 Other 分支会连接到它）
        # write by senlin.deng 2026-01-21: 替换原来的 Fallback Message 节点
        fallback_node_name = gen_unique_node_name('jump_to_main_agent', page_id)
        fallback_node = self._create_jump_to_main_agent_node(fallback_node_name)
        nodes.append(fallback_node)
        
        # 5.1 添加 Other 分支（纯条件都不满足时连接到 Fallback Message）
        pure_fallback_condition_id = str(uuid.uuid4())
        pure_condition_branches.append({
            "condition_id": pure_fallback_condition_id,
            "condition_name": "Other",
            "logical_operator": "other",
            "conditions": [],
            "condition_action": [],
            "_next_node": fallback_node_name  # writed by senlin.deng 2026-01-17: 连接到 Fallback Message
        })
        
        # 5.2 生成纯条件判断节点
        pure_condition_node = {
            "type": "condition",
            "name": pure_condition_node_name,
            "title": "Condition Routing",
            "if_else_conditions": pure_condition_branches
        }
        nodes.append(pure_condition_node)
        condition_branches.extend(pure_condition_branches)
        
        # 6. 添加 Fallback 分支
        # writed by senlin.deng 2026-01-17, updated 2026-01-19
        # 如果有混合 AND+OR 条件，Fallback 先连接到 Combined Mixed Condition Check 代码节点
        # 代码节点再连接到 pure_condition_routing 节点
        if has_mixed_conditions and combined_mixed_code_node:
            # 有混合条件：Fallback -> Combined Mixed Condition Check -> pure_condition_routing
            fallback_branch = {
                "condition_id": default_condition_id,
                "condition_name": "Fallback",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": [],
                "from_semantic_node": semantic_node_name,
                "_direct_target": combined_mixed_code_node['name']  # Fallback 连接到代码节点
            }
            # 代码节点连接到 pure_condition_routing
            combined_mixed_code_node['_next_node'] = pure_condition_node_name
            logger.debug(f"  🔗 Fallback 分支链: Semantic -> {combined_mixed_code_node['name']} -> {pure_condition_node_name}")
        else:
            # 无混合条件：Fallback 直接连接到纯条件判断节点
            fallback_branch = {
                "condition_id": default_condition_id,
                "condition_name": "Fallback",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": [],
                "from_semantic_node": semantic_node_name,
                "_direct_target": pure_condition_node_name  # Fallback 直接连接到纯条件判断节点
            }
        condition_branches.append(fallback_branch)
        
        # 记录内部分支信息（用于边生成）
        semantic_branches_for_edges = []
        for intent_name, cond_id in intent_to_condition_id.items():
            trans_info_list = intent_to_trans_info_list.get(intent_name, [])
            first_trans_info = trans_info_list[0] if trans_info_list else {}
            # 从 condition_branches 中获取 transition_code_node 和 transition_text_nodes
            transition_code_node = None
            transition_text_nodes = []
            for branch in condition_branches:
                if branch.get('condition_id') == cond_id:
                    transition_code_node = branch.get('transition_code_node')
                    transition_text_nodes = branch.get('transition_text_nodes', [])
                    break
            semantic_branches_for_edges.append({
                "condition_id": cond_id,
                "condition_name": f"Intent_{intent_name}",
                "target_page_id": first_trans_info.get('target_page_id'),
                "target_flow_id": first_trans_info.get('target_flow_id'),
                "is_always_true": first_trans_info.get('is_always_true', False),
                "set_parameter_actions": first_trans_info.get('set_parameter_actions', []),
                "before_transition_text_nodes": first_trans_info.get('before_transition_text_nodes', []),
                "transition_code_node": transition_code_node,  # 新增：transition_code_node
                "transition_text_nodes": transition_text_nodes,  # 新增：transition_text_nodes
                "from_semantic_node": semantic_node_name,
                "has_multiple_conditions": len(trans_info_list) > 1
            })
        semantic_branches_for_edges.append(fallback_branch)
        
        semantic_node["_internal_branches"] = semantic_branches_for_edges
        semantic_node["_has_pure_conditions"] = True  # 标记：有纯条件路由
        semantic_node["_pure_condition_node"] = pure_condition_node_name  # 纯条件判断节点名
        semantic_node["_has_mixed_conditions"] = has_mixed_conditions  # 新增：标记是否有混合条件
        if has_mixed_conditions and combined_mixed_code_node:
            semantic_node["_mixed_condition_code_node"] = combined_mixed_code_node['name']  # 新增：混合条件代码节点名
        
        logger.debug(f"  ✅ 生成混合路由: SemanticJudgment({len(semantic_conditions)}个意图) + 纯条件判断({len(pure_condition_routes)}个条件)" + 
                     (f" + 混合条件代码节点" if has_mixed_conditions else ""))
        
        return nodes, condition_branches
    
    def _generate_semantic_judgment_node(
        self,
        page: Dict[str, Any],
        transition_info_list: List[Dict[str, Any]],
        gen_unique_node_name: Callable[[str, str], str] = None,
        gen_variable_name: Callable[[], str] = None,
        generate_setparameter_code_node_func: Callable = None,
        intents_mapping: Dict[str, str] = None,
        intent_parameters_map: Dict[str, List[Dict[str, Any]]] = None,
        entity_candidates: Dict[str, Dict[str, List[str]]] = None,
        lang: str = 'en',
        node_counter_ref: List[int] = None,
        has_any_parameter: bool = False
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        生成语义判断节点（版本2的意图识别方式）
        将 kb → code(extract intent) → condition 转化为 semanticJudgment 节点
        
        当有 parameter 时，在语义判断节点后面为每个意图分支生成参数提取节点（LLM + CODE + condition）
        
        Args:
            page: page配置
            transition_info_list: transitionEvent信息列表
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            generate_setparameter_code_node_func: 生成setParameter代码节点的函数
            intents_mapping: 意图ID到名称的映射
            intent_parameters_map: 意图参数映射
            entity_candidates: 实体候选值映射
            lang: 语言代码
            node_counter_ref: 节点计数器引用
            has_any_parameter: 是否有参数需要提取
            
        Returns:
            (节点列表, 条件分支列表)
        """
        import uuid
        import base64
        
        def short_id():
            """生成短 ID（类似 EdfMIK03J77ZhSLJ6_OKZ 格式）"""
            raw = uuid.uuid4().bytes
            return base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=\n').replace('-', '_').replace('+', '_')[:21]
        
        if gen_unique_node_name is None:
            gen_unique_node_name = self._generate_unique_node_name
        if gen_variable_name is None:
            gen_variable_name = self._generate_variable_name
        if generate_setparameter_code_node_func is None:
            generate_setparameter_code_node_func = self._generate_setparameter_code_node
        if intents_mapping is None:
            intents_mapping = self.intents_mapping
        if intent_parameters_map is None:
            intent_parameters_map = self.intent_parameters_map
        if entity_candidates is None:
            entity_candidates = self.entity_candidates
        if lang is None or lang == '':
            lang = self.lang
        if node_counter_ref is None:
            node_counter_ref = [self.node_counter]
            
        nodes = []
        condition_branches = []
        
        # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
        page_id = page.get('key') or page.get('pageId', '')
        
        # 判断是否有任何intent需要处理
        has_any_intent = any(t['has_intent'] for t in transition_info_list)
        
        if not has_any_intent:
            return nodes, condition_branches
        
        # 1. 生成capture节点 - 收集用户输入
        capture_variable = "last_user_response"
        capture_node_name = gen_unique_node_name('capture_input', page_id)
        capture_node = {
            "type": "captureUserReply",
            "name": capture_node_name,
            "title": "Capture User Input",
            "variable_assign": capture_variable
        }
        nodes.append(capture_node)
        
        # 2. 生成语义判断节点
        semantic_node_name = gen_unique_node_name('semantic_judgment', page_id)
        semantic_node_id = str(uuid.uuid4())
        
        # 构建 semantic_conditions 和记录每个意图的后续节点信息
        # 重要：相同意图的多个 transitionEvent 应该合并为一个语义条件，不同的条件分支在后续处理
        semantic_conditions = []
        intent_to_condition_id = {}  # intent_name -> condition_id
        intent_to_trans_info_list = {}  # intent_name -> [trans_info1, trans_info2, ...]  收集所有相同意图的不同条件
        processed_intents = set()  # 记录已处理的意图，避免重复添加 semantic_condition
        
        for idx, trans_info in enumerate(transition_info_list, 1):
            if not trans_info['has_intent']:
                continue
            
            intent_name = trans_info.get('intent_name', '')
            intent_id = trans_info.get('intent_id', '')
            
            # 收集所有相同意图的 trans_info（包含不同条件的路由）
            if intent_name not in intent_to_trans_info_list:
                intent_to_trans_info_list[intent_name] = []
            intent_to_trans_info_list[intent_name].append(trans_info)
            
            # 如果这个意图已经处理过，跳过（不重复添加 semantic_condition）
            if intent_name in processed_intents:
                logger.debug(f"    ⏭️ 意图 '{intent_name}' 已存在，跳过重复添加（将合并条件分支）")
                continue
            
            processed_intents.add(intent_name)
            
            # 从 intents_training_phrases 获取训练短语
            training_phrases = self.intents_training_phrases.get(intent_name, [])
            
            # 如果没有找到，尝试用 intent_id 查找
            if not training_phrases and intent_id:
                training_phrases = self.intents_training_phrases.get(intent_id, [])
            
            # 如果还是没有找到，尝试大小写不敏感匹配
            if not training_phrases:
                intent_name_lower = intent_name.lower()
                for key, phrases in self.intents_training_phrases.items():
                    if key.lower() == intent_name_lower:
                        training_phrases = phrases
                        logger.debug(f"    🔄 通过大小写不敏感匹配找到: '{key}' -> '{intent_name}'")
                        break
            
            # 调试日志：检查训练短语是否正确加载
            if training_phrases:
                logger.debug(f"    ✅ 意图 '{intent_name}' 找到 {len(training_phrases)} 个训练短语")
            else:
                logger.warning(f"    ⚠️ 意图 '{intent_name}' (id={intent_id}) 没有找到训练短语！")
                logger.debug(f"       可用的 intents_training_phrases keys (前10个): {list(self.intents_training_phrases.keys())[:10]}")
            
            # 构建 positive_examples
            # 使用 "question" 字段以匹配 semanticJudgment 节点的标准格式
            positive_examples = []
            for phrase in training_phrases:
                positive_examples.append({
                    "id": short_id(),
                    "question": phrase
                })
            
            # 生成 condition_id（一个意图只有一个 condition_id）
            condition_id = str(uuid.uuid4())
            intent_to_condition_id[intent_name] = condition_id
            
            # 构建单个 semantic_condition
            semantic_condition = {
                "condition_id": condition_id,
                "name": intent_name,
                "desc": "",
                "refer_questions": [
                    {
                        "id": short_id(),
                        "question": ""
                    }
                ],
                "positive_examples": positive_examples,
                "negative_examples": [
                    {
                        "id": short_id(),
                        "question": ""
                    }
                ],
                "condition_config": {
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
                }
            }
            semantic_conditions.append(semantic_condition)
        
        # 构建 default_condition（fallback）
        default_condition_id = str(uuid.uuid4())
        default_condition = {
            "condition_id": default_condition_id,
            "name": "Fallback_Intent",
            "desc": "",
            "refer_questions": [],
            "condition_config": {
                "keyword_enable": False,
                "keywords": [],
                "keyword_type": 0,
                "regular_enable": False,
                "regular_str": "",
                "sft_model_enable": False,
                "sft_model_name": "",
                "sft_model_reponse_structure": {
                    "label": "",
                    "value": ""
                },
                "llm_enable": True,
                "embedding_enable": False
            }
        }
        
        # 根据语言设置 embedding_language
        embedding_language = lang if lang else "en"
        
        # 构建完整的语义判断节点
        semantic_node = {
            "id": semantic_node_id,
            "type": "semanticJudgment",
            "name": semantic_node_name,
            "title": "Intent Recognition (Semantic)",
            "initialized": False,
            "position": {"x": 0, "y": 0},
            "data": {
                "sourceHandle": str(uuid.uuid4()),
                "showToolBar": False
            },
            "blockId": str(uuid.uuid4()),
            "hidden": True,
            "config": {
                "semantic_conditions": semantic_conditions,
                "default_condition": default_condition,
                "global_config": {
                    "is_chatflow": True,
                    "confidence": self.global_config.get("semantic_confidence", 50),
                    "is_start_intent": 0,
                    "embedding_model_name": "bge-m3",
                    "embedding_rerank_enable": True,
                    "embedding_rerank_model_name": "bge-reranker-v2-m3",
                    "embedding_rerank_confidence": 0,
                    "embedding_llm_enable": False,
                    "allow_update_embedding": True,
                    "embedding_confidence": 0,
                    "embedding_llm_model_name": self.global_config.get('llmcodemodel', 'qwen3-30b-a3b'),
                    "embedding_llm_prompt": "",
                    "embedding_llm_return_count": 0,
                    "embedding_language": embedding_language
                },
                "title": "Intent Recognition"
            }
        }
        nodes.append(semantic_node)
        
        # write by senlin.deng 2026-01-07
        # 复用版本一的条件节点参数提取逻辑
        # 3. 根据是否有参数，决定后续处理方式
        # **修改：版本2只替换kb+code+condition(intent check)，其他逻辑复用版本一**
        # 如果有参数，需要将semanticJudgment的输出转换为intent_variable，然后复用版本一的参数提取逻辑
        
        # 3.1 检查是否有参数需要提取
        has_any_parameter_for_extraction = False
        for trans_info in transition_info_list:
            if trans_info.get('has_intent'):
                intent_id = trans_info.get('intent_id', '')
                parameters = intent_parameters_map.get(intent_id, [])
                if parameters or trans_info.get('has_condition'):
                    has_any_parameter_for_extraction = True
                    break
        
        if has_any_parameter_for_extraction:
            # **有参数：复用版本一的参数提取逻辑**
            # semanticJudgment节点不输出变量，而是通过condition_id作为边的起点连接到intent_check节点
            # 但是版本一的参数提取逻辑需要intent_variable，所以我们需要创建一个虚拟的intent_variable
            # 实际上，intent_check节点会根据semanticJudgment的condition_id边来路由，不需要判断intent_variable
            # 但为了复用版本一的代码，我们仍然需要intent_variable（虽然不会被使用）
            intent_variable = "intent"  # 虚拟变量，不会被实际使用
            
            # 3.1.1 复用版本一的参数提取逻辑
            # 注意：intent_check节点会根据semanticJudgment的condition_id边来路由，而不是通过intent_variable判断
            nodes_param, branches_param = self._generate_parameter_extraction_nodes(
                page, transition_info_list, intent_variable,
                intents_mapping=intents_mapping,
                intent_parameters_map=intent_parameters_map,
                gen_unique_node_name=gen_unique_node_name,
                gen_variable_name=gen_variable_name,
                generate_setparameter_code_node_func=generate_setparameter_code_node_func,
                entity_candidates=entity_candidates,
                lang=lang,
                node_counter_ref=node_counter_ref,
                skip_intent_check=True
            )
            
            # writed by senlin.deng 2026-01-22
            # 修复：为生成的 LLM、CODE、param_condition 节点设置 from_semantic_condition_id 属性
            # 这样边生成代码能正确识别这些节点并生成 param_condition 分支到 target_page 的边
            for node in nodes_param:
                node_type = node.get('type', '')
                title = node.get('title', '')
                # 从 title 中提取 intent_name，然后找到对应的 semantic_condition_id
                if node_type in ('llmVariableAssignment', 'code') and 'for ' in title:
                    intent_name_from_title = title.split('for ')[-1]
                    cond_id = intent_to_condition_id.get(intent_name_from_title)
                    if cond_id:
                        node['from_semantic_condition_id'] = cond_id
                elif node_type == 'condition' and ('Parameter Routing for' in title or 'Parameter Routing (' in title):
                    # param_condition 节点：从 title 中提取 intent_name
                    if 'for ' in title:
                        intent_name_from_title = title.split('for ')[-1]
                        cond_id = intent_to_condition_id.get(intent_name_from_title)
                        if cond_id:
                            node['from_semantic_condition_id'] = cond_id
            
            nodes.extend(nodes_param)
            condition_branches.extend(branches_param)
            
            # 记录intent_check节点的"是"分支连接的下一个节点，用于边生成
            # semanticJudgment节点直接连接到intent_check的"是"分支的下一个节点（通常是LLM节点）
            intent_check_nodes = [n for n in nodes_param if n.get('type') == 'condition' and 'Check if Intent is' in n.get('title', '')]
            condition_id_to_next_node = {}  # semanticJudgment的condition_id → intent_check的"是"分支的下一个节点名
            
            for intent_name, cond_id in intent_to_condition_id.items():
                # 找到对应的intent_check节点
                for check_node in intent_check_nodes:
                    if intent_name in check_node.get('title', ''):
                        # 找到"是"分支（is_{intent_name}）的target_node
                        for branch in check_node.get('if_else_conditions', []):
                            branch_id = branch.get('condition_id', '')
                            if branch_id.startswith('is_') and intent_name in branch_id:
                                # 获取"是"分支连接的下一个节点（通常是LLM节点）
                                next_node = branch.get('target_node')
                                if next_node:
                                    condition_id_to_next_node[cond_id] = next_node
                                break
                        break
            
            semantic_node["_condition_id_to_next_node"] = condition_id_to_next_node
            semantic_node["_reuse_v1_parameter_extraction"] = True  # 标记：复用版本一的参数提取逻辑
            
        else:
            # **没有参数：直接生成condition节点（类似版本一的无参数情况）**
            # semanticJudgment节点不输出变量，而是通过condition_id作为边的起点连接到condition节点
            intent_variable = "intent"  # 虚拟变量，不会被实际使用
            
            # writed by senlin.deng 2026-01-14
            # 3.2.2 为有 setParameterActions 的 transition 生成 code 节点
            # 为有 beforeTransition.staticUserResponse 的 transition 生成 text 节点
            transition_code_nodes = {}  # condition_id -> code_node_name
            transition_text_nodes = {}  # condition_id -> list of text_node_names
            for idx, trans_info in enumerate(transition_info_list, 1):
                if not trans_info['has_intent']:
                    continue
                
                intent_name = trans_info['intent_name']
                condition_id_for_intent = intent_to_condition_id.get(intent_name)
                if not condition_id_for_intent:
                    continue
                
                # 处理 setParameterActions
                set_param_actions = trans_info.get('set_parameter_actions', [])
                if set_param_actions:
                    code_node, _ = generate_setparameter_code_node_func(
                        set_param_actions, page_id, intent_name
                    )
                    if code_node:
                        nodes.append(code_node)
                        transition_code_nodes[condition_id_for_intent] = code_node['name']
                
                # 处理 beforeTransition.staticUserResponse
                before_transition_text_nodes = trans_info.get('before_transition_text_nodes', [])
                if before_transition_text_nodes:
                    text_node_names = []
                    for text_node in before_transition_text_nodes:
                        nodes.append(text_node)
                        text_node_names.append(text_node['name'])
                    transition_text_nodes[condition_id_for_intent] = text_node_names
            
            # 3.2.2 生成条件判断节点（意图路由）
            # 注意：这个condition节点不会被semanticJudgment直接连接
            # semanticJudgment会通过condition_id边连接到对应的分支
            # 但是为了保持结构一致，我们仍然生成这个节点
            intent_condition_node_name = gen_unique_node_name('intent_condition', page_id)
            intent_branches = []
            
            for idx, trans_info in enumerate(transition_info_list, 1):
                if not trans_info['has_intent']:
                    continue
                
                intent_name = trans_info['intent_name']
                condition_id = intent_to_condition_id.get(intent_name)
                if not condition_id:
                    continue
                
                # 如果同时有condition，添加到条件列表
                conditions_list = []
                if trans_info['has_condition']:
                    conditions_list.append({
                        "condition_type": "variable",
                        "comparison_operator": trans_info['condition_operator'],
                        "condition_value": str(trans_info['condition_value']),
                        "condition_variable": trans_info['condition_variable']
                    })
                
                branch = {
                    "condition_id": condition_id,  # 使用semanticJudgment的condition_id
                    "condition_name": f"Intent_{intent_name}",
                    "logical_operator": "and" if conditions_list else "other",
                    "conditions": conditions_list,
                    "condition_action": [],
                    "target_page_id": trans_info['target_page_id'],
                    "target_flow_id": trans_info['target_flow_id'],
                    "transition_code_node": transition_code_nodes.get(condition_id),
                    "transition_text_nodes": transition_text_nodes.get(condition_id, []),  # Record corresponding text nodes
                    "is_always_true": trans_info.get('is_always_true', False),
                    "from_semantic_node": semantic_node_name
                }
                intent_branches.append(branch)
                condition_branches.append(branch)
            
            # 添加fallback条件（使用唯一ID）
            fallback_condition = {
                "condition_id": default_condition_id,
                "condition_name": "Fallback",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": [],
                "from_semantic_node": semantic_node_name
            }
            intent_branches.append(fallback_condition)
            condition_branches.append(fallback_condition)
            
            intent_condition_node = {
                "type": "condition",
                "name": intent_condition_node_name,
                "title": "Intent Routing",
                "if_else_conditions": intent_branches,
                "_from_semantic_judgment": True  # 标记：从semanticJudgment节点连接
            }
            nodes.append(intent_condition_node)
            
            semantic_node["_intent_routing_condition_node"] = intent_condition_node_name
            semantic_node["_reuse_v1_parameter_extraction"] = False  # 标记：无参数情况
            semantic_node["_intent_to_condition_id"] = intent_to_condition_id  # 保存映射关系，用于边生成
        
        # 添加 fallback 分支
        fallback_branch = {
            "condition_id": default_condition_id,
            "condition_name": "Fallback",
            "logical_operator": "other",
            "conditions": [],
            "condition_action": [],
            "from_semantic_node": semantic_node_name
        }
        condition_branches.append(fallback_branch)
        
        # 记录语义判断节点的分支信息（仅用于内部边生成，不保存到最终输出）
        # 只保留语义判断节点本身的分支（意图分支），不包含 param_condition 的分支
        semantic_branches_for_edges = []
        for intent_name, cond_id in intent_to_condition_id.items():
            trans_info_list = intent_to_trans_info_list.get(intent_name, [])
            first_trans_info = trans_info_list[0] if trans_info_list else {}
            # 从 condition_branches 中获取 transition_code_node 和 transition_text_nodes
            transition_code_node = None
            transition_text_nodes = []
            for branch in condition_branches:
                if branch.get('condition_id') == cond_id:
                    transition_code_node = branch.get('transition_code_node')
                    transition_text_nodes = branch.get('transition_text_nodes', [])
                    break
            semantic_branches_for_edges.append({
                "condition_id": cond_id,
                "condition_name": f"Intent_{intent_name}",
                "target_page_id": first_trans_info.get('target_page_id'),
                "target_flow_id": first_trans_info.get('target_flow_id'),
                "is_always_true": first_trans_info.get('is_always_true', False),
                "set_parameter_actions": first_trans_info.get('set_parameter_actions', []),
                "before_transition_text_nodes": first_trans_info.get('before_transition_text_nodes', []),
                "transition_code_node": transition_code_node,  # 新增：transition_code_node
                "transition_text_nodes": transition_text_nodes,  # 新增：transition_text_nodes
                "from_semantic_node": semantic_node_name,
                "has_multiple_conditions": len(trans_info_list) > 1  # 标记是否有多个条件分支
            })
        semantic_branches_for_edges.append(fallback_branch)
        
        # 将分支信息存储到节点的 _internal_branches 字段（前缀 _ 表示内部使用）
        semantic_node["_internal_branches"] = semantic_branches_for_edges
        
        # 4. 生成 Jump to Main Agent 节点
        # write by senlin.deng 2026-01-21: 替换原来的 Fallback Message 节点
        fallback_node_name = gen_unique_node_name('jump_to_main_agent', page_id)
        fallback_node = self._create_jump_to_main_agent_node(fallback_node_name)
        nodes.append(fallback_node)
        
        logger.debug(f"  ✅ 生成语义判断节点: {semantic_node_name}, 包含 {len(semantic_conditions)} 个意图条件, has_parameter={has_any_parameter}")
        
        return nodes, condition_branches
    
   
    
    def _generate_intent_and_condition_nodes(
        self, 
        page: Dict[str, Any], 
        transition_info_list: List[Dict[str, Any]],
        intents_mapping: Dict[str, str] = None,
        intent_parameters_map: Dict[str, List[Dict[str, Any]]] = None,
        gen_unique_node_name: Callable[[str, str], str] = None,
        gen_variable_name: Callable[[], str] = None,
        generate_setparameter_code_node_func: Callable = None,
        entity_candidates: Dict[str, Dict[str, List[str]]] = None,
        lang: str = 'en',
        node_counter_ref: List[int] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        根据transitionInfo生成不同情况的节点
        
        支持四种情况：
        1. 有intent无condition：capture+kb+code(extract intent)+condition → target_pages
        2. 有intent有condition：链式判断结构
           - capture+kb+code(extract intent) → intent_check(链式)
           - 每个intent：意图匹配 → LLM(提取condition变量) → CODE → param_condition → target_pages
           - 意图不匹配 → 下一个intent_check 或 fallback
        3. 纯条件分支（无intent）：condition → target_pages
        4. intent单独抽取+多个condition分支（新情况）：
           - 第1个route是intent触发（无target或target为空）
           - 后续routes是condition判断（判断session参数）
           - 流程：capture → kb → extract_intent → check_intent(匹配) → llm(提取参数) → code → param_condition → target_pages
        
        Args:
            page: page配置
            transition_info_list: transitionEvent信息列表
            intents_mapping: 意图ID到名称的映射
            intent_parameters_map: 意图参数映射
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            generate_setparameter_code_node_func: 生成setParameter代码节点的函数
            entity_candidates: 实体候选值映射
            lang: 语言代码
            node_counter_ref: 节点计数器引用
            
        Returns:
            (节点列表, 条件分支列表)
        """
        # 使用传入的参数，如果没有传入则使用 self 的方法（向后兼容）
        if intents_mapping is None:
            intents_mapping = self.intents_mapping
        if intent_parameters_map is None:
            intent_parameters_map = self.intent_parameters_map
        if gen_unique_node_name is None:
            gen_unique_node_name = self._generate_unique_node_name
        if gen_variable_name is None:
            gen_variable_name = self._generate_variable_name
        if generate_setparameter_code_node_func is None:
            generate_setparameter_code_node_func = self._generate_setparameter_code_node
        if entity_candidates is None:
            entity_candidates = self.entity_candidates
        if lang is None or lang == '':
            lang = self.lang
        if node_counter_ref is None:
            node_counter_ref = [self.node_counter]
        
        nodes = []
        condition_branches = []
        
        if not transition_info_list:
            return nodes, condition_branches
        
        # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
        page_id = page.get('key') or page.get('pageId', '')
        
        # 判断是否有任何intent需要处理
        has_any_intent = any(t['has_intent'] for t in transition_info_list)
        # 修改：如果有parameters OR 有condition（参数条件），都应该走链式结构
        has_any_parameter = any(t.get('has_parameters') or t.get('has_condition') for t in transition_info_list)
        # 纯条件页面（Pattern 3）：没有 intent，且至少有一个 route 具备：
        # - 显式条件 (has_condition)，或
        # - condition 为 true 且有 target/setParameterActions（典型 Input 页）
        has_only_conditions = (
            not has_any_intent and any(
                t['has_condition'] or (
                    t.get('is_always_true')
                    and (t.get('target_page_id') or t.get('target_flow_id') or t.get('set_parameter_actions'))
                )
                for t in transition_info_list
            )
        )
        
        # 情况3：纯条件分支（没有intent，只有condition）
        if has_only_conditions:
            nodes_p3, branches_p3, direct_conns_p3 = self._generate_pure_condition_nodes(
                page, transition_info_list,
                gen_unique_node_name=gen_unique_node_name,
                gen_variable_name=gen_variable_name,
                generate_setparameter_code_node_func=generate_setparameter_code_node_func
            )
            # Store direct_connections in nodes for later edge generation
            if direct_conns_p3:
                for node in nodes_p3:
                    if node.get('type') not in ('condition', 'jump'):
                        node['_direct_connections'] = direct_conns_p3
                        break
            return nodes_p3, branches_p3

        # **新增判断：检查是否是"intent单独抽取+多个condition"的情况**
        # 特征：第1个是intent route（没有target或target为空），后续是condition routes
        is_intent_plus_conditions_separated = False
        if has_any_intent and len(transition_info_list) > 1:
            # 收集intent routes和condition routes
            intent_routes = [t for t in transition_info_list if t['has_intent']]
            condition_routes = [t for t in transition_info_list if not t['has_intent'] and t['has_condition']]

            # 如果有1个或多个intent routes（且没有target），且有多个condition routes
            if intent_routes and condition_routes:
                # 检查intent routes是否没有有效的target
                intents_without_target = [t for t in intent_routes if not t.get('target_page_id') and not t.get('target_flow_id')]
                if intents_without_target and len(condition_routes) >= 2:
                    # 这是新情况：intent单独抽取，后续用condition判断
                    is_intent_plus_conditions_separated = True

        # writed by senlin.deng 2026-01-16
        # 修复：情况四的生成逻辑，在版本1下，优先处理特殊情况（intent + conditions分离模式）
        # 情况4：intent单独抽取+多个condition分支（新情况）,优先处理特殊情况（intent + conditions分离模式）
        if is_intent_plus_conditions_separated and self.intent_recognition_version == 1:
            try:
                result = self._generate_intent_extraction_plus_conditions(
                    page, transition_info_list,
                    intents_mapping=intents_mapping,
                    intent_parameters_map=intent_parameters_map,
                    gen_unique_node_name=gen_unique_node_name,
                    gen_variable_name=gen_variable_name,
                    generate_setparameter_code_node_func=generate_setparameter_code_node_func,
                    entity_candidates=entity_candidates,
                    lang=lang,
                    node_counter_ref=node_counter_ref
                )
                return result
            except Exception as e:
                logger.error(f"  ⚠️ Intent and Condition Special case processing failed: {e}")
                # 继续执行标准处理

        # **版本2：使用语义判断节点进行意图识别**
        # 当 intent_recognition_version == 2 且有 intent 时，使用 semanticJudgment 节点
        # 无论是哪种情况，只要有 intent 就使用语义判断节点替换 kb+code+condition
        if self.intent_recognition_version == 2 and has_any_intent:
            try:
                # writed by senlin.deng 2026-01-17
                # 检查是否同时存在 Intent 路由和纯条件路由
                intent_routes = [t for t in transition_info_list if t['has_intent']]
                pure_condition_routes = [t for t in transition_info_list if not t['has_intent'] and t['has_condition']]
                
                # 如果同时存在 Intent 路由和纯条件路由，使用专门的处理函数
                if intent_routes and pure_condition_routes:
                    result = self._generate_semantic_judgment_with_pure_conditions(
                        page, transition_info_list,
                        gen_unique_node_name=gen_unique_node_name,
                        gen_variable_name=gen_variable_name,
                        generate_setparameter_code_node_func=generate_setparameter_code_node_func,
                        intents_mapping=intents_mapping,
                        intent_parameters_map=intent_parameters_map,
                        entity_candidates=entity_candidates,
                        lang=lang,
                        node_counter_ref=node_counter_ref,
                        has_any_parameter=has_any_parameter
                    )
                    return result
                else:
                    # 标准的语义判断节点处理（只有Intent路由）
                    result = self._generate_semantic_judgment_node(
                        page, transition_info_list,
                        gen_unique_node_name=gen_unique_node_name,
                        gen_variable_name=gen_variable_name,
                        generate_setparameter_code_node_func=generate_setparameter_code_node_func,
                        intents_mapping=intents_mapping,
                        intent_parameters_map=intent_parameters_map,
                        entity_candidates=entity_candidates,
                        lang=lang,
                        node_counter_ref=node_counter_ref,
                        has_any_parameter=has_any_parameter
                    )
                    return result
            except Exception as e:
                logger.error(f"版本2语义判断节点处理失败: {e}")
                import traceback
                traceback.print_exc()
                raise
        
        # 情况1和2：有intent的情况（版本1：使用 kb + code + condition）
        if has_any_intent:
            # 1. 生成收集用户回复节点
            # 1. 生成capture节点 - 使用固定变量名 last_user_response
            capture_variable = "last_user_response"
            capture_node_name = gen_unique_node_name('capture_input', page_id)
            capture_node = {
                "type": "captureUserReply",
                "name": capture_node_name,
                "title": "Capture User Input",
                "variable_assign": capture_variable
            }
            nodes.append(capture_node)
            
            # 2. 生成知识库检索节点（RAG）- RAG 使用 {{last_user_response}} 检索
            rag_output_variable = gen_variable_name()
            kb_node_name = gen_unique_node_name('kb_retrieval', page_id)
            
            # 收集这个 page 相关的所有 intent names (用于 Step 3 映射知识库ID)
            page_intents = [t.get('intent_name') for t in transition_info_list if t.get('has_intent') and t.get('intent_name')]
            
            kb_node = {
                "type": "knowledgeAssignment",
                "name": kb_node_name,
                "title": "Knowledge Base Retrieval",
                "variable_assign": rag_output_variable,
                "knowledge_base_ids": [10212],
                "rag_question": f"{{{{{capture_variable}}}}}",  # 使用模板变量格式 {{last_user_response}}
                "page_intents": page_intents  # 记录这个 page 对应的 intent(s)
            }
            nodes.append(kb_node)
            
            # 3. 生成CODE节点，从RAG结果中提取intent名称
            intent_variable = "intent"
            code_node_name = gen_unique_node_name('extract_intent', page_id)
            code_node = {
                "type": "code",
                "name": code_node_name,
                "title": "Extract Intent from RAG",
                "variable_assign": intent_variable,
                "code": f'''import re
def main({rag_output_variable}) -> dict:
    match = re.search(r"A:(.*)", {rag_output_variable})
    if match:
        result = match.group(1).strip()
    else:
        result = "unknown"
    return {{
        "{intent_variable}": result
    }}''',
                "outputs": [intent_variable],
                "args": [rag_output_variable]
            }
            nodes.append(code_node)
            
            # 情况1：有intent无parameter
            if not has_any_parameter:
                # 3.1 为有 setParameterActions 的 transition 生成 code 节点
                # 为有 beforeTransition.staticUserResponse 的 transition 生成 text 节点
                transition_code_nodes = {}  # condition_id -> code_node_name
                transition_text_nodes = {}  # condition_id -> list of text_node_names
                for idx, trans_info in enumerate(transition_info_list, 1):
                    if not trans_info['has_intent']:
                        continue
                    
                    condition_id = f"intent_{idx}"
                    
                    # 处理 setParameterActions
                    set_param_actions = trans_info.get('set_parameter_actions', [])
                    if set_param_actions:
                        intent_name = trans_info['intent_name']
                        code_node, _ = generate_setparameter_code_node(
                            set_param_actions, page_id, intent_name, gen_unique_node_name
                        )
                        if code_node:
                            nodes.append(code_node)
                            transition_code_nodes[condition_id] = code_node['name']
                    
                    # 处理 beforeTransition.staticUserResponse
                    before_transition_text_nodes = trans_info.get('before_transition_text_nodes', [])
                    if before_transition_text_nodes:
                        text_node_names = []
                        for text_node in before_transition_text_nodes:
                            nodes.append(text_node)
                            text_node_names.append(text_node['name'])
                        transition_text_nodes[condition_id] = text_node_names
                
                # 4. 生成条件判断节点（意图匹配）
                intent_condition_node_name = gen_unique_node_name('intent_condition', page_id)
                intent_branches = []
                
                for idx, trans_info in enumerate(transition_info_list, 1):
                    if not trans_info['has_intent']:
                        continue
                    
                    intent_name = trans_info['intent_name']
                    condition_id = f"intent_{idx}"
                    
                    # 基础条件：intent匹配
                    conditions_list = [{
                        "condition_type": "variable",
                        "comparison_operator": "=",
                        "condition_value": intent_name,
                        "condition_variable": intent_variable
                    }]
                    
                    # 如果同时有condition，也添加到条件列表
                    if trans_info['has_condition']:
                        conditions_list.append({
                            "condition_type": "variable",
                            "comparison_operator": trans_info['condition_operator'],
                            "condition_value": str(trans_info['condition_value']),
                            "condition_variable": trans_info['condition_variable']
                        })
                    
                    branch = {
                        "condition_id": condition_id,
                        "condition_name": f"Intent_{intent_name}",
                        "logical_operator": "and",
                        "conditions": conditions_list,
                        "condition_action": [],
                        "target_page_id": trans_info['target_page_id'],
                        "target_flow_id": trans_info['target_flow_id'],
                        "transition_code_node": transition_code_nodes.get(condition_id),  # 记录对应的code节点
                        "transition_text_nodes": transition_text_nodes.get(condition_id, []),  # 记录对应的text节点
                        "is_always_true": trans_info.get('is_always_true', False)  # 传递 is_always_true 标记
                    }
                    intent_branches.append(branch)
                    condition_branches.append(branch)
                
                # 添加fallback条件（使用唯一ID）
                fallback_condition = {
                    "condition_id": f"fallback_condition_{page_id[:8]}_{node_counter_ref[0]}",
                    "condition_name": "Fallback",
                    "logical_operator": "other",
                    "conditions": [],
                    "condition_action": []
                }
                node_counter_ref[0] += 1
                intent_branches.append(fallback_condition)
                condition_branches.append(fallback_condition)
                
                intent_condition_node = {
                    "type": "condition",
                    "name": intent_condition_node_name,
                    "title": "Intent Routing",
                    "if_else_conditions": intent_branches
                }
                nodes.append(intent_condition_node)
                
                # 5. 生成 Jump to Main Agent 节点
                # write by senlin.deng 2026-01-21: 替换原来的 Fallback Message 节点
                fallback_node_name = gen_unique_node_name('jump_to_main_agent', page_id)
                fallback_node = self._create_jump_to_main_agent_node(fallback_node_name)
                nodes.append(fallback_node)
            
            # 情况2：有intent有parameter
            else:
                nodes_param, branches_param = self._generate_parameter_extraction_nodes(
                    page, transition_info_list, intent_variable,
                    intents_mapping=intents_mapping,
                    intent_parameters_map=intent_parameters_map,
                    gen_unique_node_name=gen_unique_node_name,
                    gen_variable_name=gen_variable_name,
                    generate_setparameter_code_node_func=generate_setparameter_code_node_func,
                    entity_candidates=entity_candidates,
                    lang=lang,
                    node_counter_ref=node_counter_ref
                )
                nodes.extend(nodes_param)
                condition_branches.extend(branches_param)
        
        return nodes, condition_branches
    
    def _generate_pure_condition_nodes(
        self, 
        page: Dict[str, Any], 
        transition_info_list: List[Dict[str, Any]],
        gen_unique_node_name: Callable[[str, str], str] = None,
        gen_variable_name: Callable[[], str] = None,
        generate_setparameter_code_node_func: Callable = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Pattern 3: Generate pure condition branch nodes (no intent)
        
        Args:
            page: Page configuration
            transition_info_list: List of transitionEvent information
            gen_unique_node_name: Function to generate unique node names
            gen_variable_name: Function to generate variable names
            generate_setparameter_code_node_func: Function to generate setParameter code nodes
            
        Returns:
            (list of nodes, list of condition branches, list of direct connections for is_always_true)
        """
        # 使用传入的参数，如果没有传入则使用 self 的方法（向后兼容）
        if gen_unique_node_name is None:
            gen_unique_node_name = self._generate_unique_node_name
        if gen_variable_name is None:
            gen_variable_name = self._generate_variable_name
        if generate_setparameter_code_node_func is None:
            generate_setparameter_code_node_func = self._generate_setparameter_code_node
        
        nodes = []
        condition_branches = []
        direct_connections = []  # For is_always_true routes that skip condition node
        
        # Support both formats: 'key' (converted format) and 'pageId' (original format)
        page_id = page.get('key') or page.get('pageId', '')
        
        # Generate code nodes for transitions with setParameterActions
        # Generate text nodes for transitions with beforeTransition.staticUserResponse
        transition_code_nodes = {}  # condition_id -> code_node_name
        transition_text_nodes = {}  # condition_id -> list of text_node_names
        for idx, trans_info in enumerate(transition_info_list, 1):
            # Handle is_always_true cases (even if has_condition is false)
            if not trans_info.get('has_condition', False) and not trans_info.get('is_always_true', False):
                continue
            
            condition_id = f"condition_{idx}"
            
            # 处理 setParameterActions
            set_param_actions = trans_info.get('set_parameter_actions', [])
            if set_param_actions:
                code_node, _ = generate_setparameter_code_node(
                    set_param_actions, page_id, f"Condition_{idx}", gen_unique_node_name
                )
                if code_node:
                    nodes.append(code_node)
                    transition_code_nodes[condition_id] = code_node['name']
            
            # 处理 beforeTransition.staticUserResponse
            before_transition_text_nodes = trans_info.get('before_transition_text_nodes', [])
            if before_transition_text_nodes:
                text_node_names = []
                for text_node in before_transition_text_nodes:
                    nodes.append(text_node)
                    text_node_names.append(text_node['name'])
                transition_text_nodes[condition_id] = text_node_names
        
        # Separate is_always_true routes from condition routes
        condition_node_name = gen_unique_node_name('condition', page_id)
        if_else_conditions = []
        
        # **NEW: 收集所有混合条件，合并到一个 code 节点**
        mixed_conditions_with_index = []  # [(branch_index, mixed_condition), ...]
        for idx, trans_info in enumerate(transition_info_list, 1):
            if trans_info.get('is_mixed_and_or') and trans_info.get('mixed_and_or_condition'):
                mixed_conditions_with_index.append((idx, trans_info['mixed_and_or_condition']))
        
        # write by senlin.deng 2026-01-19
        # 如果有混合条件，生成合并的 code 节点
        combined_mixed_code_node = None
        combined_output_var = None
        index_to_condition_value = {}  # {branch_index: "1", "2", ...}
        
        if mixed_conditions_with_index:
            combined_mixed_code_node, combined_output_var, index_to_condition_value = generate_combined_mixed_condition_code_node(
                mixed_conditions_with_index,
                page_id,
                gen_unique_node_name
            )
            nodes.append(combined_mixed_code_node)
            logger.debug(f"  ✓ Generated combined mixed condition code node with {len(mixed_conditions_with_index)} conditions")
        
        for idx, trans_info in enumerate(transition_info_list, 1):
            # Handle is_always_true cases (even if has_condition is false)
            if not trans_info.get('has_condition', False) and not trans_info.get('is_always_true', False):
                continue
            
            condition_id = f"condition_{idx}"
            is_always_true = trans_info.get('is_always_true', False)
            
            # **NEW: If is_always_true, add to direct_connections instead of condition_branches**
            if is_always_true:
                direct_connections.append({
                    'target_page_id': trans_info['target_page_id'],
                    'target_flow_id': trans_info['target_flow_id'],
                    'transition_code_node': transition_code_nodes.get(condition_id),
                    'transition_text_nodes': transition_text_nodes.get(condition_id, []),
                    'set_parameter_actions': trans_info.get('set_parameter_actions', [])
                })
                logger.debug(f"    ✓ Route {idx} is always_true, will create direct edge (skip condition node)")
                continue
            
            # Only add real conditions to condition branches
            conditions = []
            logical_op = "and"  # Default

            if trans_info.get('has_condition', False):
                # **NEW: 混合条件使用合并的 code 节点输出**
                if trans_info.get('is_mixed_and_or') and idx in index_to_condition_value:
                    # 使用合并 code 节点的输出，condition_value 是 "1", "2", "3" 等
                    conditions = [{
                        "condition_type": "variable",
                        "comparison_operator": "=",
                        "condition_value": index_to_condition_value[idx],  # "1", "2", "3", ...
                        "condition_variable": combined_output_var  # "mixed_condition_result"
                    }]
                    logical_op = "and"
                # **Support multiple AND/OR conditions**
                elif trans_info.get('and_conditions_list'):
                    and_conditions = trans_info.get('and_conditions_list', [])
                    is_or = trans_info.get('is_or_condition', False)
                    conditions = [
                        {
                            "condition_type": "variable",
                            "comparison_operator": cond['operator'],
                            "condition_value": str(cond['value']) if cond['value'] is not None else "",
                            # writed by senlin.deng 2026-01-13
                            # 将 condition_variable 转换为小写，确保一致性
                            "condition_variable": (cond.get('variable', '') or '').replace('-', '_').lower()
                        }
                        for cond in and_conditions
                    ]
                    # Set logical operator based on condition type
                    logical_op = "or" if is_or else "and"
                else:
                    conditions = [{
                        "condition_type": "variable",
                        "comparison_operator": trans_info.get('condition_operator', '='),
                        "condition_value": str(trans_info.get('condition_value', '')),
                        # writed by senlin.deng 2026-01-13
                        # 将 condition_variable 转换为小写，确保一致性
                        "condition_variable": (trans_info.get('condition_variable', '') or '').replace('-', '_').lower()
                    }]
            
            branch = {
                "condition_id": condition_id,
                "condition_name": f"Condition_{idx}",
                "logical_operator": logical_op if conditions else "other",
                "conditions": conditions,
                "condition_action": [],
                "target_page_id": trans_info['target_page_id'],
                "target_flow_id": trans_info['target_flow_id'],
                "transition_code_node": transition_code_nodes.get(condition_id),  # Record corresponding code node
                "transition_text_nodes": transition_text_nodes.get(condition_id, []),  # Record corresponding text nodes
                "combined_mixed_code_node": combined_mixed_code_node['name'] if combined_mixed_code_node else None,  # 新增：合并的混合条件 code 节点
                "is_always_true": False  # Only real conditions here now
            }
            if_else_conditions.append(branch)
            condition_branches.append(branch)
        
        # **NEW: Only create Condition Routing node if there are real conditions (not just is_always_true)**
        if if_else_conditions:
            # Add fallback condition (using unique ID)
            fallback_condition = {
                "condition_id": f"fallback_condition_{page_id[:8]}_{self.node_counter}",
                "condition_name": "Fallback",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": []
            }
            self.node_counter += 1
            if_else_conditions.append(fallback_condition)
            condition_branches.append(fallback_condition)
            
            condition_node = {
                "type": "condition",
                "name": condition_node_name,
                "title": "Condition Routing",
                "if_else_conditions": if_else_conditions
            }
            nodes.append(condition_node)
            
            # 标记入口节点：优先 combined_mixed_code_node，否则 condition_node
            if combined_mixed_code_node:
                combined_mixed_code_node['_is_entry_node'] = True
            else:
                condition_node['_is_entry_node'] = True
        else:
            logger.debug(f"  ✓ All routes are is_always_true, skipping Condition Routing node")
        
        return nodes, condition_branches, direct_connections
    
    # 多意图路由+纯条件路由混合的情况，kb
    def _generate_intent_extraction_plus_conditions(
        self, 
        page: Dict[str, Any], 
        transition_info_list: List[Dict[str, Any]],
        intents_mapping: Dict[str, str] = None,
        intent_parameters_map: Dict[str, List[Dict[str, Any]]] = None,
        gen_unique_node_name: Callable[[str, str], str] = None,
        gen_variable_name: Callable[[], str] = None,
        generate_setparameter_code_node_func: Callable = None,
        entity_candidates: Dict[str, Dict[str, List[str]]] = None,
        lang: str = 'en',
        node_counter_ref: List[int] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Pattern 4: Generate nodes for intent extraction + condition branches
        
        Characteristics:
        - First route is intent trigger (no target or empty target)
        - Subsequent routes are condition checks (checking session parameters)
        
        Flow:
        capture → kb → extract_intent → check_intent (is intent matched?)
          ├─ Yes → llm (extract parameters) → code (parse) → param_condition (check param value) → target_pages
          └─ No → fallback → capture (loop)
        
        Note: Only one capture is needed, no second capture required
        
        Args:
            page: Page configuration
            transition_info_list: List of transitionEvent information
            intents_mapping: Intent ID to name mapping
            intent_parameters_map: Intent parameters mapping
            gen_unique_node_name: Function to generate unique node names
            gen_variable_name: Function to generate variable names
            generate_setparameter_code_node_func: Function to generate setParameter code nodes
            entity_candidates: Entity candidates mapping
            lang: Language code
            node_counter_ref: Node counter reference
            
        Returns:
            (list of nodes, list of condition branches)
        """
        # 使用传入的参数，如果没有传入则使用 self 的方法（向后兼容）
        if intents_mapping is None:
            intents_mapping = self.intents_mapping
        if intent_parameters_map is None:
            intent_parameters_map = self.intent_parameters_map
        if gen_unique_node_name is None:
            gen_unique_node_name = self._generate_unique_node_name
        if gen_variable_name is None:
            gen_variable_name = self._generate_variable_name
        if generate_setparameter_code_node_func is None:
            generate_setparameter_code_node_func = self._generate_setparameter_code_node
        if entity_candidates is None:
            entity_candidates = self.entity_candidates
        if lang is None or lang == '':
            lang = self.lang
        if node_counter_ref is None:
            node_counter_ref = [self.node_counter]
        
        nodes = []
        condition_branches = []
        page_id = page.get('key') or page.get('pageId', '')
        
        # 1. 分离intent routes和condition routes
        intent_routes = [t for t in transition_info_list if t['has_intent']]
        condition_routes = [t for t in transition_info_list if not t['has_intent'] and t['has_condition']]

        # 如果没有intent routes但有condition routes，说明这不是我们预期的模式
        if not intent_routes:
            logger.error(f"  ⚠️ Warning: No intent routes found, this might not be the expected pattern")
            return nodes, condition_branches

        # 如果没有condition routes，我们可以只处理intent路由（降级处理）
        if not condition_routes:
            logger.error(f"  ⚠️ Warning: No condition routes found, will process intent routes only")
            # 这里可以选择回退到标准处理，或者抛出异常让上层处理
            # 为了健壮性，我们选择抛出异常，让上层代码知道特殊处理失败
            raise ValueError("Intent extraction + conditions pattern requires both intent and condition routes")
        
        # 取第一个intent作为触发器
        intent_info = intent_routes[0]
        intent_name = intent_info.get('intent_name', 'unknown_intent')
        if not intent_name or intent_name == 'unknown_intent':
            logger.error(f"  ⚠️ Warning: Invalid intent name '{intent_name}', skipping special processing")
            return nodes, condition_branches
        
        # 2. 生成第一阶段：capture → kb → extract_intent节点链
        # 2.1 Capture节点
        capture_variable = "last_user_response"
        capture_node_name = gen_unique_node_name('capture_input', page_id)
        capture_node = {
            "type": "captureUserReply",
            "name": capture_node_name,
            "title": "Capture User Input",
            "variable_assign": capture_variable
        }
        nodes.append(capture_node)
        
        # 2.2 KB检索节点
        rag_output_variable = gen_variable_name()
        kb_node_name = gen_unique_node_name('kb_retrieval', page_id)
        kb_node = {
            "type": "knowledgeAssignment",
            "name": kb_node_name,
            "title": "Knowledge Base Retrieval",
            "variable_assign": rag_output_variable,
            "knowledge_base_ids": [10212],
            "rag_question": f"{{{{{capture_variable}}}}}",
            "page_intents": [intent_name]
        }
        nodes.append(kb_node)
        
        # 2.3 提取intent的CODE节点
        intent_variable = "intent"
        extract_intent_node_name = gen_unique_node_name('extract_intent', page_id)
        extract_intent_node = {
            "type": "code",
            "name": extract_intent_node_name,
            "title": "Extract Intent from RAG",
            "variable_assign": intent_variable,
            "code": f'''import re
def main({rag_output_variable}) -> dict:
    match = re.search(r"A:(.*)", {rag_output_variable})
    if match:
        result = match.group(1).strip()
    else:
        result = "unknown"
    return {{
        "{intent_variable}": result
    }}''',
            "outputs": [intent_variable],
            "args": [rag_output_variable]
        }
        nodes.append(extract_intent_node)
        
        # 3. 生成intent判断condition节点
        intent_check_node_name = gen_unique_node_name('check_intent', page_id)
        
        # 3.1 Intent匹配分支 → 继续处理
        intent_match_condition_id = f"intent_match_{intent_name}"
        
        # 3.2 Intent不匹配分支 → fallback
        intent_mismatch_condition_id = f"intent_mismatch_{intent_name}"
        
        intent_check_branches = [
            {
                "condition_id": intent_match_condition_id,
                "condition_name": f"Intent is {intent_name}",
                "logical_operator": "and",
                "conditions": [{
                    "condition_type": "variable",
                    "comparison_operator": "=",
                    "condition_value": intent_name,
                    "condition_variable": intent_variable
                }],
                "condition_action": [],
                "target_node": None  # 稍后连接到第二个capture或llm节点
            },
            {
                "condition_id": intent_mismatch_condition_id,
                "condition_name": "Intent mismatch",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": [],
                "target_node": None  # 稍后连接到fallback节点
            }
        ]
        
        intent_check_node = {
            "type": "condition",
            "name": intent_check_node_name,
            "title": f"Check if Intent is {intent_name}",
            "if_else_conditions": intent_check_branches
        }
        nodes.append(intent_check_node)
        
        # 记录这两个分支（用于后续生成edges）
        condition_branches.extend(intent_check_branches)
        
        # 4. 生成 Jump to Main Agent 节点
        # write by senlin.deng 2026-01-21: 替换原来的 Fallback Message 节点
        fallback_node_name = gen_unique_node_name('jump_to_main_agent', page_id)
        fallback_node = self._create_jump_to_main_agent_node(fallback_node_name)
        nodes.append(fallback_node)
        
        # 5. 如果intent匹配，直接生成LLM → CODE → param_condition链路
        # 注意：不需要第二个capture！用户已经在第一个capture中选择了按钮
        # 5.1 从condition routes中收集需要提取的变量（统一替换-为_）
        variables_to_extract = set()
        for cond_info in condition_routes:
            cond_var = cond_info.get('condition_variable')
            if cond_var:
                # 统一将-替换为_，避免Python变量名问题
                variables_to_extract.add(cond_var.replace('-', '_'))
        
        if not variables_to_extract:
            logger.error(f"  ⚠️ Warning: No condition variables found in condition routes")
            # 如果没有变量需要提取，这可能不是我们预期的模式
            # 我们可以尝试从condition routes中提取可能的变量名
            for cond_info in condition_routes:
                cond_var = cond_info.get('condition_variable')
                if cond_var:
                    variables_to_extract.add(cond_var.replace('-', '_'))

            if not variables_to_extract:
                logger.error(f"  ⚠️ Still no variables found after checking all conditions")
                # 如果实在没有变量，我们可以创建一个默认的
                variables_to_extract = set(['fallback_param'])
        
        # 5.2 根据 ner_version 选择参数提取模式
        ner_gen = self._init_ner_generator()
        
        # =====================================
        # Semantic NER 版本：使用 SemanticJudgment + Code 节点
        # =====================================
        ner_semantic_node = None
        ner_code_nodes = []
        ner_semantic_branches = []
        
        # 获取 intent 的 parameters 信息
        intent_id = intent_info.get('intent_id')
        parameters = intent_parameters_map.get(intent_id, []) if intent_parameters_map else []
        
        if self.ner_version == 'semantic' and parameters and isinstance(ner_gen, SemanticNERNodeGenerator):
            logger.debug(f"  🔄 [Pattern4] 使用 Semantic NER 版本为意图 {intent_name} 生成参数提取节点")
            
            try:
                # 构建 trans_info_list
                intent_trans_info_list = [t for t in transition_info_list if t.get('intent_name') == intent_name]
                
                semantic_nodes, semantic_branches = ner_gen.generate_parameter_nodes(
                    page_id=page_id,
                    intent_name=intent_name,
                    condition_id=f"is_{intent_name}",
                    trans_info_list=intent_trans_info_list + condition_routes,
                    parameters=parameters,
                    capture_variable=capture_variable,
                    gen_unique_node_name=gen_unique_node_name,
                    gen_variable_name=gen_variable_name,
                    lang=lang
                )
                
                if semantic_nodes:
                    ner_semantic_node = next((n for n in semantic_nodes if n.get('type') == 'semanticJudgment'), None)
                    ner_code_nodes = [n for n in semantic_nodes if n.get('type') == 'code']
                    ner_semantic_branches = semantic_branches
                    
                    if ner_semantic_node:
                        nodes.append(ner_semantic_node)
                        nodes.extend(ner_code_nodes)
                        logger.debug(f"    ✅ Semantic NER 生成了 {len(semantic_nodes)} 个节点")
            except Exception as e:
                logger.warning(f"    ⚠️ Semantic NER 生成失败，回退到 LLM 版本: {e}")
                import traceback
                traceback.print_exc()
                ner_semantic_node = None
        
        # =====================================
        # LLM NER 版本：使用 LLM + Code 节点（默认）
        # =====================================
        llm_node = None
        code_node = None
        llm_variable = None
        
        if not ner_semantic_node:
            llm_node_name = gen_unique_node_name('llm_extract_param', page_id)
            code_node_name = gen_unique_node_name('parse_params', page_id)
            
            # 构建 hint 文本
            hint_text = ner_gen.build_hint_text_for_kb(
                condition_routes=condition_routes,
                intent_parameters_map=intent_parameters_map,
                lang=lang
            )
            
            # 生成 LLM 和 CODE 节点
            llm_node, code_node, llm_variable = ner_gen.build_llm_and_code_nodes(
                variables_to_extract=variables_to_extract,
                capture_variable=capture_variable,
                llm_node_name=llm_node_name,
                code_node_name=code_node_name,
                gen_variable_name=gen_variable_name,
                hint_text=hint_text,
                llm_title="Extract Parameters from User Input",
                code_title="Parse Parameters from LLM"
            )
            nodes.append(llm_node)
            nodes.append(code_node)
        
        sorted_vars = sorted(variables_to_extract) if variables_to_extract else ['fallback_param']
        
        # 5.5 生成参数判断condition节点
        param_condition_node_name = gen_unique_node_name('param_condition', page_id)
        param_branches = []
        
        for idx, cond_info in enumerate(condition_routes, 1):
            condition_id = f"param_{idx}"
            mixed_code_node_name = None  # 用于存储混合条件的 code 节点名
            
            # **NEW: Support mixed AND+OR conditions (highest priority)**
            if cond_info.get('is_mixed_and_or') and cond_info.get('mixed_and_or_condition'):
                # 生成 code 节点处理混合条件
                mixed_code_node, output_var = generate_mixed_condition_code_node(
                    cond_info['mixed_and_or_condition'],
                    page_id,
                    gen_unique_node_name
                )
                nodes.append(mixed_code_node)
                mixed_code_node_name = mixed_code_node['name']
                
                # 条件分支使用 code 节点的输出
                conditions = [{
                    "condition_type": "variable",
                    "comparison_operator": "=",
                    "condition_value": "True",
                    "condition_variable": output_var.lower()
                }]
                logical_op = "and"
                condition_name = f"Param: Mixed AND+OR Condition #{idx}"
            # **Support AND/OR multi-conditions in Pattern 4**
            elif cond_info.get('and_conditions_list'):
                and_conditions = cond_info.get('and_conditions_list', [])
                is_or = cond_info.get('is_or_condition', False)
                # Multi-condition (AND or OR)
                conditions = [
                    {
                        "condition_type": "variable",
                        "comparison_operator": cond['operator'],
                        "condition_value": str(cond['value']) if cond['value'] is not None else "",
                        # writed by senlin.deng 2026-01-13
                        # 将 condition_variable 转换为小写，确保一致性
                        "condition_variable": (cond.get('variable', '') or '').replace('-', '_').lower()
                    }
                    for cond in and_conditions
                ]
                logical_op = "or" if is_or else "and"
                # Generate condition name from first condition
                first_cond = and_conditions[0]
                condition_name = f"Param: {first_cond['variable']} {first_cond['operator']} {first_cond['value']} (+ {len(and_conditions)-1} more)"
            else:
                # Single condition (original logic)
                cond_var = cond_info.get('condition_variable', '') or ''
                cond_op = cond_info.get('condition_operator', '=')
                cond_val = cond_info.get('condition_value', '')
                # writed by senlin.deng 2026-01-13
                # 将 condition_variable 转换为小写，确保一致性
                cond_var_normalized = cond_var.replace('-', '_').lower() if cond_var else ''
                
                conditions = [{
                    "condition_type": "variable",
                    "comparison_operator": cond_op,
                    "condition_value": str(cond_val),
                    "condition_variable": cond_var_normalized
                }]
                logical_op = "and"
                condition_name = f"Param: {cond_var_normalized} {cond_op} {cond_val}"
            
            # writed by senlin.deng 2026-01-14
            # 检查是否有 setParameterActions，如果有则生成 code 节点
            transition_code_node = None
            set_param_actions = cond_info.get('set_parameter_actions', [])
            if set_param_actions:
                code_node, _ = generate_setparameter_code_node_func(
                    set_param_actions, page_id, f"Condition_{idx}"
                )
                if code_node:
                    nodes.append(code_node)
                    transition_code_node = code_node['name']
            
            # 处理 beforeTransition.staticUserResponse
            transition_text_node_names = []
            before_transition_text_nodes = cond_info.get('before_transition_text_nodes', [])
            if before_transition_text_nodes:
                for text_node in before_transition_text_nodes:
                    nodes.append(text_node)
                    transition_text_node_names.append(text_node['name'])
            
            branch = {
                "condition_id": condition_id,
                "condition_name": condition_name,
                "logical_operator": logical_op,
                "conditions": conditions,
                "condition_action": [],
                "target_page_id": cond_info.get('target_page_id'),
                "target_flow_id": cond_info.get('target_flow_id'),
                "transition_code_node": transition_code_node,  # 记录对应的code节点
                "transition_text_nodes": transition_text_node_names,  # 记录对应的text节点
                "mixed_condition_code_node": mixed_code_node_name  # 新增：混合条件 code 节点
            }
            param_branches.append(branch)
            condition_branches.append(branch)
        
        # 添加fallback（参数不匹配时）
        param_fallback_condition = {
            "condition_id": f"param_fallback_{page_id[:8]}_{node_counter_ref[0]}",
            "condition_name": "Parameter Fallback",
            "logical_operator": "other",
            "conditions": [],
            "condition_action": [],
            "target_node": fallback_node_name  # 跳转到fallback
        }
        node_counter_ref[0] += 1
        param_branches.append(param_fallback_condition)
        condition_branches.append(param_fallback_condition)
        
        param_condition_node = {
            "type": "condition",
            "name": param_condition_node_name,
            "title": "Route by Parameter Value",
            "if_else_conditions": param_branches
        }
        nodes.append(param_condition_node)
        
        # 6. 更新intent_check_node的target_node
        intent_check_branches[0]["target_node"] = llm_node_name  # intent匹配 → LLM节点
        intent_check_branches[1]["target_node"] = fallback_node_name  # intent不匹配 → fallback
        
        print(f"  ✓ Generated {len(nodes)} nodes for intent extraction + conditions pattern")
        print(f"     Flow: capture → kb → extract_intent → check_intent → llm → code → param_condition")
        print(f"     Variables to extract: {sorted_vars}")
        print(f"     Condition branches: {len(condition_branches)}")

        # 最终验证：确保我们生成了有意义的节点
        if not nodes:
            raise ValueError("No nodes generated for intent extraction + conditions pattern")

        if not condition_branches:
            print(f"  ⚠️ Warning: No condition branches generated, this may cause routing issues")

        return nodes, condition_branches  # Pattern 4 handles is_always_true in param_condition, not in direct_connections
    
    def _generate_parameter_extraction_nodes(
        self, 
        page: Dict[str, Any], 
        transition_info_list: List[Dict[str, Any]],
        intent_variable: str,
        intents_mapping: Dict[str, str] = None,
        intent_parameters_map: Dict[str, List[Dict[str, Any]]] = None,
        gen_unique_node_name: Callable[[str, str], str] = None,
        gen_variable_name: Callable[[], str] = None,
        generate_setparameter_code_node_func: Callable = None,
        entity_candidates: Dict[str, Dict[str, List[str]]] = None,
        lang: str = 'en',
        node_counter_ref: List[int] = None,
        skip_intent_check: bool = False
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Pattern 2: Generate chain-style intent checking nodes with conditions
        
        Key: Extract variables from conditions, not just from parameter definitions!
        
        Flow (chain-style checking):
        capture → kb → code(extract intent) 
          ↓
        intent_check1 (is it intent1?)
        ├─ Yes → LLM1 (extract variables from condition) → CODE1 (parse JSON) → param_condition1 (check param values) → target_pages
        └─ No → intent_check2 (is it intent2?)
                   ├─ Yes → LLM2 (extract variables) → CODE2 → param_condition2 → target_pages
                   └─ No → fallback → capture (loop)
        
        Notes:
        - Collect variables_to_extract from both condition_variable and parameters
        - LLM extraction only happens when intent matches, otherwise go to next check or fallback
        - param_condition only checks parameter values, no redundant intent checking
        
        Args:
            page: Page configuration
            transition_info_list: List of transitionEvent information
            intent_variable: Intent variable name
            
        Returns:
            (list of nodes, list of condition branches)
        """
        nodes = []
        condition_branches = []
        # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
        page_id = page.get('key') or page.get('pageId', '')
        
        # 1. 创建 Jump to Main Agent 节点（意图不匹配时使用）
        # write by senlin.deng 2026-01-21: 替换原来的 Fallback Message 节点
        fallback_node_name = gen_unique_node_name('jump_to_main_agent', page_id)
        fallback_node = self._create_jump_to_main_agent_node(fallback_node_name)
        nodes.append(fallback_node)
        
        # 2. 收集所有需要链式处理的intent
        # 注意：即使intent route本身没有condition，但如果同一个page中有纯condition routes，
        # 这些condition routes的变量也应该由这个intent的LLM节点提取
        intent_chains = []  # 存储每个intent的完整节点链
        processed_intents = set()
        
        # 收集所有纯condition routes的condition variables（没有intent的routes）
        pure_condition_routes = [t for t in transition_info_list if not t['has_intent'] and t.get('has_condition')]
        pure_condition_vars = set()
        for pcr in pure_condition_routes:
            cond_var = pcr.get('condition_variable')
            if cond_var:
                pure_condition_vars.add(cond_var.replace('-', '_'))
        
        for trans_info in transition_info_list:
            if not trans_info['has_intent']:
                continue
            
             # writed by senlin.deng 2026-01-15
            # fixed：即使意图没有 params、condition，也应该处理（生成 intent_check 节点）
            # 修复page存在多个意图且一个意图没有实体抽取的情况
            # 否则会跳过只有 intent 触发、无条件的 transitionEvent（如 MakingPayments_CCR_PreviousPage）
            # 后续逻辑会根据是否有 variables_to_extract 来决定是否创建 LLM 和 CODE 节点
            
            # # 修改：如果有parameters OR 有condition OR 有纯condition routes，都应该处理
            # has_params = trans_info.get('has_parameters') and trans_info.get('parameters')
            # has_condition = trans_info.get('has_condition')
            # # 新增：如果有纯condition routes，也应该处理这个intent
            # has_related_conditions = len(pure_condition_routes) > 0
            # if not has_params and not has_condition and not has_related_conditions:
            #     continue
                        
            intent_name = trans_info['intent_name']
            parameters = trans_info.get('parameters', [])
            
            # 每个意图只处理一次
            if intent_name in processed_intents:
                continue
            processed_intents.add(intent_name)
            
            # 为这个intent创建完整的节点链
            intent_chain = {
                "intent_name": intent_name,
                "trans_info": trans_info,
                "nodes": []
            }
            # 2.1 创建intent检查condition节点
            intent_check_name = self._generate_unique_node_name(f'check_{intent_name}', page_id)
            intent_check_node = {
                "type": "condition",
                "name": intent_check_name,
                "title": f"Check if Intent is {intent_name}",
                "if_else_conditions": [
                    {
                        "condition_id": f"is_{intent_name}",
                        "condition_name": f"Is {intent_name}",
                        "logical_operator": "and",
                        "conditions": [{
                            "condition_type": "variable",
                            "comparison_operator": "=",
                            "condition_value": intent_name,
                            "condition_variable": intent_variable
                        }],
                        "condition_action": [],
                        "target_node": None  # 稍后设置为LLM节点
                    },
                    {
                        "condition_id": f"not_{intent_name}",
                        "condition_name": f"Not {intent_name}",
                        "logical_operator": "other",
                        "conditions": [],
                        "condition_action": [],
                        "target_node": None  # 稍后设置为下一个intent_check或fallback
                    }
                ]
            }
            intent_chain["intent_check"] = intent_check_node
            intent_chain["nodes"].append(intent_check_node)
            
            # 2.2 收集需要提取的变量（从condition中）（统一替换-为_）
            # 遍历该intent的所有trans_info，收集condition_variable
            variables_to_extract = set()
            for t_info in transition_info_list:
                if t_info.get('intent_name') == intent_name:
                    condition_var = t_info.get('condition_variable')
                    if condition_var:
                        # 统一将-替换为_
                        variables_to_extract.add(condition_var.replace('-', '_'))
            
            # 如果有parameters定义，也加入
            if parameters:
                for param in parameters:
                    param_id = param.get('id', '')
                    if param_id:
                        # 统一将-替换为_
                        variables_to_extract.add(param_id.replace('-', '_'))
            
            # **新增：加入纯condition routes的变量**
            # 如果这个intent route没有自己的condition，但有纯condition routes，将其变量加入
            if not trans_info.get('has_condition') and pure_condition_vars:
                variables_to_extract.update(pure_condition_vars)
                print(f"    📝 Including pure condition variables: {pure_condition_vars}")
            
            # 2.3 如果有需要提取的变量，创建参数提取节点
            # 根据 ner_version 选择 Semantic 或 LLM 模式
            if variables_to_extract:
                # =====================================
                # Semantic NER 版本：使用 SemanticJudgment + Code 节点
                # =====================================
                if self.ner_version == 'semantic' and parameters:
                    logger.debug(f"  🔄 [Pattern2] 使用 Semantic NER 版本为意图 {intent_name} 生成参数提取节点")
                    
                    ner_gen = self._init_ner_generator()
                    if isinstance(ner_gen, SemanticNERNodeGenerator):
                        try:
                            # 构建 trans_info_list（该意图的所有条件分支）
                            intent_trans_info_list = [t for t in transition_info_list if t.get('intent_name') == intent_name]
                            
                            semantic_nodes, semantic_branches = ner_gen.generate_parameter_nodes(
                                page_id=page_id,
                                intent_name=intent_name,
                                condition_id=f"is_{intent_name}",  # 使用 intent_check 的 condition_id
                                trans_info_list=intent_trans_info_list,
                                parameters=parameters,
                                capture_variable="last_user_response",
                                gen_unique_node_name=gen_unique_node_name,
                                gen_variable_name=gen_variable_name,
                                lang=self.lang
                            )
                            
                            if semantic_nodes:
                                # 找到 SemanticJudgment 节点和 Code 节点
                                ner_semantic_node = next((n for n in semantic_nodes if n.get('type') == 'semanticJudgment'), None)
                                ner_code_nodes = [n for n in semantic_nodes if n.get('type') == 'code']
                                
                                if ner_semantic_node:
                                    intent_chain["ner_semantic"] = ner_semantic_node
                                    intent_chain["nodes"].append(ner_semantic_node)
                                    intent_chain["ner_code_nodes"] = ner_code_nodes
                                    intent_chain["nodes"].extend(ner_code_nodes)
                                    intent_chain["ner_branches"] = semantic_branches
                                    
                                    # 设置intent_check的yes分支指向 NER Semantic 节点
                                    ner_semantic_name = ner_semantic_node.get('name')
                                    intent_check_node["if_else_conditions"][0]["target_node"] = ner_semantic_name
                                    
                                    logger.debug(f"    ✅ Semantic NER 生成了 {len(semantic_nodes)} 个节点")
                                    # 跳过 LLM+Code 生成，继续处理 param_condition
                        except Exception as e:
                            logger.warning(f"    ⚠️ Semantic NER 生成失败，回退到 LLM 版本: {e}")
                            import traceback
                            traceback.print_exc()
                            # 继续使用 LLM 版本
                
                # =====================================
                # LLM NER 版本：使用 LLM + Code 节点（默认）
                # =====================================
                if "ner_semantic" not in intent_chain:
                    # 创建 LLM 节点（提取变量）
                    llm_variable = gen_variable_name()
                    llm_node_name = gen_unique_node_name(f'llm_extract_{intent_name}', page_id)

                    # 构建输出模板（包含所有需要提取的变量）
                    output_template = "{\n"
                    for var_name in sorted(variables_to_extract):
                        output_template += f'  "{var_name}": "",\n'
                    output_template += "}"
                    
                    # 构建prompt（收集所有参数的allowed values，并附加 synonyms）
                    hint_lines = []
                    
                    # 1. 从parameters定义中获取候选值（通过entityTypeDisplayName）
                    if parameters:
                        for param in parameters:
                            param_id = param.get('id', '')
                            ent_type = param.get('entityTypeDisplayName')
                            if ent_type:
                                # 统一将-替换为_
                                param_id_normalized = param_id.replace('-', '_')
                                # 获取候选值（原有逻辑）
                                entity_key = f"@{ent_type}" if not ent_type.startswith('@') else ent_type
                                lang_vals = self.entity_candidates.get(entity_key, {}).get(self.lang, [])
                                
                                if lang_vals:
                                    # 保持原格式
                                    hint_line = f'- {param_id_normalized}: allowed values ({self.lang}) = ' + ", ".join(lang_vals)
                                    
                                    # 尝试从 entities_with_synonyms 获取 synonyms
                                    entity_display_name = ent_type.lstrip('@')
                                    entity_data = self.entities_with_synonyms.get(entity_display_name, {}).get(self.lang, [])
                                    
                                    if entity_data:
                                        synonym_lines = []
                                        for entry in entity_data:
                                            value = entry.get('value', '')
                                            synonyms = entry.get('synonyms', [])
                                            if value and synonyms:
                                                synonyms_str = "、".join(synonyms)
                                                synonym_lines.append(f'   "{value}"(synonyms: {synonyms_str})')
                                        
                                        if synonym_lines:
                                            hint_line += '\n' + '\n'.join(synonym_lines)
                                    
                                    hint_lines.append(hint_line)
                    
                    # 2. 从condition中收集该intent的所有可能值（condition_value）
                    # **修改：同时收集纯condition routes（没有intent的routes）的condition值**
                    # **修改：支持OR条件（disjunction）的多个值**
                    condition_values_by_var = {}
                    for t_info in transition_info_list:
                        # 收集两种情况：
                        # 1. 属于这个intent的routes
                        # 2. 纯condition routes（没有intent，但有condition）
                        is_intent_route = t_info.get('intent_name') == intent_name
                        is_pure_condition = not t_info['has_intent'] and t_info.get('has_condition')
                        
                        if is_intent_route or is_pure_condition:
                            condition_var = t_info.get('condition_variable')
                            # 统一将-替换为_
                            condition_var_normalized = condition_var.replace('-', '_') if condition_var else None
                            
                            if condition_var_normalized:
                                if condition_var_normalized not in condition_values_by_var:
                                    condition_values_by_var[condition_var_normalized] = []
                                
                                # 收集单个值
                                condition_val = t_info.get('condition_value')
                                if condition_val:
                                    condition_values_by_var[condition_var_normalized].append(str(condition_val))
                                
                                # **新增：收集OR条件（disjunction）的多个值**
                                condition_vals = t_info.get('condition_values', [])
                                for cv in condition_vals:
                                    if cv:
                                        condition_values_by_var[condition_var_normalized].append(str(cv))
                    
                    # 将condition的候选值添加到hint（如果参数还没有hint，并尝试添加 synonyms）
                    existing_param_ids = {line.split(':')[0].strip('- ') for line in hint_lines if ':' in line}
                    for var_name, values in condition_values_by_var.items():
                        if var_name not in existing_param_ids and values:
                            # 去重并显示
                            unique_values = list(set(values))
                            hint_line = f'- {var_name}: allowed values ({self.lang}) = ' + ", ".join(unique_values)
                            
                            # 尝试从 entities_with_synonyms 查找 synonyms
                            # 根据变量名匹配对应的实体
                            entity_data = None
                            var_name_original = var_name.replace('_', '-')
                            
                            for try_name in [var_name, var_name_original]:
                                if try_name in self.entities_with_synonyms:
                                    entity_data = self.entities_with_synonyms[try_name].get(self.lang, [])
                                    break
                            
                            if entity_data:
                                synonym_lines = []
                                for entry in entity_data:
                                    value = entry.get('value', '')
                                    if value in unique_values:
                                        synonyms = entry.get('synonyms', [])
                                        if synonyms:
                                            synonyms_str = "、".join(synonyms)
                                            synonym_lines.append(f'   "{value}"(synonyms: {synonyms_str})')
                                
                                if synonym_lines:
                                    hint_line += '\n' + '\n'.join(synonym_lines)
                            
                            hint_lines.append(hint_line)
                    
                    hint_text = ''
                    if hint_lines:
                        hint_text = '\n##Hints (Use one of the allowed values for each parameter)\n' + "\n".join(hint_lines) + '\n'

                    # 添加user response变量到prompt
                    prompt = f'''#Role
You are an information extraction specialist. Your task is to extract parameters from the user's reply.

##User Input
{{{{last_user_response}}}}

##Output Template
{output_template}

##Instructions
Extract the required parameters from user input and return in JSON format. If a parameter is not found, use empty string.
{hint_text}'''

                    llm_node = {
                        "type": "llmVariableAssignment",
                        "name": llm_node_name,
                        "title": f"Extract Parameters for {intent_name}",
                        "prompt_template": prompt,
                        "variable_assign": llm_variable,
                        "llm_name": self.global_config.get("llmcodemodel", "azure-gpt-4o"),
                        "chat_history_flag": self.global_config.get("enable_short_memory", False),
                        "chat_history_count": self.global_config.get("short_chat_count", 5)
                    }
                    intent_chain["llm"] = llm_node
                    intent_chain["nodes"].append(llm_node)
                    
                    # 设置intent_check的yes分支指向LLM
                    intent_check_node["if_else_conditions"][0]["target_node"] = llm_node_name
                    
                    # 2.4 创建 CODE 解析节点（解析JSON到变量）
                    code_variable = gen_variable_name()
                    code_node_name = gen_unique_node_name(f'parse_{intent_name}', page_id)
                    
                    # 构建解析代码
                    sorted_vars = sorted(variables_to_extract)
                    return_dict = ",\n".join([f'        "{v}": data["{v}"] if "{v}" in data else ""' for v in sorted_vars])
                    
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
                    
                    # 输出变量列表：使用收集到的所有变量
                    output_vars = sorted(list(variables_to_extract))
                    
                    code_node = {
                        "type": "code",
                        "name": code_node_name,
                        "title": f"Parse Parameters for {intent_name}",
                        "variable_assign": code_variable,
                        "code": parse_code,
                        "outputs": output_vars,
                        "args": [llm_variable]
                    }
                    intent_chain["code"] = code_node
                    intent_chain["nodes"].append(code_node)
            
            # 2.4 创建参数路由condition节点
            param_condition_name = gen_unique_node_name(f'param_route_{intent_name}', page_id)
            param_if_else = []
            
            # 收集这个intent的所有trans_info（可能有多个参数条件）
            # **修改：同时收集纯condition routes（没有intent的routes），将它们关联到这个intent**
            branch_index = 0
            intent_default_target = None  # 记录intent route的默认target
            intent_default_route_info = None  # 记录intent默认route的完整信息（用于beforeTransition）
            
            # writed by senlin.deng 2026-01-19
            # 处理kb、semantic中Intent路由下的混合条件（可能存在parameter）
            # **新增：合并当前 intent 下的混合 AND+OR 条件**
            mixed_conditions_with_index = []
            temp_branch_index = 0
            for t_info in transition_info_list:
                is_intent_route = t_info.get('intent_name') == intent_name
                is_pure_condition = not t_info['has_intent'] and t_info.get('has_condition')
                
                if not is_intent_route and not is_pure_condition:
                    continue
                
                # intent 默认路由不生成 condition 分支
                if is_intent_route and not t_info.get('has_condition'):
                    continue
                
                temp_branch_index += 1
                if t_info.get('is_mixed_and_or') and t_info.get('mixed_and_or_condition'):
                    mixed_conditions_with_index.append((temp_branch_index, t_info['mixed_and_or_condition']))
            
            combined_mixed_code_node = None
            combined_output_var = None
            index_to_condition_value = {}
            if mixed_conditions_with_index:
                combined_mixed_code_node, combined_output_var, index_to_condition_value = generate_combined_mixed_condition_code_node(
                    mixed_conditions_with_index,
                    page_id,
                    gen_unique_node_name
                )
                intent_chain["nodes"].append(combined_mixed_code_node)
            
            for t_info in transition_info_list:
                # 处理两种情况：
                # 1. 属于这个intent的routes
                # 2. 纯condition routes（没有intent，但有condition）
                is_intent_route = t_info.get('intent_name') == intent_name
                is_pure_condition = not t_info['has_intent'] and t_info.get('has_condition')
                
                if not is_intent_route and not is_pure_condition:
                    continue
                
                # 如果是intent route本身（没有condition），记录其target作为默认路由
                if is_intent_route and not t_info.get('has_condition'):
                    if t_info.get('target_page_id') or t_info.get('target_flow_id'):
                        intent_default_target = {
                            'target_page_id': t_info.get('target_page_id'),
                            'target_flow_id': t_info.get('target_flow_id')
                        }
                        if intent_default_route_info is None:
                            intent_default_route_info = t_info
                    # 不为这个route生成condition分支，只记录默认target
                    continue
                
                mixed_code_node_name = None  # 用于存储混合条件的 code 节点名
                current_branch_index = branch_index + 1
                
                # write by senlin.deng 2026-01-19
                # 处理kb、semantic中Intent路由下的混合条件（可能存在parameter）
                # **NEW: Support mixed AND+OR conditions (highest priority)**
                if t_info.get('is_mixed_and_or') and t_info.get('mixed_and_or_condition'):
                    if combined_output_var and current_branch_index in index_to_condition_value:
                        # 使用合并的 mixed condition code 节点
                        mixed_code_node_name = combined_mixed_code_node['name'] if combined_mixed_code_node else None
                        conditions_list = [{
                            "condition_type": "variable",
                            "comparison_operator": "=",
                            "condition_value": index_to_condition_value[current_branch_index],
                            "condition_variable": combined_output_var
                        }]
                        logical_operator = "and"
                        condition_name = f"Route {intent_name}: Mixed AND+OR Condition #{branch_index}"
                    else:
                        # 兜底：生成单独的混合条件 code 节点
                        mixed_code_node, output_var = generate_mixed_condition_code_node(
                            t_info['mixed_and_or_condition'],
                            page_id,
                            gen_unique_node_name
                        )
                        intent_chain["nodes"].append(mixed_code_node)
                        mixed_code_node_name = mixed_code_node['name']
                        
                        # 条件分支使用 code 节点的输出
                        conditions_list = [{
                            "condition_type": "variable",
                            "comparison_operator": "=",
                            "condition_value": "True",
                            "condition_variable": output_var
                        }]
                        logical_operator = "and"
                        condition_name = f"Route {intent_name}: Mixed AND+OR Condition #{branch_index}"
                # **Support AND/OR multi-conditions in Pattern 2**
                elif t_info.get('and_conditions_list'):
                    and_conditions = t_info.get('and_conditions_list', [])
                    is_or = t_info.get('is_or_condition', False)
                    # Multi-condition (AND or OR)
                    conditions_list = [
                        {
                            "condition_type": "variable",
                            "comparison_operator": cond['operator'],
                            "condition_value": str(cond['value']) if cond['value'] is not None else "",
                            # writed by senlin.deng 2026-01-13
                            # 将 condition_variable 转换为小写，确保一致性
                            "condition_variable": (cond.get('variable', '') or '').replace('-', '_').lower()
                        }
                        for cond in and_conditions
                    ]
                    logical_operator = "or" if is_or else "and"
                    # Generate condition name from first condition
                    first_cond = and_conditions[0]
                    condition_value_short = str(first_cond['value'])[:20] if first_cond['value'] else ""
                    condition_value_short = condition_value_short.replace(' ', '_').replace('"', '').replace("'", '')
                    condition_name = f"Route {intent_name}: {condition_value_short}... (+ {len(and_conditions)-1} more)" if len(and_conditions) > 1 else f"Route {intent_name}: {condition_value_short}..."
                else:
                    # Single condition (original logic)
                    conditions_list = []
                    if t_info.get('condition_variable') and t_info.get('condition_operator') and t_info.get('condition_value') is not None:
                        # writed by senlin.deng 2026-01-13
                        # 统一将-替换为_，并转换为小写，确保一致性
                        cond_var_normalized = (t_info.get('condition_variable') or '').replace('-', '_').lower()
                        conditions_list.append({
                            "condition_type": "variable",
                            "comparison_operator": t_info['condition_operator'],
                            "condition_value": str(t_info['condition_value']),
                            "condition_variable": cond_var_normalized
                        })
                    logical_operator = "and"
                    condition_value_short = str(t_info.get('condition_value', ''))[:20]
                    condition_value_short = condition_value_short.replace(' ', '_').replace('"', '').replace("'", '')
                    condition_name = f"Route {intent_name}: {condition_value_short}..." if condition_value_short else f"Route {intent_name} #{branch_index}"
                
                # 生成唯一的condition_id
                branch_index += 1
                unique_condition_id = f"param_{intent_name}_{branch_index}"
                
                # 检查是否有 setParameterActions，如果有则生成 code 节点
                transition_code_node = None
                set_param_actions = t_info.get('set_parameter_actions', [])
                if set_param_actions:
                    code_node, _ = generate_setparameter_code_node(
                        set_param_actions, page_id, intent_name, gen_unique_node_name
                    )
                    if code_node:
                        intent_chain["nodes"].append(code_node)
                        transition_code_node = code_node['name']
                
                # 处理 beforeTransition.staticUserResponse
                transition_text_node_names = []
                before_transition_text_nodes = t_info.get('before_transition_text_nodes', [])
                if before_transition_text_nodes:
                    for text_node in before_transition_text_nodes:
                        intent_chain["nodes"].append(text_node)
                        transition_text_node_names.append(text_node['name'])
                
                param_if_else.append({
                    "condition_id": unique_condition_id,
                    "condition_name": condition_name,
                    "logical_operator": logical_operator if conditions_list else "other",
                    "conditions": conditions_list,
                    "condition_action": [],
                    "target_page_id": t_info['target_page_id'],
                    "target_flow_id": t_info['target_flow_id'],
                    "transition_code_node": transition_code_node,  # 记录对应的code节点
                    "transition_text_nodes": transition_text_node_names,  # 记录对应的text节点
                    "mixed_condition_code_node": mixed_code_node_name  # 新增：混合条件 code 节点
                })
            # write by senlin.deng 2026-01-20
            # 添加默认fallback（如果intent route有默认target，使用它；否则使用fallback）
            # 修复：为默认路由生成 beforeTransition 相关节点（setParameterActions + staticUserResponse）
            # 解决在直接跳转flow的节点链的情况下，不生成beforeTransition相关节点的问题
            default_transition_code_node = None
            default_transition_text_nodes = []
            if intent_default_route_info:
                set_param_actions = intent_default_route_info.get('set_parameter_actions', [])
                if set_param_actions:
                    code_node, _ = generate_setparameter_code_node(
                        set_param_actions, page_id, intent_name, gen_unique_node_name
                    )
                    if code_node:
                        intent_chain["nodes"].append(code_node)
                        default_transition_code_node = code_node['name']
                before_transition_text_nodes = intent_default_route_info.get('before_transition_text_nodes', [])
                if before_transition_text_nodes:
                    for text_node in before_transition_text_nodes:
                        intent_chain["nodes"].append(text_node)
                        default_transition_text_nodes.append(text_node['name'])

            fallback_branch = {
                "condition_id": f"param_fallback_{intent_name}",
                "condition_name": "Parameter Fallback",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": []
            }
            if intent_default_target:
                fallback_branch["target_page_id"] = intent_default_target.get('target_page_id')
                fallback_branch["target_flow_id"] = intent_default_target.get('target_flow_id')
                fallback_branch["transition_code_node"] = default_transition_code_node
                fallback_branch["transition_text_nodes"] = default_transition_text_nodes
            
            # writed by senlin.deng 2026-01-16
            # 修复intent单独抽取+多个condition分支（新情况），没有实际条件时，不生成param_condition节点
            # **修复：检查是否只有 fallback 分支（没有任何实际条件）**
            # 如果 param_if_else 为空，说明没有任何实际条件分支，只有 fallback
            # 这种情况下不应该生成无条件的条件节点，而应该让上游节点直接连接到目标 page
            if len(param_if_else) == 0 and intent_default_target:
                # 不生成 param_condition 节点，记录直接连接信息
                intent_chain["skip_param_condition"] = True
                intent_chain["direct_target"] = intent_default_target
                print(f"    ⏭️ Skipping param_condition for {intent_name}: no condition branches, direct connect to target")
                
                # 如果有 LLM/CODE 节点，最后一个节点需要直接连接到目标
                # 如果没有，intent_check 的 yes 分支需要直接连接到目标
                if not variables_to_extract:
                    # 没有变量提取，intent_check 的 yes 分支直接连接到目标
                    intent_check_node["if_else_conditions"][0]["target_page_id"] = intent_default_target.get('target_page_id')
                    intent_check_node["if_else_conditions"][0]["target_flow_id"] = intent_default_target.get('target_flow_id')
                    intent_check_node["if_else_conditions"][0]["transition_code_node"] = default_transition_code_node
                    intent_check_node["if_else_conditions"][0]["transition_text_nodes"] = default_transition_text_nodes
                    intent_check_node["if_else_conditions"][0]["target_node"] = None
                else:
                    # 有变量提取，CODE 节点后需要直接连接到目标
                    # 在 CODE 节点中添加 _direct_target 字段，边生成时检查此字段
                    intent_chain["code_direct_target"] = intent_default_target
                    
                    # write by senlin.deng 2026-02-04: 同时处理 LLM 版本和 Semantic NER 版本
                    if "code" in intent_chain:
                        # LLM 版本：设置 CODE 节点的 _direct_target
                        intent_chain["code"]["_direct_target"] = intent_default_target
                        intent_chain["code"]["_direct_transition_code_node"] = default_transition_code_node
                        intent_chain["code"]["_direct_transition_text_nodes"] = default_transition_text_nodes
                        print(f"    📍 Marked CODE node {intent_chain['code']['name']} with direct target: {intent_default_target}")
                    
                    if "ner_semantic" in intent_chain and "ner_branches" in intent_chain:
                        # Semantic NER 版本：为所有 NER Code 节点的分支设置 _direct_target
                        direct_target_with_transition = {
                            "target_page_id": intent_default_target.get('target_page_id'),
                            "target_flow_id": intent_default_target.get('target_flow_id'),
                            "transition_code_node": default_transition_code_node,
                            "transition_text_nodes": default_transition_text_nodes
                        }
                        ner_branches = intent_chain.get("ner_branches", [])
                        for branch in ner_branches:
                            if branch.get('_ner_branch'):
                                branch['_direct_target'] = direct_target_with_transition
                        # 同时更新 ner_semantic 节点内部的 _condition_branches
                        ner_semantic_node = intent_chain.get("ner_semantic")
                        if ner_semantic_node:
                            internal_branches = ner_semantic_node.get('_condition_branches', [])
                            for branch in internal_branches:
                                branch['_direct_target'] = direct_target_with_transition
                            logger.debug(f"    📍 Marked NER Code nodes with direct target: {intent_default_target}")
            else:
                # 正常情况：有实际的条件分支或没有默认目标
                param_if_else.append(fallback_branch)
                
                param_condition_node = {
                    "type": "condition",
                    "name": param_condition_name,
                    "title": f"Parameter Routing for {intent_name}",
                    "if_else_conditions": param_if_else,
                    "combined_mixed_condition_code_node": combined_mixed_code_node['name'] if combined_mixed_code_node else None
                }
                intent_chain["param_condition"] = param_condition_node
                intent_chain["nodes"].append(param_condition_node)
                
                # 如果没有需要提取的变量，intent_check的yes分支应该直接指向param_condition
                if not variables_to_extract:
                    intent_check_node["if_else_conditions"][0]["target_node"] = param_condition_name
                
                # write by senlin.deng 2026-02-04: 为 Semantic NER 版本的 Code 节点设置后续连接
                # NER Code 节点需要连接到 param_condition 节点
                if "ner_semantic" in intent_chain and "ner_branches" in intent_chain:
                    ner_branches = intent_chain.get("ner_branches", [])
                    for branch in ner_branches:
                        if branch.get('_ner_branch'):
                            branch['_next_condition_node'] = param_condition_name
                    # 同时更新 ner_semantic 节点内部的 _condition_branches
                    ner_semantic_node = intent_chain.get("ner_semantic")
                    if ner_semantic_node:
                        internal_branches = ner_semantic_node.get('_condition_branches', [])
                        for branch in internal_branches:
                            branch['_next_condition_node'] = param_condition_name
                        logger.debug(f"    📍 Marked NER Code nodes with next condition: {param_condition_name}")
            
            intent_chains.append(intent_chain)
        
        # 3. 链接所有intent_chains（形成链式结构）
        # intent_check1 → (yes: LLM1→CODE1→param_condition1, no: intent_check2)
        # intent_check2 → (yes: LLM2→CODE2→param_condition2, no: fallback)
        for i in range(len(intent_chains)):
            current_chain = intent_chains[i]
            
            # 设置"否"分支的目标
            if i < len(intent_chains) - 1:
                # 不是最后一个，指向下一个intent_check
                next_chain = intent_chains[i + 1]
                next_intent_check_name = next_chain["intent_check"]["name"]
                current_chain["intent_check"]["if_else_conditions"][1]["target_node"] = next_intent_check_name
            else:
                # 最后一个，指向fallback
                current_chain["intent_check"]["if_else_conditions"][1]["target_node"] = fallback_node_name
            
            # 将所有节点添加到nodes列表
            for node in current_chain["nodes"]:
                nodes.append(node)
        
        # 4. 记录condition_branches和第一个intent_check
        first_intent_check = intent_chains[0]["intent_check"]["name"] if intent_chains else None
        
        for trans_info in transition_info_list:
            if not trans_info['has_intent']:
                continue
            
            intent_name = trans_info['intent_name']
            
            branch = {
                "condition_id": f"intent_{intent_name}",
                "condition_name": f"Intent_{intent_name}",
                "logical_operator": "and",
                "conditions": [{
                    "condition_type": "variable",
                    "comparison_operator": "=",
                    "condition_value": intent_name,
                    "condition_variable": intent_variable
                }],
                "condition_action": [],
                "has_parameters": trans_info['has_parameters'],
                "parameters": trans_info['parameters'],
                "target_page_id": trans_info['target_page_id'],
                "target_flow_id": trans_info['target_flow_id'],
                "target_node": first_intent_check,  # 指向第一个intent_check
                "is_chain_start": True  # 标记为链式结构的入口
            }
            condition_branches.append(branch)

        return nodes, condition_branches

    def _generate_jump_nodes_for_page_transitions(self, transition_info_list: List[Dict[str, Any]], page_id: str, all_nodes: List[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        为页面层级的 transitionEvents 生成 jump 节点（当有 targetFlowId 且无 targetPageId 时）

        Args:
            transition_info_list: transition事件信息列表（应该已经过滤，只包含有 targetFlowId 且无 targetPageId 的）
            page_id: 页面ID（用于生成唯一节点名）
            all_nodes: 所有节点列表（用于检查唯一性）

        Returns:
            (jump_nodes列表, jump_node_names列表)
        """
        jump_nodes = []
        jump_node_names = []

        for trans_info in transition_info_list:
            target_page_id = trans_info.get('target_page_id')
            target_flow_id = trans_info.get('target_flow_id')
            page_display_name = trans_info.get('page_display_name', '')

            # 只有当有 targetFlowId 且无 targetPageId 时才生成 jump 节点
            if target_flow_id and not target_page_id:
                # 生成 jump 节点名称：直接使用 jump_to_{page_id前8位}，不添加计数器
                jump_node_name = f"jump_to_{page_id[:8]}"
                
                # 确保节点名称唯一（检查 jump_nodes 和 all_nodes）
                existing_names = {n.get('name') for n in jump_nodes}
                if all_nodes:
                    existing_names.update({n.get('name') for n in all_nodes})
                
                # 如果名称已存在，检查是否是相同的 jump 节点（相同的 jump_flow_uuid 和 page_display_name）
                existing_jump_node = None
                if jump_node_name in existing_names:
                    # 检查 all_nodes 中是否存在相同名称的 jump 节点
                    if all_nodes:
                        for node in all_nodes:
                            if node.get('type') == 'jump' and node.get('name') == jump_node_name:
                                jump_flow_uuid = node.get('jump_flow_uuid')
                                jump_flow_name = node.get('jump_flow_name', '')
                                trans_info_existing = node.get('transition_info', {})
                                jump_page_display_name = trans_info_existing.get('page_display_name', '') if trans_info_existing else ''
                                
                                # 检查 flow_id 和 page_display_name 是否匹配
                                if jump_flow_uuid == target_flow_id:
                                    if page_display_name:
                                        if jump_page_display_name == page_display_name or jump_flow_name == page_display_name:
                                            existing_jump_node = node
                                            break
                                    else:
                                        # 如果没有 page_display_name，匹配第一个
                                        existing_jump_node = node
                                        break
                
                if existing_jump_node:
                    # 复用已存在的节点
                    jump_node = existing_jump_node
                    print(f"      ✅ Reusing existing jump node: {jump_node_name} -> {target_flow_id[:8]}... (page: {page_display_name or page_id[:8]})")
                else:
                    # 创建新的 jump 节点
                    # title 使用 page_display_name 或 flow_id
                    if page_display_name:
                        title = f"jump_to_{page_display_name}"
                    else:
                        title = f"jump_to_{target_flow_id[:8]}"

                    jump_node = {
                        "type": "jump",
                        "name": jump_node_name,
                        "title": title,  # title 使用 displayName，name 用于唯一性
                        "jump_type": "flow",
                        "jump_robot_id": "",
                        "jump_robot_name": "",
                        "jump_carry_history_number": 5,
                        "jump_flow_name": page_display_name if page_display_name else "",
                        "jump_flow_uuid": target_flow_id,  # 保存 target_flow_id
                        "jump_carry_userinput": True,
                        "transition_info": trans_info  # 保存关联信息
                    }
                    print(f"      ✅ Created jump node: {jump_node_name} -> {target_flow_id[:8]}... (title: {title})")
                
                # 只有当节点不在 jump_nodes 中时才添加（避免重复）
                if jump_node_name not in jump_node_names:
                    jump_nodes.append(jump_node)
                    jump_node_names.append(jump_node_name)

        return jump_nodes, jump_node_names

    def _generate_global_transition_nodes(self, page: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        处理全局transitionEvent（triggerIntentId为null，condition为GLOBAL true）
        情况1：页面本身有全局transition（包含setParameterActions和targetFlowId）
        情况2：页面有全局transition指向目标页面，目标页面有全局transition

        Args:
            page: 页面配置字典

        Returns:
            (nodes列表, edges列表)
        """
        nodes = []
        edges = []

        # 处理两种格式：转换后的格式（key/value）和原始格式（pageId/transitionEvents）
        if 'key' in page and 'value' in page:
            # 转换后的格式
            page_id = page.get('key', '')
            # 支持两种格式：'value.transitionEvents' (转换后的格式) 和 'transitionEvents' (原始格式)
            transition_events = page.get('value', {}).get('transitionEvents', [])
            page_name = page.get('name', 'Unknown')
        else:
            # 原始格式
            page_id = page.get('pageId', '')
            transition_events = page.get('transitionEvents', [])
            page_name = page.get('displayName', 'Unknown')

        for event in transition_events:
            trigger_intent_id = event.get('triggerIntentId')
            condition = event.get('condition', {})
            handler = event.get('transitionEventHandler', {})
            condition_string = event.get('conditionString', '')
            is_literal_condition = condition_string.strip().lower() in ('true', 'false') if condition_string else False

            # 检查是否满足全局transition条件
            is_global_transition = (
                trigger_intent_id is None and
                condition.get('comparator') == 'GLOBAL' and
                condition.get('rhs', {}).get('value') == 'true' and
                not is_literal_condition
            )

            if not is_global_transition:
                continue

            # 获取beforeTransition中的setParameterActions
            before_transition = handler.get('beforeTransition', {})
            set_parameter_actions = before_transition.get('setParameterActions', [])

            # 获取目标
            target_page_id = handler.get('targetPageId')
            target_flow_id = handler.get('targetFlowId')

            # 情况1：页面本身有targetFlowId（可能有或没有setParameterActions）
            if target_flow_id and not target_page_id:
                if set_parameter_actions:
                    print(f"  - Processing global transition with parameters and jump to flow: {target_flow_id[:8]}...")
                    
                    # 直接生成jump节点，参数通过set_parameter_actions传递，不需要单独的code节点
                    jump_node_name = self._generate_unique_node_name('global_jump', page_id)
                    # name 和 title 设置为相同值
                    title = jump_node_name
                    jump_node = {
                        "type": "jump",
                        "name": jump_node_name,
                        "title": title,  # name 和 title 相同
                        "jump_type": "flow",
                        "jump_robot_id": "",
                        "jump_robot_name": "",
                        "jump_carry_history_number": 5,
                        "jump_flow_name": "",
                        "jump_flow_uuid": target_flow_id,  # 保存 target_flow_id
                        "jump_carry_userinput": True,
                        "transition_info": {
                            "target_page_id": target_page_id,
                            "target_flow_id": target_flow_id,
                            "set_parameter_actions": set_parameter_actions
                        },
                        "_needs_connection": True  # 标记需要从 page 的最后一个节点连接
                    }
                    nodes.append(jump_node)
                    print(f"    - Generated jump node to flow: {target_flow_id[:8]}... (with parameters in set_parameter_actions)")
                    print(f"    - Note: Edge will be added from page's last node")
                else:
                    # 没有 setParameterActions，只有 targetFlowId
                    print(f"  - Processing global transition without parameters, jump to flow: {target_flow_id[:8]}...")
                    
                    # 生成jump节点
                    jump_node_name = self._generate_unique_node_name('global_jump', page_id)
                    # name 和 title 设置为相同值
                    title = jump_node_name
                    jump_node = {
                        "type": "jump",
                        "name": jump_node_name,
                        "title": title,  # name 和 title 相同
                        "jump_type": "flow",
                        "jump_robot_id": "",
                        "jump_robot_name": "",
                        "jump_carry_history_number": 5,
                        "jump_flow_name": "",
                        "jump_flow_uuid": target_flow_id,  # 保存 target_flow_id
                        "jump_carry_userinput": True,
                        "transition_info": {
                            "target_page_id": target_page_id,
                            "target_flow_id": target_flow_id,
                            "set_parameter_actions": []
                        },
                        "_needs_connection": True  # 标记需要从 page 的最后一个节点连接
                    }
                    nodes.append(jump_node)
                    print(f"    - Generated jump node to flow: {target_flow_id[:8]}...")
                    print(f"    - Note: Edge will be added from page's last node")

            # 情况2：页面有全局transition指向目标页面，需要检查目标页面
            elif target_page_id and not target_flow_id:
                # 尝试获取目标页面的信息
                target_page_info = self._find_page_by_id(target_page_id)
                if target_page_info:
                    # 检查目标页面是否有全局transition
                    jump_info = self._check_target_page_global_transition(target_page_info)
                    if jump_info:
                        print(f"  - Processing indirect global transition to page {target_page_id[:8]}...")

                        # 直接生成jump节点，参数通过set_parameter_actions传递，不需要单独的code节点
                        jump_node_name = self._generate_unique_node_name('indirect_jump', page_id)
                        # name 和 title 设置为相同值
                        title = jump_node_name
                        jump_node = {
                            "type": "jump",
                            "name": jump_node_name,
                            "title": title,  # name 和 title 相同
                            "jump_type": "flow",
                            "jump_robot_id": "",
                            "jump_robot_name": "",
                            "jump_carry_history_number": 5,
                            "jump_flow_name": "",
                            "jump_flow_uuid": jump_info['target_flow_id'],
                            "jump_carry_userinput": True,
                            "transition_info": {
                                "target_page_id": None,
                                "target_flow_id": jump_info['target_flow_id'],
                                "set_parameter_actions": jump_info['set_parameter_actions'],
                                "source_page_id": page_id,
                                "target_page_id": target_page_id
                            },
                            "_needs_connection": True  # 标记需要从 page 的最后一个节点连接
                        }
                        nodes.append(jump_node)
                        print(f"    - Generated jump node to flow: {jump_info['target_flow_id'][:8]}... (with parameters in set_parameter_actions)")
                        print(f"    - Note: Edge will be added from page's last node")

        return nodes, edges

    def _find_page_by_id(self, page_id: str) -> Dict[str, Any]:
        """根据pageId查找页面信息"""
        try:
            with open('output/step1_processed/fulfillments_en.json', 'r', encoding='utf-8') as f:
                fulfillments_data = json.load(f)

            for page in fulfillments_data.get('pages', []):
                if page.get('pageId') == page_id:
                    return page
        except Exception as e:
            print(f"Error finding page {page_id}: {e}")

        return None

    def _check_target_page_global_transition(self, page: Dict[str, Any]) -> Dict[str, Any]:
        """
        检查目标页面是否有全局transition（包含setParameterActions和targetFlowId）

        Returns:
            包含目标flow信息的字典，如果没有则返回None
        """
        transition_events = page.get('transitionEvents', [])

        for event in transition_events:
            trigger_intent_id = event.get('triggerIntentId')
            condition = event.get('condition', {})
            handler = event.get('transitionEventHandler', {})

            # 检查是否满足全局transition条件
            is_global_transition = (
                trigger_intent_id is None and
                condition.get('comparator') == 'GLOBAL' and
                condition.get('rhs', {}).get('value') == 'true'
            )

            if not is_global_transition:
                continue

            # 获取beforeTransition中的setParameterActions
            before_transition = handler.get('beforeTransition', {})
            set_parameter_actions = before_transition.get('setParameterActions', [])

            # 获取目标flow
            target_page_id = handler.get('targetPageId')
            target_flow_id = handler.get('targetFlowId')

            # 必须同时有setParameterActions和targetFlowId（且无targetPageId）
            if set_parameter_actions and target_flow_id and not target_page_id:
                return {
                    'target_flow_id': target_flow_id,
                    'set_parameter_actions': set_parameter_actions,
                    'target_page_id': target_page_id
                }

        return None

    def _check_target_page_has_flow_transition(self, target_page_id: str, page_id_map: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if target page is a pure relay page (only has flow transition, no content to display)
        Returns flow info if the page is a relay page, None otherwise
        
        A page is considered a relay page if:
        - It has a transition to another flow (with condition=true)
        - It has NO form, eventHandlers, or entryFulfillment (responses)
        
        Args:
            target_page_id: Target page ID
            page_id_map: Mapping of page_id to page data
            
        Returns:
            Dict containing target flow info if it's a relay page, None otherwise
        """
        if not target_page_id:
            return None
        
        # Find page data from page_id_map
        page_data = page_id_map.get(target_page_id)
        if not page_data:
            return None
        
        # Handle both formats: converted format (key/value) and original format (pageId/transitionEvents)
        if 'value' in page_data:
            page_value = page_data.get('value', {})
            transition_events = page_value.get('transitionEvents', [])
            # Check if page has content to display (form, eventHandlers, or entryFulfillment)
            # If it has content, it should NOT be skipped, user needs to see the responses
            has_content = bool(page_value.get('form', {})) or \
                         bool(page_value.get('eventHandlers', [])) or \
                         bool(page_value.get('entryFulfillment', {}).get('messages', []))
        else:
            transition_events = page_data.get('transitionEvents', [])
            has_content = bool(page_data.get('form', {})) or \
                         bool(page_data.get('eventHandlers', [])) or \
                         bool(page_data.get('entryFulfillment', {}).get('messages', []))
        
        # If page has any content (form, eventHandlers, or responses), it should NOT be treated as a relay page
        # User needs to see the content before transitioning
        if has_content:
            return None
        
        # 检查 transitionEvents 是否只有跳转到 flow 的逻辑
        for event in transition_events:
            handler = event.get('transitionEventHandler', {})
            target_flow_id = handler.get('targetFlowId')
            target_page_id_in_event = handler.get('targetPageId')
            condition = event.get('condition', {})
            condition_string = event.get('conditionString', '')
            is_literal_condition = condition_string.strip().lower() in ('true', 'false') if condition_string else False
            
            # 检查是否有 targetFlowId 且无 targetPageId
            if target_flow_id and not target_page_id_in_event:
                # 检查 condition 是否为 true
                restriction = condition.get('restriction', condition)
                condition_rhs = restriction.get('rhs', {}) if isinstance(restriction, dict) else {}
                condition_value = condition_rhs.get('value') if isinstance(condition_rhs, dict) else None
                comparator = restriction.get('comparator', "") if isinstance(restriction, dict) else condition.get('comparator', "")
                
                # 判断是否为始终为 true 的条件（仅 GLOBAL true 或空条件）
                # Literal conditionString should not bypass condition nodes.
                is_always_true = self._is_always_true_condition(condition) and not is_literal_condition
                
                if is_always_true:
                    # 获取 beforeTransition 中的 setParameterActions
                    before_transition = handler.get('beforeTransition', {})
                    set_parameter_actions = before_transition.get('setParameterActions', [])
                    
                    return {
                        'target_flow_id': target_flow_id,
                        'set_parameter_actions': set_parameter_actions,
                        'is_always_true': True
                    }
        
        return None

    def _find_jump_node_for_target(self, target_flow_id: str, target_page_id: str, jump_nodes: List[Dict[str, Any]], page_display_name: str = None, all_nodes: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        查找与目标 flow 对应的 jump 节点（仅当有 targetFlowId 且无 targetPageId 时）
        如果有多个 jump 节点指向同一个 flow_id，优先返回与 page_display_name 匹配的
        同时检查 all_nodes 中是否已存在匹配的 jump 节点

        Args:
            target_flow_id: 目标flow ID
            target_page_id: 目标page ID
            jump_nodes: jump节点列表
            page_display_name: page 的 displayName（可选，用于精确匹配）
            all_nodes: 所有节点列表（可选，用于检查已存在的节点）

        Returns:
            匹配的jump节点或None
        """
        if not target_flow_id or target_page_id:
            return None

        # 首先检查 all_nodes 中是否已存在匹配的 jump 节点
        if all_nodes:
            for node in all_nodes:
                if node.get('type') == 'jump':
                    jump_flow_uuid = node.get('jump_flow_uuid')
                    trans_info = node.get('transition_info', {})
                    jump_flow_id = trans_info.get('target_flow_id') if trans_info else None
                    jump_page_id = trans_info.get('target_page_id') if trans_info else None
                    jump_page_display_name = trans_info.get('page_display_name', '') if trans_info else ''
                    jump_flow_name = node.get('jump_flow_name', '')
                    
                    # 检查 flow_id 匹配且无 target_page_id
                    if (jump_flow_uuid == target_flow_id or jump_flow_id == target_flow_id) and not jump_page_id:
                        # 如果有 page_display_name，优先匹配
                        if page_display_name:
                            if jump_page_display_name == page_display_name or jump_flow_name == page_display_name:
                                return node
                        else:
                            # 如果没有 page_display_name，返回第一个匹配的
                            return node

        # 然后检查 jump_nodes 中是否已存在匹配的 jump 节点
        # 如果有 page_display_name，优先查找匹配的 jump 节点
        if page_display_name:
            for jump_node in jump_nodes:
                trans_info = jump_node.get('transition_info', {})
                jump_flow_id = trans_info.get('target_flow_id')
                jump_page_id = trans_info.get('target_page_id')
                jump_page_display_name = trans_info.get('page_display_name', '')
                
                # 检查 flow_id 匹配且无 target_page_id
                if jump_flow_id == target_flow_id and not jump_page_id:
                    # 如果 page_display_name 匹配，优先返回
                    if jump_page_display_name == page_display_name:
                        return jump_node
                    # 或者检查 jump_flow_name 是否匹配
                    if jump_node.get('jump_flow_name') == page_display_name:
                        return jump_node

        # 如果没有 page_display_name 或没有找到匹配的，返回第一个匹配的
        for jump_node in jump_nodes:
            trans_info = jump_node.get('transition_info', {})
            if trans_info.get('target_flow_id') == target_flow_id and not trans_info.get('target_page_id'):
                return jump_node
        return None

    # write by senlin.deng 2026-01-18
    # 处理路由组，使得生成节点与flowid关联
    def _generate_route_groups_nodes(
        self,
        page: Dict[str, Any],
        page_id: str,
        lang: str = 'en',
        jump_nodes: List[Dict[str, Any]] = None,
        page_id_map: Dict[str, Any] = None,
        all_nodes: List[Dict[str, Any]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
        """
        处理 routeGroupsTransitionEvents，生成 Route Groups Intent Recognition 节点和相关边
        
        逻辑说明：
        1. routeGroupsTransitionEvents 包含 Intent 路由和纯条件路由
        2. 生成的节点结构：
           - Route Groups Intent Recognition (Semantic) 节点
           - 每个 Intent 的参数提取节点（如果有参数）
           - 纯条件路由节点（Condition Routing）
           - Jump_to_main_agent 节点（用于 Other 分支）
           - 为有 targetFlowId 的路由生成 jump_to_flow 节点
        
        Args:
            page: page配置字典
            page_id: page ID
            lang: 语言代码
            jump_nodes: 已有的 jump 节点列表
            page_id_map: page ID 到 page 配置的映射
            all_nodes: 所有已生成的节点列表
            
        Returns:
            (nodes, edges, route_groups_entry_node_name)
        """
        nodes = []
        edges = []
        route_groups_entry_node_name = None
        
        # 初始化 jump_nodes 列表（如果未提供）
        if jump_nodes is None:
            jump_nodes = []
        
        # 支持两种格式
        page_value = page.get('value', {}) if 'value' in page else page
        route_groups_events = page_value.get('routeGroupsTransitionEvents', [])
        
        if not route_groups_events:
            return nodes, edges, route_groups_entry_node_name
        
        logger.info(f"  📋 Processing routeGroupsTransitionEvents: {len(route_groups_events)} events")
        
        # 1. 分类 routeGroupsTransitionEvents：Intent 路由 vs 纯条件路由
        intent_routes = []
        pure_condition_routes = []
        
        for event in route_groups_events:
            trigger_intent_id = event.get('triggerIntentId')
            condition = event.get('condition', {})
            handler = event.get('transitionEventHandler', {})
            
            if trigger_intent_id:
                # Intent 路由
                intent_name = self.intents_mapping.get(trigger_intent_id, trigger_intent_id)
                before_transition = handler.get('beforeTransition', {})
                set_parameter_actions = before_transition.get('setParameterActions', [])
                
                # 解析 beforeTransition 中的 staticUserResponse
                event_name = event.get('name', f"route_group_{len(intent_routes)}")
                from step2.page_processor import parse_before_transition_responses
                before_transition_text_nodes = parse_before_transition_responses(
                    before_transition, lang, self._generate_unique_node_name, event_name
                )
                
                # 检查该 intent 是否有 parameters
                parameters = self.intent_parameters_map.get(trigger_intent_id, [])
                
                intent_routes.append({
                    'intent_id': trigger_intent_id,
                    'intent_name': intent_name,
                    'target_page_id': handler.get('targetPageId'),
                    'target_flow_id': handler.get('targetFlowId'),
                    'set_parameter_actions': set_parameter_actions,
                    'before_transition_text_nodes': before_transition_text_nodes,
                    'parameters': parameters,
                    'has_parameters': len(parameters) > 0,
                    'event': event
                })
            else:
                # 纯条件路由
                pure_condition_routes.append({
                    'condition': condition,
                    'condition_string': event.get('conditionString', ''),
                    'target_page_id': handler.get('targetPageId'),
                    'target_flow_id': handler.get('targetFlowId'),
                    'before_transition': handler.get('beforeTransition', {}),
                    'event': event
                })
        
        logger.info(f"    - Intent routes: {len(intent_routes)}, Pure condition routes: {len(pure_condition_routes)}")
        
        # write by senlin.deng 2026-01-18
        # 修复路由组中未生成jump_to_flow节点的问题
        # 1.1 为有 targetFlowId 的路由生成 jump_to_flow 节点
        generated_jump_nodes = []
        flow_id_to_jump_node = {}  # targetFlowId -> jump_node_name 映射
        
        # 收集所有需要生成 jump 节点的 targetFlowId
        all_target_flow_ids = set()
        for route in intent_routes:
            target_flow_id = route.get('target_flow_id')
            if target_flow_id:
                all_target_flow_ids.add(target_flow_id)
        for route in pure_condition_routes:
            target_flow_id = route.get('target_flow_id')
            if target_flow_id:
                all_target_flow_ids.add(target_flow_id)
        
        # 为每个 targetFlowId 生成 jump 节点
        existing_jump_uuids = set()
        if jump_nodes:
            existing_jump_uuids = {n.get('jump_flow_uuid') for n in jump_nodes if n.get('type') == 'jump'}
        if all_nodes:
            existing_jump_uuids.update({n.get('jump_flow_uuid') for n in all_nodes if n.get('type') == 'jump'})
        
        for target_flow_id in all_target_flow_ids:
            if target_flow_id in existing_jump_uuids:
                # 已存在的 jump 节点，找到它
                for node in (jump_nodes or []) + (all_nodes or []):
                    if node.get('type') == 'jump' and node.get('jump_flow_uuid') == target_flow_id:
                        flow_id_to_jump_node[target_flow_id] = node.get('name')
                        break
                continue
            
            # 生成新的 jump 节点
            import re
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', target_flow_id[:16])
            safe_name = re.sub(r'_+', '_', safe_name).strip('_')
            jump_node_name = self._generate_unique_node_name(f'jump_to_flow_{safe_name}', page_id)
            
            jump_node = {
                "type": "jump",
                "name": jump_node_name,
                "title": f"Jump to Flow ({target_flow_id[:8]}...)",
                "jump_type": "flow",
                "jump_robot_id": "",
                "jump_robot_name": "",
                "jump_carry_history_number": 5,
                "jump_flow_name": "",
                "jump_flow_uuid": target_flow_id,
                "jump_carry_userinput": True
            }
            generated_jump_nodes.append(jump_node)
            nodes.append(jump_node)
            flow_id_to_jump_node[target_flow_id] = jump_node_name
            logger.info(f"    ✅ Generated jump node: {jump_node_name} for targetFlowId: {target_flow_id[:8]}...")
        
        # 1.2 生成 Jump_to_main_agent 节点（用于 Other 分支的 fallback）
        jump_to_main_agent_name = self._generate_unique_node_name('jump_to_main_agent', page_id)
        jump_to_main_agent_node = {
            "type": "jump",
            "name": jump_to_main_agent_name,
            "title": "Jump to Main Agent",
            "jump_type": "robot_direct",
            "jump_robot_id": None,
            "jump_robot_name": "",
            "jump_carry_history_number": 5,
            "jump_carry_histories": True,
            "jump_flow_name": "",
            "jump_flow_uuid": "",  # 无需指定下一个要跳转的 flow
            "jump_carry_userinput": True
        }
        nodes.append(jump_to_main_agent_node)
        logger.info(f"    ✅ Generated Jump_to_main_agent node: {jump_to_main_agent_name}")
        
        # 2. 生成 Route Groups Intent Recognition (Semantic) 节点
        if intent_routes:
            semantic_node_name = self._generate_unique_node_name('route_groups_semantic', page_id)
            semantic_conditions = []
            condition_id_to_intent = {}  # condition_id -> intent_route info
            
            for idx, route in enumerate(intent_routes):
                intent_name = route['intent_name']
                condition_id = str(uuid.uuid4())
                condition_id_to_intent[condition_id] = route
                
                # 获取该 intent 的 training phrases 作为 positive examples
                positive_examples = []
                intent_id = route['intent_id']
                
                # 尝试通过 intent_id 和 intent_name 获取训练短语
                phrases_data = self.intents_training_phrases.get(intent_id, [])
                if not phrases_data:
                    phrases_data = self.intents_training_phrases.get(intent_name, [])
                
                # intents_training_phrases 的格式是 intent_id/name -> list of phrases
                phrases = phrases_data if isinstance(phrases_data, list) else []
                for phrase in phrases[:10]:  # 最多取10个
                    positive_examples.append({
                        "id": str(uuid.uuid4()),
                        "question": phrase
                    })
                
                semantic_condition = {
                    "condition_id": condition_id,
                    "name": f" {intent_name}",  # 前面有空格是为了和 demo 保持一致
                    "desc": "",
                    "refer_questions": [{"id": str(uuid.uuid4()), "question": ""}],
                    "positive_examples": positive_examples if positive_examples else [{"id": str(uuid.uuid4()), "question": ""}],
                    "negative_examples": [{"id": str(uuid.uuid4()), "question": ""}],
                    "condition_config": {
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
                    }
                }
                semantic_conditions.append(semantic_condition)
            
            # Default condition (Other/Fallback)
            default_condition_id = str(uuid.uuid4())
            default_condition = {
                "condition_id": default_condition_id,
                "name": "Other",
                "desc": "",
                "refer_questions": [],
                "condition_config": {
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
                }
            }
            
            semantic_node = {
                "type": "semanticJudgment",
                "name": semantic_node_name,
                "title": "Route Groups Intent Recognition (Semantic)",
                "config": {
                    "semantic_conditions": semantic_conditions,
                    "default_condition": default_condition,
                    "global_config": {
                        "is_chatflow": True,
                        "confidence": self.global_config.get("semantic_confidence", 50),
                        "is_start_intent": 0,
                        "embedding_model_name": "bge-m3",
                        "embedding_rerank_enable": True,
                        "embedding_rerank_model_name": "bge-reranker-v2-m3",
                        "embedding_rerank_confidence": 0,
                        "embedding_llm_enable": False,
                        "allow_update_embedding": True,
                        "embedding_confidence": 0,
                        "embedding_llm_model_name": self.global_config.get('llmcodemodel', 'qwen3-30b-a3b'),
                        "embedding_llm_prompt": "",
                        "embedding_llm_return_count": 0,
                        "embedding_language": lang
                    },
                    "title": "Route Groups Intent Recognition (Semantic)"
                }
            }
            nodes.append(semantic_node)
            route_groups_entry_node_name = semantic_node_name
            
            # 3. 为每个 Intent 路由生成后续节点
            # 存储内部分支信息用于边生成
            semantic_branches = []
            
            for condition_id, route in condition_id_to_intent.items():
                intent_name = route['intent_name']
                target_page_id = route['target_page_id']
                target_flow_id = route['target_flow_id']
                set_param_actions = route['set_parameter_actions']
                before_transition_text_nodes = route['before_transition_text_nodes']
                
                branch_info = {
                    'condition_id': condition_id,
                    'intent_name': intent_name,
                    'target_page_id': target_page_id,
                    'target_flow_id': target_flow_id,
                    'from_semantic_node': semantic_node_name
                }
                
                # 如果有 setParameterActions，生成 code 节点
                if set_param_actions:
                    code_node_name = self._generate_unique_node_name('set_params', page_id)
                    code_node, _ = generate_setparameter_code_node(
                        set_param_actions, page_id, intent_name, self._generate_unique_node_name
                    )
                    if code_node:
                        code_node['name'] = code_node_name
                        code_node['from_semantic_condition_id'] = condition_id
                        nodes.append(code_node)
                        branch_info['code_node'] = code_node_name
                
                # 如果有 before_transition_text_nodes，添加这些节点
                if before_transition_text_nodes:
                    for text_node in before_transition_text_nodes:
                        text_node['from_semantic_condition_id'] = condition_id
                        nodes.append(text_node)
                    branch_info['text_nodes'] = [n['name'] for n in before_transition_text_nodes]
                
                semantic_branches.append(branch_info)
            
            # 添加 fallback 分支
            fallback_branch = {
                'condition_id': default_condition_id,
                'condition_name': 'Other',
                'logical_operator': 'other',
                'from_semantic_node': semantic_node_name
            }
            semantic_branches.append(fallback_branch)
            
            # 存储分支信息到语义节点
            semantic_node['_internal_branches'] = semantic_branches
            semantic_node['_condition_id_to_intent'] = condition_id_to_intent
            semantic_node['_has_pure_conditions'] = len(pure_condition_routes) > 0
            semantic_node['_flow_id_to_jump_node'] = flow_id_to_jump_node
            semantic_node['_jump_to_main_agent_node'] = jump_to_main_agent_name
        
        # 4. 如果有纯条件路由，生成 Condition Routing 节点
        if pure_condition_routes:
            condition_node_name = self._generate_unique_node_name('route_groups_condition', page_id)
            if_else_conditions = []
            
            for idx, route in enumerate(pure_condition_routes):
                condition = route['condition']
                condition_string = route['condition_string']
                target_page_id = route['target_page_id']
                target_flow_id = route['target_flow_id']
                before_transition = route['before_transition']
                
                condition_id = str(uuid.uuid4())
                
                # 解析条件
                conditions_list = []
                if condition_string:
                    # 从 conditionString 解析变量和值
                    import re
                    match = re.match(r'\$session\.params\.([a-zA-Z0-9_-]+)\s*(!=|=|>|<|>=|<=)\s*("([^"]*)"|null|true|false|(\S+))', condition_string)
                    if match:
                        var_name = match.group(1).replace('-', '_')
                        operator = match.group(2)
                        value_raw = match.group(4) if match.group(4) is not None else (match.group(5) if match.group(5) else match.group(3))
                        value = value_raw
                        
                        operator_mapping = {'=': '=', '!=': '≠', '>': '>', '<': '<', '>=': '≥', '<=': '≤'}
                        mapped_operator = operator_mapping.get(operator, operator)
                        
                        conditions_list.append({
                            "condition_type": "variable",
                            "comparison_operator": mapped_operator,
                            "condition_value": value,
                            "condition_variable": var_name
                        })
                
                # 生成 beforeTransition 相关节点
                set_param_actions = before_transition.get('setParameterActions', [])
                transition_code_node = None
                if set_param_actions:
                    code_node, _ = generate_setparameter_code_node(
                        set_param_actions, page_id, f"condition_{idx}", self._generate_unique_node_name
                    )
                    if code_node:
                        code_node['from_condition_id'] = condition_id
                        nodes.append(code_node)
                        transition_code_node = code_node['name']
                
                # 解析 beforeTransition 中的 staticUserResponse
                from step2.page_processor import parse_before_transition_responses
                transition_text_nodes = parse_before_transition_responses(
                    before_transition, lang, self._generate_unique_node_name, f"condition_{idx}"
                )
                for text_node in transition_text_nodes:
                    text_node['from_condition_id'] = condition_id
                    nodes.append(text_node)
                
                branch = {
                    "condition_id": condition_id,
                    "condition_name": f"Route Groups {condition_string}" if condition_string else f"Condition {idx}",
                    "logical_operator": "and" if conditions_list else "other",
                    "conditions": conditions_list,
                    "condition_action": [],
                    "target_page_id": target_page_id,
                    "target_flow_id": target_flow_id,
                    "transition_code_node": transition_code_node,
                    "transition_text_nodes": [n['name'] for n in transition_text_nodes]
                }
                if_else_conditions.append(branch)
            
            # 添加 fallback 分支
            fallback_condition_id = str(uuid.uuid4())
            if_else_conditions.append({
                "condition_id": fallback_condition_id,
                "condition_name": "Other",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": []
            })
            
            condition_node = {
                "type": "condition",
                "name": condition_node_name,
                "title": "Condition Routing",
                "if_else_conditions": if_else_conditions,
                "_flow_id_to_jump_node": flow_id_to_jump_node,
                "_jump_to_main_agent_node": jump_to_main_agent_name,
                "_fallback_condition_id": fallback_condition_id
            }
            nodes.append(condition_node)
            
            # 如果没有 intent 路由，条件路由节点就是入口
            if not intent_routes:
                route_groups_entry_node_name = condition_node_name
            
            # 存储纯条件节点名称到语义节点（用于边生成）
            if intent_routes:
                semantic_node['_pure_condition_node'] = condition_node_name
                semantic_node['_pure_condition_fallback_id'] = fallback_condition_id
        
        logger.info(f"    ✅ Generated {len(nodes)} route groups nodes, entry: {route_groups_entry_node_name}")
        
        return nodes, edges, route_groups_entry_node_name

    def generate_workflow_from_page(
        self, 
        page: Dict[str, Any], 
        lang: str = "en",
        page_id_map: Dict[str, Any] = None,
        all_nodes: List[Dict[str, Any]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
        """
        从单个page生成完整的工作流配置（nodes和edges）
        注意：不再为每个page生成start节点，只有flow层级有唯一的start节点
        
        Args:
            page: page配置字典
            lang: 语言代码
            
        Returns:
            (nodes_config, edge_config, entry_node_name) - 返回该page的入口节点名称
        """
        nodes = []
        edges = []
        
        # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
        page_id = page.get('key') or page.get('pageId', '')
        # 支持两种格式：'value.displayName' (转换后的格式) 和 'displayName' (原始格式)
        page_name = page.get('value', {}).get('displayName', '') or page.get('displayName', '')
        
        vprint(f"Processing Page: {page_name} ({page_id[:8]}...)")
        
        # 不再添加start节点，直接从第一个节点开始
        previous_node = None  # 初始没有前置节点
        entry_node_name = None  # 记录该page的第一个节点（入口节点）
        
        # 1. 先解析setParameterActions，生成code节点（变量赋值应该在responses之前执行）
        code_node, output_vars = parse_parameter_actions(page, self._generate_unique_node_name)
        if code_node:
            nodes.append(code_node)
            
            # code节点是entry node（如果存在）
            if entry_node_name is None:
                entry_node_name = code_node['name']
            
            # 添加边：前一个节点 -> code节点（如果有前置节点）
            if previous_node:
                edges.append({
                    "source_node": previous_node,
                    "target_node": code_node['name'],
                    "connection_type": "default"
                })
            previous_node = code_node['name']
            
            vprint(f"  - Generated 1 code node, output variables: {', '.join(output_vars)}")
        
        # 2. 再解析responses，生成text节点（responses在变量赋值之后执行）
        # text_nodes = parse_responses(page, lang, self._generate_unique_node_name)
        # writed by senlin.deng 2026-01-15
        # 修复：responses中可能包含表达式code节点，需要先解析出来。
        response_code_node, text_nodes = parse_responses(page, lang, self._generate_unique_node_name)
        
        # 2.1 如果有表达式code节点，先添加它
        if response_code_node:
            nodes.append(response_code_node)
            
            # 如果还没有entry node，code节点就是entry node
            if entry_node_name is None:
                entry_node_name = response_code_node['name']
            
            # 添加边：前一个节点 -> code节点
            if previous_node:
                edges.append({
                    "source_node": previous_node,
                    "target_node": response_code_node['name'],
                    "connection_type": "default"
                })
            previous_node = response_code_node['name']
            
            vprint(f"  - Generated response expression code node: {response_code_node['name']}")
        
        # 2.2 添加text节点
        for idx, text_node in enumerate(text_nodes):
            nodes.append(text_node)
            
            # 如果还没有entry node，第一个text节点就是entry node
            if idx == 0 and entry_node_name is None:
                entry_node_name = text_node['name']
            
            # 添加边：前一个节点 -> text节点（如果有前置节点）
            if previous_node:
                edges.append({
                    "source_node": previous_node,
                    "target_node": text_node['name'],
                    "connection_type": "default"
                })
            previous_node = text_node['name']
        
        print(f"  - Generated {len(text_nodes)} text nodes")
        
        # 2.5 page 层 slots 抽取
        from step2.page_slot_extractor import build_page_slot_chain
        slot_nodes, slot_edges, entry_node_name, previous_node = build_page_slot_chain(
            page=page,
            page_id=page_id,
            lang=self.lang,
            entity_candidates=self.entity_candidates,
            gen_unique_name=self._generate_unique_node_name,
            gen_var=self._generate_variable_name,
            existing_entry=entry_node_name,
            previous_node=previous_node,
            gen_variable_name=self._generate_variable_name,
            entity_kinds=self.entity_kinds,  # 传入 entity kind 类型映射
            global_config=self.global_config
        )
        nodes.extend(slot_nodes)
        edges.extend(slot_edges)
        
        # 3. 解析transitionEvents，生成意图识别和条件判断节点
        intent_nodes, condition_branches, _ = parse_transition_events(
            page=page,
            intents_mapping=self.intents_mapping,
            intent_parameters_map=self.intent_parameters_map,
            gen_unique_node_name=self._generate_unique_node_name,
            gen_variable_name=self._generate_variable_name,
            generate_setparameter_code_node_func=lambda set_param_actions, page_id, intent_name: generate_setparameter_code_node(set_param_actions, page_id, intent_name, self._generate_unique_node_name),
            generate_intent_and_condition_nodes_func=self._generate_intent_and_condition_nodes,
            entity_candidates=self.entity_candidates,
            lang=self.lang,
            node_counter_ref=[self.node_counter]
        )
        
        # writed by senlin.deng 2026-01-21
        # 修复：将 intent_nodes 添加到 nodes 列表中（之前遗漏了这一步，导致所有 transitionEvents 生成的节点都没有被添加）
        nodes.extend(intent_nodes)
        if intent_nodes:
            print(f"  - Generated {len(intent_nodes)} intent/transition nodes")

        # 3.1 生成jump节点（用于页面层级的跳转到另一个flow）
        # 直接从 page 的 transitionEvents 中查找，检查 transitionEventHandler 中的 targetFlowId 和 targetPageId
        # 如果遇到需要跳转到 flow（targetFlowId 存在且 targetPageId 不存在），就创建 jump 节点
        # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
        page_id = page.get('key') or page.get('pageId', '')
        # 支持两种格式：'value' (转换后的格式) 和直接属性 (原始格式)
        page_value = page.get('value', {}) if 'value' in page else page
        page_display_name = page_value.get('displayName', '')
        transition_events = page_value.get('transitionEvents', [])
        
        # Check for condition=true transitions
        # Two types: 
        # 1. Direct jump to flow (target page is pure relay)
        # 2. Direct connection to target page (target page has content)
        direct_jump_connections = []  # For pure relay pages (only have jump logic)
        direct_page_connections = []  # For pages with content (buttons, text, etc.)
        
        transition_info_list = []
        for event in transition_events:
            handler = event.get('transitionEventHandler', {})
            target_page_id = handler.get('targetPageId')
            target_flow_id = handler.get('targetFlowId')
            condition = event.get('condition', {})
            condition_string = event.get('conditionString', '')
            trigger_intent_id = event.get('triggerIntentId')
            is_literal_condition = condition_string.strip().lower() in ('true', 'false') if condition_string else False
            
            # 检查condition是否为true
            restriction = condition.get('restriction', condition)
            condition_rhs = restriction.get('rhs', {}) if isinstance(restriction, dict) else {}
            condition_value = condition_rhs.get('value') if isinstance(condition_rhs, dict) else None
            comparator = restriction.get('comparator', "") if isinstance(restriction, dict) else condition.get('comparator', "")
            
            # Treat literal conditionString (true/false) as a normal condition branch
            # so it won't bypass the condition node.
            is_always_true = self._is_always_true_condition(condition) and not is_literal_condition
            
            # If condition=true and has targetPageId, create direct connection
            # Only allow direct connections for non-intent routes.
            if is_always_true and not trigger_intent_id and target_page_id and page_id_map:
                target_page_flow_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                if target_page_flow_info and target_page_flow_info.get('is_always_true'):
                    # Target page is pure relay (only has jump logic), connect to jump node
                    target_flow_id_from_page = target_page_flow_info.get('target_flow_id')
                    if target_flow_id_from_page:
                        page_data = page_id_map.get(target_page_id)
                        page_display_name_target = ''
                        if page_data:
                            if 'value' in page_data:
                                page_display_name_target = page_data.get('value', {}).get('displayName', '')
                            else:
                                page_display_name_target = page_data.get('displayName', '')
                        
                        direct_jump_connections.append({
                            'target_flow_id': target_flow_id_from_page,
                            'target_page_id': target_page_id,
                            'page_display_name': page_display_name_target,
                            'set_parameter_actions': target_page_flow_info.get('set_parameter_actions', [])
                        })
                        print(f"    [FOUND] Direct jump connection: text node -> jump node (relay page: {page_display_name_target or target_page_id[:8]})")
                else:
                    # Target page has content (buttons, text, delay, etc.), connect to target page directly
                    page_data = page_id_map.get(target_page_id)
                    page_display_name_target = ''
                    if page_data:
                        if 'value' in page_data:
                            page_display_name_target = page_data.get('value', {}).get('displayName', '')
                        else:
                            page_display_name_target = page_data.get('displayName', '')
                    
                    direct_page_connections.append({
                        'target_page_id': target_page_id,
                        'page_display_name': page_display_name_target
                    })
                    print(f"    [FOUND] Direct page connection: text node -> page (target: {page_display_name_target or target_page_id[:8]})")
            
            # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
            if target_flow_id and not target_page_id:
                transition_info_list.append({
                    'target_page_id': target_page_id,
                    'target_flow_id': target_flow_id,
                    'page_display_name': page_display_name  # 添加 page 的 displayName
                })
                print(f"    [FOUND] Found jump to flow: targetFlowId={target_flow_id[:8]}..., page={page_display_name or page_id[:8]}")
        
        jump_nodes, jump_node_names = self._generate_jump_nodes_for_page_transitions(transition_info_list, page_id, all_nodes)
        nodes.extend(jump_nodes)

        if jump_nodes:
            print(f"  - Generated {len(jump_nodes)} jump nodes for page transitions")
            # 注意：jump 节点的边将在处理完所有节点后添加，因为需要找到 page 的最后一个节点
        
        # Handle direct connections (condition=true)
        # 1. Direct jump connections (target page is pure relay)
        # 2. Direct page connections (target page has content)
        if direct_jump_connections or direct_page_connections:
            # writed by senlin.deng 2026-01-21
            # 修复：如果当前页面已经生成了 transitionEvents 的节点链（capture/semantic/condition），则不要再用 always-true 的直连边覆盖入口节点。否则会导致跳过当前page中的其他节点直接连接到下一个节点。
            # 如果当前页面已经生成了 transitionEvents 的节点链（capture/semantic/condition），
            # 则不要再用 always-true 的直连边覆盖入口节点。
            if intent_nodes:
                logger.debug("  🔀 Skip direct always-true connections: transitionEvents nodes exist")
            else:
                # Find the last node of this page (should be text node)
                last_text_node = previous_node if previous_node else None
                if not last_text_node and nodes:
                    # 找到最后一个text节点
                    for node in reversed(nodes):
                        if node.get('type') == 'textReply':
                            last_text_node = node.get('name')
                            break
                
                if last_text_node:
                    for jump_info in direct_jump_connections:
                        target_flow_id = jump_info.get('target_flow_id')
                        target_page_id = jump_info.get('target_page_id')
                        page_display_name_target = jump_info.get('page_display_name', '')
                    
                    # 查找或创建对应的jump节点（同时检查 all_nodes）
                    jump_node = self._find_jump_node_for_target(target_flow_id, None, jump_nodes, page_display_name_target, all_nodes)
                    if not jump_node:
                        # 创建新的jump节点：直接使用 jump_to_{page_id前8位}，不添加计数器
                        jump_node_name = f"jump_to_{target_page_id[:8]}"
                        
                        # 确保节点名称唯一
                        existing_names = {n.get('name') for n in jump_nodes}
                        existing_names.update({n.get('name') for n in nodes})
                        if all_nodes:
                            existing_names.update({n.get('name') for n in all_nodes})
                        
                        # 如果名称已存在，检查是否是相同的 jump 节点
                        if jump_node_name in existing_names:
                            if all_nodes:
                                for node in all_nodes:
                                    if node.get('type') == 'jump' and node.get('name') == jump_node_name:
                                        jump_flow_uuid = node.get('jump_flow_uuid')
                                        if jump_flow_uuid == target_flow_id:
                                            jump_node = node
                                            jump_node_name = node.get('name')
                                            break
                        
                        # title使用page_display_name
                        if page_display_name_target:
                            title = f"jump_to_{page_display_name_target}"
                        else:
                            title = f"jump_to_{target_flow_id[:8]}"
                        
                        jump_node = {
                            "type": "jump",
                            "name": jump_node_name,
                            "title": title,
                            "jump_type": "flow",
                            "jump_robot_id": "",
                            "jump_robot_name": "",
                            "jump_carry_history_number": 5,
                            "jump_flow_name": page_display_name_target if page_display_name_target else "",
                            "jump_flow_uuid": target_flow_id,
                            "jump_carry_userinput": True,
                            "transition_info": {
                                "target_page_id": None,
                                "target_flow_id": target_flow_id,
                                "page_display_name": page_display_name_target
                            }
                        }
                        jump_nodes.append(jump_node)
                        nodes.append(jump_node)
                        print(f'    ✅ Created jump node for direct connection: {jump_node_name} -> {target_flow_id[:8]}...')
                    
                    # Connect text node directly to jump node
                    if jump_node:
                        jump_node_name = jump_node.get('name')
                        # Check if edge already exists
                        has_edge = any(e.get('target_node') == jump_node_name and e.get('source_node') == last_text_node for e in edges)
                        if not has_edge:
                            edges.append({
                                "source_node": last_text_node,
                                "target_node": jump_node_name,
                                "connection_type": "default"
                            })
                            print(f'    ✅ Added direct edge: {last_text_node} → {jump_node_name} (always true → relay page)')
                        
                        # Remove this target_page_id from condition_branches to avoid duplicate connection via condition node
                        condition_branches = [b for b in condition_branches 
                                            if b.get('target_page_id') != target_page_id]
                
                    # Handle direct page connections (condition=true, target page has content)
                    for page_info in direct_page_connections:
                        target_page_id = page_info.get('target_page_id')
                        page_display_name_target = page_info.get('page_display_name', '')
                        
                        # Target node is the page entry node
                        target_node_name = f"page_{target_page_id[:8]}"
                        
                        # Check if edge already exists
                        has_edge = any(e.get('target_node') == target_node_name and e.get('source_node') == last_text_node for e in edges)
                        if not has_edge:
                            edges.append({
                                "source_node": last_text_node,
                                "target_node": target_node_name,
                                "connection_type": "default"
                            })
                            print(f'    ✅ Added direct edge: {last_text_node} → {target_node_name} (always true → page: {page_display_name_target or target_page_id[:8]})')

        # 3.2 处理全局transition（triggerIntentId为null，condition为GLOBAL true）
        global_nodes, global_edges = self._generate_global_transition_nodes(page)
        nodes.extend(global_nodes)
        edges.extend(global_edges)

        if global_nodes:
            print(f"  - Generated {len(global_nodes)} global transition nodes")

        # 3.3 处理 routeGroupsTransitionEvents
        # 当 transitionEvents 没有匹配时（Fallback），应该优先进入 routeGroupsTransitionEvents 的处理
        route_groups_nodes, route_groups_edges, route_groups_entry_node = self._generate_route_groups_nodes(
            page=page,
            page_id=page_id,
            lang=self.lang,
            jump_nodes=jump_nodes,
            page_id_map=page_id_map,
            all_nodes=all_nodes
        )
        nodes.extend(route_groups_nodes)
        edges.extend(route_groups_edges)
        # logger.info(route_groups_nodes)
        # exit()
        
        # writed by senlin.deng 2026-01-21
        # 新增：当页面没有 transitionEvents 但有 routeGroupsTransitionEvents 时，
        # 将 onLoad 生成的最后一个节点（previous_node）直接连接到 route_groups_entry_node
        if route_groups_entry_node and not intent_nodes and previous_node:
            # 检查边是否已存在
            edge_exists = any(
                e.get('source_node') == previous_node and e.get('target_node') == route_groups_entry_node
                for e in edges
            )
            if not edge_exists:
                edges.append({
                    "source_node": previous_node,
                    "target_node": route_groups_entry_node,
                    "connection_type": "default"
                })
                print(f"    ✅ Added edge: {previous_node} → {route_groups_entry_node} (onLoad → routeGroupsTransitionEvents)")
        
        # 生成 Route Groups 节点的内部边
        if route_groups_nodes and route_groups_entry_node:
            print(f"  - Generated {len(route_groups_nodes)} route groups nodes, entry: {route_groups_entry_node}")
            
            # 找到 Route Groups 语义判断节点和条件节点
            route_groups_semantic_node = None
            route_groups_condition_node = None
            route_groups_fallback_text_node = None
            
            for node in route_groups_nodes:
                node_type = node.get('type')
                title = node.get('title', '')
                
                if node_type == 'semanticJudgment' and 'Route Groups' in title:
                    route_groups_semantic_node = node
                elif node_type == 'condition' and 'Condition Routing' in title:
                    route_groups_condition_node = node
            
            # 生成 Route Groups 语义判断节点的边
            if route_groups_semantic_node:
                rg_semantic_node_name = route_groups_semantic_node.get('name')
                rg_semantic_branches = route_groups_semantic_node.get('_internal_branches', [])
                rg_condition_id_to_intent = route_groups_semantic_node.get('_condition_id_to_intent', {})
                rg_has_pure_conditions = route_groups_semantic_node.get('_has_pure_conditions', False)
                rg_pure_condition_node = route_groups_semantic_node.get('_pure_condition_node')
                rg_flow_id_to_jump_node = route_groups_semantic_node.get('_flow_id_to_jump_node', {})
                rg_jump_to_main_agent = route_groups_semantic_node.get('_jump_to_main_agent_node')
                
                for branch in rg_semantic_branches:
                    branch_condition_id = branch.get('condition_id')
                    
                    if branch.get('logical_operator') == 'other':
                        # Fallback 分支
                        if rg_has_pure_conditions and rg_pure_condition_node:
                            # 有纯条件路由：连接到纯条件路由节点
                            self._safe_append_edge(edges, rg_semantic_node_name, rg_pure_condition_node, "condition", branch_condition_id, all_nodes)
                            logger.info(f"    🔀 Route Groups Semantic Other → Condition Routing ({rg_pure_condition_node})")
                        elif rg_jump_to_main_agent:
                            # 无纯条件路由：直接连接到 Jump_to_main_agent 节点
                            self._safe_append_edge(edges, rg_semantic_node_name, rg_jump_to_main_agent, "condition", branch_condition_id, all_nodes)
                            logger.info(f"    🔀 Route Groups Semantic Other → Jump_to_main_agent ({rg_jump_to_main_agent})")
                    else:
                        # Intent 分支 → 处理 setParameterActions 和 beforeTransition 节点
                        route = rg_condition_id_to_intent.get(branch_condition_id, {})
                        code_node = branch.get('code_node')
                        text_nodes = branch.get('text_nodes', [])
                        target_page_id = route.get('target_page_id')
                        target_flow_id = route.get('target_flow_id')
                        
                        # 构建连接链：semantic → code → text_nodes → target
                        current_source = rg_semantic_node_name
                        
                        if code_node:
                            self._safe_append_edge(edges, current_source, code_node, "condition", branch_condition_id, all_nodes)
                            current_source = code_node
                        
                        if text_nodes:
                            for idx, text_node_name in enumerate(text_nodes):
                                if idx == 0 and not code_node:
                                    self._safe_append_edge(edges, current_source, text_node_name, "condition", branch_condition_id, all_nodes)
                                else:
                                    self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                current_source = text_node_name
                        
                        # 连接到最终目标
                        if target_flow_id:
                            # 优先使用新生成的 jump 节点
                            if target_flow_id in rg_flow_id_to_jump_node:
                                final_target = rg_flow_id_to_jump_node[target_flow_id]
                            else:
                                # 尝试在已有的 jump_nodes 中查找
                                jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                                if jump_node:
                                    final_target = jump_node['name']
                                else:
                                    final_target = f"page_{target_flow_id[:8]}"
                            
                            if not code_node and not text_nodes:
                                self._safe_append_edge(edges, current_source, final_target, "condition", branch_condition_id, all_nodes)
                            else:
                                self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                            # logger.info(f"    🔀 Route Groups Intent ({route.get('intent_name', 'Unknown')}) → {final_target}")
                        elif target_page_id:
                            # 有 targetPageId 的情况
                            jump_node = self._find_jump_node_for_target(None, target_page_id, jump_nodes, None, all_nodes)
                            if jump_node:
                                final_target = jump_node['name']
                            else:
                                final_target = f"page_{target_page_id[:8]}"
                            
                            if not code_node and not text_nodes:
                                self._safe_append_edge(edges, current_source, final_target, "condition", branch_condition_id, all_nodes)
                            else:
                                self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                            logger.info(f"    🔀 Route Groups Intent ({route.get('intent_name', 'Unknown')}) → {final_target}")
            
            # 生成 Route Groups 条件路由节点的边
            if route_groups_condition_node:
                rg_condition_node_name = route_groups_condition_node.get('name')
                if_else_conditions = route_groups_condition_node.get('if_else_conditions', [])
                rg_cond_flow_id_to_jump_node = route_groups_condition_node.get('_flow_id_to_jump_node', {})
                rg_cond_jump_to_main_agent = route_groups_condition_node.get('_jump_to_main_agent_node')
                rg_cond_fallback_id = route_groups_condition_node.get('_fallback_condition_id')
                
                for branch in if_else_conditions:
                    condition_id = branch.get('condition_id')
                    target_page_id = branch.get('target_page_id')
                    target_flow_id = branch.get('target_flow_id')
                    transition_code_node = branch.get('transition_code_node')
                    transition_text_nodes = branch.get('transition_text_nodes', [])
                    
                    if branch.get('logical_operator') == 'other':
                        # 最终 Fallback - 连接到 Jump_to_main_agent 节点
                        if rg_cond_jump_to_main_agent:
                            self._safe_append_edge(edges, rg_condition_node_name, rg_cond_jump_to_main_agent, "condition", condition_id, all_nodes)
                            logger.info(f"    🔀 Route Groups Condition Other → Jump_to_main_agent ({rg_cond_jump_to_main_agent})")
                    elif target_flow_id:
                        # 有 targetFlowId 的情况，优先使用新生成的 jump 节点
                        if target_flow_id in rg_cond_flow_id_to_jump_node:
                            final_target = rg_cond_flow_id_to_jump_node[target_flow_id]
                        else:
                            jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                            if jump_node:
                                final_target = jump_node['name']
                            else:
                                final_target = f"page_{target_flow_id[:8]}"
                        
                        # 构建连接链
                        current_source = rg_condition_node_name
                        
                        if transition_code_node:
                            self._safe_append_edge(edges, current_source, transition_code_node, "condition", condition_id, all_nodes)
                            current_source = transition_code_node
                        
                        if transition_text_nodes:
                            for idx, text_node_name in enumerate(transition_text_nodes):
                                if idx == 0 and not transition_code_node:
                                    self._safe_append_edge(edges, current_source, text_node_name, "condition", condition_id, all_nodes)
                                else:
                                    self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                current_source = text_node_name
                        
                        if not transition_code_node and not transition_text_nodes:
                            self._safe_append_edge(edges, current_source, final_target, "condition", condition_id, all_nodes)
                        else:
                            self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                        logger.info(f"    🔀 Route Groups Condition ({branch.get('condition_name', 'Unknown')}) → {final_target}")
                    elif target_page_id:
                        # 有 targetPageId 的情况
                        jump_node = self._find_jump_node_for_target(None, target_page_id, jump_nodes, None, all_nodes)
                        if jump_node:
                            final_target = jump_node['name']
                        else:
                            final_target = f"page_{target_page_id[:8]}"
                        
                        # 构建连接链
                        current_source = rg_condition_node_name
                        
                        if transition_code_node:
                            self._safe_append_edge(edges, current_source, transition_code_node, "condition", condition_id, all_nodes)
                            current_source = transition_code_node
                        
                        if transition_text_nodes:
                            for idx, text_node_name in enumerate(transition_text_nodes):
                                if idx == 0 and not transition_code_node:
                                    self._safe_append_edge(edges, current_source, text_node_name, "condition", condition_id, all_nodes)
                                else:
                                    self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                current_source = text_node_name
                        
                        if not transition_code_node and not transition_text_nodes:
                            self._safe_append_edge(edges, current_source, final_target, "condition", condition_id, all_nodes)
                        else:
                            self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                        logger.info(f"    🔀 Route Groups Condition ({branch.get('condition_name', 'Unknown')}) → {final_target}")

        if intent_nodes:
            capture_node = None
            kb_node = None
            extract_intent_code = None
            intent_routing_condition = None  # 情况1：有intent无parameter
            first_intent_check = None  # 情况2：链式结构的第一个intent_check
            intent_check_nodes = []  # 所有intent_check节点
            param_condition_nodes = []  # 所有param_condition节点
            fallback_text_node = None
            llm_to_code = {}  # llm_node_name -> code_node_name
            
            # 版本2：语义判断节点相关变量
            semantic_judgment_node = None  # 语义判断节点
            semantic_llm_nodes = []  # 从语义判断节点连接的LLM节点
            semantic_code_nodes = []  # 从语义判断节点连接的CODE节点
            semantic_param_conditions = []  # 从语义判断节点连接的参数条件节点
            
            # Semantic NER 节点相关变量
            ner_semantic_nodes = []  # NER Semantic 节点
            ner_code_nodes = []  # NER Code 节点
            ner_capture_nodes = []  # NER Capture 节点
            ner_condition_nodes = []  # NER 条件路由节点
            
            # 第一遍：识别所有节点类型
            # 注意：intent_nodes 已经在上面通过 nodes.extend(intent_nodes) 添加过了，
            # 这里只是遍历识别节点类型，不再重复添加！
            for node in intent_nodes:
                # nodes.append(node)  # writed by senlin.deng 2026-01-24: 移除重复添加，intent_nodes 已在第 5053 行添加
                node_type = node.get('type')
                node_name = node.get('name')
                title = node.get('title', '')
                
                # Pattern 3 入口节点标记（优先级最高）
                if node.get('_is_entry_node') and entry_node_name is None:
                    entry_node_name = node_name
                    logger.debug(f"  ✓ Pattern 3 entry node: {node_name}")
                
                if node_type == 'captureUserReply':
                    # 区分两种capture节点：Capture User Input 和 Capture Parameter Value
                    if 'Parameter' in title:
                        # 这是pattern 4中的第二个capture，不作为主capture节点
                        pass
                    else:
                        # 这是第一个capture节点
                        capture_node = node_name
                        if entry_node_name is None:
                            entry_node_name = capture_node
                
                elif node_type == 'knowledgeAssignment':
                    kb_node = node_name
                
                elif node_type == 'semanticJudgment':
                    # 版本2：语义判断节点
                    # 检查是否是 NER Semantic 节点
                    if node.get('_is_ner_semantic'):
                        ner_semantic_nodes.append(node)
                    else:
                        semantic_judgment_node = node
                
                elif node_type == 'captureUserReply' and node.get('_is_ner_capture'):
                    # NER Capture 节点
                    ner_capture_nodes.append(node)
                
                elif node_type == 'code':
                    # 检查是否是 NER Code 节点
                    if node.get('_is_ner_code'):
                        ner_code_nodes.append(node)
                    elif 'Extract Intent' in title:
                        extract_intent_code = node_name
                    elif node.get('from_semantic_condition_id'):
                        # 版本2：从语义判断节点连接的CODE节点
                        semantic_code_nodes.append(node)
                
                elif node_type == 'llm' or node_type == 'llmVariableAssignment':
                    if node.get('from_semantic_condition_id'):
                        # 版本2：从语义判断节点连接的LLM节点
                        semantic_llm_nodes.append(node)
                
                elif node_type == 'condition':
                    # 检查是否是 NER 条件路由节点
                    if node.get('_is_ner_condition'):
                        ner_condition_nodes.append(node)
                    elif 'Intent Routing' in title:
                        # 情况1：有intent无parameter（版本1）或版本2的无参数情况
                        intent_routing_condition = node_name
                    elif 'Check if Intent is' in title:
                        # 情况2：链式结构的intent_check节点
                        intent_check_nodes.append(node)
                        if first_intent_check is None:
                            first_intent_check = node_name
                    elif 'Parameter Routing for' in title or 'Parameter Routing (' in title or 'NER Parameter Routing' in title:
                        # 情况2：参数路由节点 或 版本2的参数路由节点
                        param_condition_nodes.append(node)
                        if node.get('from_semantic_condition_id'):
                            semantic_param_conditions.append(node)
                
                # write by senlin.deng 2026-01-21: 同时匹配旧的 fallback_text 节点和新的 jump_to_main_agent 节点
                elif (node_type == 'textReply' and 'fallback' in node_name) or \
                     (node_type == 'jump' and 'jump_to_main_agent' in node_name):
                    fallback_text_node = node_name
            
            # 第二遍：建立LLM→CODE的映射
            llm_nodes = [n for n in intent_nodes if n.get('type') == 'llmVariableAssignment']
            code_nodes = [n for n in intent_nodes if n.get('type') == 'code' and 'Parse Parameters' in n.get('title', '')]
            
            # 记录 param_condition 节点对应的混合条件 code 节点（如果有）
            param_condition_mixed_nodes = {}
            for param_node in param_condition_nodes:
                mixed_node_name = param_node.get('combined_mixed_condition_code_node') or param_node.get('mixed_condition_code_node')
                if mixed_node_name:
                    param_condition_mixed_nodes[param_node.get('name')] = mixed_node_name
            
            # 通过名称模式匹配：llm_extract_{intent}对应parse_{intent}
            for llm_node in llm_nodes:
                llm_name = llm_node['name']
                # 提取intent部分：llm_extract_{intent}_{id}
                if 'llm_extract_' in llm_name:
                    # 找到对应的parse节点
                    for code_node in code_nodes:
                        code_name = code_node['name']
                        # 检查是否是同一个intent（通过title匹配更可靠）
                        llm_title = llm_node.get('title', '')
                        code_title = code_node.get('title', '')
                        if 'for ' in llm_title and 'for ' in code_title:
                            llm_intent = llm_title.split('for ')[-1]
                            code_intent = code_title.split('for ')[-1]
                            if llm_intent == code_intent:
                                llm_to_code[llm_name] = code_name
                                break
            
            # 第三遍：创建edges
            
            # **版本2：语义判断节点边生成逻辑**
            if semantic_judgment_node:
                semantic_node_name = semantic_judgment_node.get('name')
                reuse_v1 = semantic_judgment_node.get('_reuse_v1_parameter_extraction', False)
                
                # 1. capture → semantic_judgment
                if previous_node and capture_node:
                    self._safe_append_edge(edges, previous_node, capture_node, "default", None, all_nodes)
                if capture_node:
                    self._safe_append_edge(edges, capture_node, semantic_node_name, "default", None, all_nodes)
                
                # **修改：semanticJudgment节点不输出变量，而是通过condition_id作为边的起点连接**
                if reuse_v1:
                    # **有参数：semanticJudgment → (通过condition_id边) → intent_check的"是"分支的下一个节点（通常是LLM节点）**
                    condition_id_to_next_node = semantic_judgment_node.get('_condition_id_to_next_node', {})
                    semantic_branches = semantic_judgment_node.get('_internal_branches', [])
                    
                    # writed by senlin.deng 2026-01-17
                    # 检查是否有混合路由（Intent + 纯条件路由）
                    has_pure_conditions_v1 = semantic_judgment_node.get('_has_pure_conditions', False)
                    
                    for branch in semantic_branches:
                        branch_condition_id = branch.get('condition_id')
                        next_node_name = condition_id_to_next_node.get(branch_condition_id)
                        
                        if next_node_name:
                            # semanticJudgment直接连接到intent_check的"是"分支的下一个节点（跳过intent_check节点）
                            # 使用semanticJudgment的condition_id作为边的condition_id
                            self._safe_append_edge(edges, semantic_node_name, next_node_name, "condition", branch_condition_id, all_nodes)
                        elif branch.get('logical_operator') == 'other':
                            # Fallback分支
                            # writed by senlin.deng 2026-01-17
                            # 修正：混合路由情况下，Fallback分支不连接到fallback_text，而是连接到Pure Condition Node
                            # writed by senlin.deng 2026-01-18
                            # 新增：如果存在 routeGroupsTransitionEvents，Fallback分支应该连接到 Route Groups Intent Recognition
                            # writed by senlin.deng 2026-01-21
                            # 修复：当 has_pure_conditions_v1=True 时，需要使用 _direct_target 属性来创建边
                            if has_pure_conditions_v1:
                                # 混合路由：Fallback 连接到 Pure Condition Node（通过 _direct_target 属性）
                                direct_target = branch.get('_direct_target')
                                if direct_target:
                                    self._safe_append_edge(edges, semantic_node_name, direct_target, "condition", branch_condition_id, all_nodes)
                                    logger.debug(f"    🔀 混合路由(reuse_v1): semantic_judgment Fallback → {direct_target}")
                                else:
                                    # 如果没有 _direct_target，从 semantic_judgment_node 获取 pure_condition_node
                                    pure_condition_node = semantic_judgment_node.get('_pure_condition_node')
                                    if pure_condition_node:
                                        self._safe_append_edge(edges, semantic_node_name, pure_condition_node, "condition", branch_condition_id, all_nodes)
                                        logger.debug(f"    🔀 混合路由(reuse_v1): semantic_judgment Fallback → {pure_condition_node}")
                            elif route_groups_entry_node:
                                # 有 routeGroupsTransitionEvents：Fallback分支 → Route Groups Intent Recognition
                                self._safe_append_edge(edges, semantic_node_name, route_groups_entry_node, "condition", branch_condition_id, all_nodes)
                                logger.info(f"    🔀 routeGroups(reuse_v1): Fallback → Route Groups Intent Recognition ({route_groups_entry_node})")
                            elif fallback_text_node:
                                # 普通情况：Fallback分支 → fallback_text
                                self._safe_append_edge(edges, semantic_node_name, fallback_text_node, "condition", branch_condition_id, all_nodes)
                        else:
                            # **修复：当 next_node_name 为空时（skip_param_condition=True 且没有变量提取），需要检查是否有 transition_code_node**
                            target_page_id = branch.get('target_page_id')
                            target_flow_id = branch.get('target_flow_id')
                            transition_code_node = branch.get('transition_code_node')
                            transition_text_nodes = branch.get('transition_text_nodes', [])
                            
                            if target_page_id or target_flow_id:
                                jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                                
                                # 如果 target_page_id 存在，检查该 page 是否有跳转到 flow 的逻辑
                                if target_page_id and not jump_node and page_id_map:
                                    target_page_flow_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                                    if target_page_flow_info and target_page_flow_info.get('is_always_true'):
                                        target_flow_id_from_page = target_page_flow_info.get('target_flow_id')
                                        if target_flow_id_from_page:
                                            page_data = page_id_map.get(target_page_id)
                                            page_display_name_target = ''
                                            if page_data:
                                                if 'value' in page_data:
                                                    page_display_name_target = page_data.get('value', {}).get('displayName', '')
                                                else:
                                                    page_display_name_target = page_data.get('displayName', '')
                                            jump_node = self._find_jump_node_for_target(target_flow_id_from_page, None, jump_nodes, page_display_name_target, all_nodes)
                                
                                if jump_node:
                                    final_target = jump_node['name']
                                else:
                                    target_page = target_page_id or target_flow_id
                                    final_target = f"page_{target_page[:8]}"
                                # writed by senlin.deng 2026-02-06
                                # 修复当存在 setParameterActions 时，应该先连接到 Set Parameters 节点，再连接到 jumpto 节点
                                # 构建连接链：semantic_judgment → transition_code_node → transition_text_nodes → final_target
                                current_source = semantic_node_name
                                
                                # 1. 先连接 code 节点（setParameterActions）- 必须在 text 节点之前
                                if transition_code_node:
                                    self._safe_append_edge(edges, current_source, transition_code_node, "condition", branch_condition_id, all_nodes)
                                    current_source = transition_code_node  # 更新当前源节点
                                
                                # 2. 连接 text 节点链（staticUserResponse）- 在 code 节点之后
                                if transition_text_nodes:
                                    for idx, text_node_name in enumerate(transition_text_nodes):
                                        if idx == 0:
                                            # 第一个 text 节点：从当前源节点（可能是 semantic_node 或 code 节点）连接
                                            self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                        else:
                                            # 后续 text 节点：从前一个 text 节点连接
                                            self._safe_append_edge(edges, transition_text_nodes[idx-1], text_node_name, "default", None, all_nodes)
                                        current_source = text_node_name  # 更新当前源节点
                                
                                # 3. 连接到最终目标
                                if transition_code_node or transition_text_nodes:
                                    # 有中间节点时，使用 default 连接
                                    self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                                else:
                                    # 没有中间节点时，使用 condition 连接
                                    self._safe_append_edge(edges, current_source, final_target, "condition", branch_condition_id, all_nodes)
                                print(f"    ✅ Added edge chain: {semantic_node_name} → {' → '.join([n for n in [transition_code_node] + transition_text_nodes + [final_target] if n])}")
                    
                    print(f"    ✅ 版本2边生成完成（有参数，复用版本一逻辑）: semantic_judgment → intent_check的下一个节点（通过condition_id边）")
                else:
                    # **无参数：semanticJudgment → (通过condition_id边) → 直接连接到目标页面或intent_routing_condition的对应分支的下一个节点**
                    intent_routing_condition_name = semantic_judgment_node.get('_intent_routing_condition_node')
                    semantic_branches = semantic_judgment_node.get('_internal_branches', [])
                    
                    if intent_routing_condition_name:
                        # 找到intent_routing_condition节点，获取每个分支连接的下一个节点
                        intent_routing_node = next((n for n in intent_nodes if n.get('name') == intent_routing_condition_name), None)
                        condition_id_to_target = {}  # semanticJudgment的condition_id → intent_routing_condition分支的下一个节点
                        condition_id_to_transition_code = {}  # semanticJudgment的condition_id → transition_code_node
                        condition_id_to_transition_text = {}  # semanticJudgment的condition_id → transition_text_nodes列表
                        
                        # 从semantic_judgment_node获取intent_to_condition_id映射
                        intent_to_condition_id_map = semantic_judgment_node.get('_intent_to_condition_id', {})
                        
                        if intent_routing_node:
                            for branch in intent_routing_node.get('if_else_conditions', []):
                                branch_cond_id = branch.get('condition_id')
                                # 找到对应的semanticJudgment的condition_id
                                for intent_name, cond_id in intent_to_condition_id_map.items():
                                    if branch_cond_id == cond_id or f"Intent_{intent_name}" in branch.get('condition_name', ''):
                                        # 获取该分支的 transition_code_node 和 transition_text_nodes
                                        transition_code_node = branch.get('transition_code_node')
                                        transition_text_nodes = branch.get('transition_text_nodes', [])
                                        
                                        if transition_code_node:
                                            condition_id_to_transition_code[cond_id] = transition_code_node
                                        if transition_text_nodes:
                                            condition_id_to_transition_text[cond_id] = transition_text_nodes
                                        
                                        # 获取该分支连接的下一个节点（target_page_id或target_flow_id）
                                        target_page_id = branch.get('target_page_id')
                                        target_flow_id = branch.get('target_flow_id')
                                        if target_page_id or target_flow_id:
                                            jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                                            if jump_node:
                                                condition_id_to_target[cond_id] = jump_node['name']
                                            else:
                                                target = target_page_id or target_flow_id
                                                condition_id_to_target[cond_id] = f"page_{target[:8]}"
                                        break
                        
                        # semanticJudgment直接连接到目标节点（跳过intent_routing_condition节点）
                        # writed by senlin.deng 2026-01-17
                        # 检查是否有混合路由（Intent + 纯条件路由）
                        has_pure_conditions = semantic_judgment_node.get('_has_pure_conditions', False)

                        for branch in semantic_branches:
                            branch_condition_id = branch.get('condition_id')
                            target_node = condition_id_to_target.get(branch_condition_id)
                            transition_code_node = condition_id_to_transition_code.get(branch_condition_id)
                            transition_text_nodes = condition_id_to_transition_text.get(branch_condition_id, [])
                            
                            if branch.get('logical_operator') == 'other':
                                # Fallback分支
                                # writed by senlin.deng 2026-01-18
                                # 新增：如果存在 routeGroupsTransitionEvents，Fallback分支应该连接到 Route Groups Intent Recognition
                                # writed by senlin.deng 2026-01-21
                                # 修复：当 has_pure_conditions=True 时，需要使用 _direct_target 属性来创建边
                                if has_pure_conditions:
                                    # 混合路由：Fallback 连接到 Pure Condition Node（通过 _direct_target 属性）
                                    direct_target = branch.get('_direct_target')
                                    if direct_target:
                                        self._safe_append_edge(edges, semantic_node_name, direct_target, "condition", branch_condition_id, all_nodes)
                                        logger.debug(f"    🔀 混合路由: semantic_judgment Fallback → {direct_target}")
                                    else:
                                        # 如果没有 _direct_target，从 semantic_judgment_node 获取 pure_condition_node
                                        pure_condition_node = semantic_judgment_node.get('_pure_condition_node')
                                        if pure_condition_node:
                                            self._safe_append_edge(edges, semantic_node_name, pure_condition_node, "condition", branch_condition_id, all_nodes)
                                            logger.debug(f"    🔀 混合路由: semantic_judgment Fallback → {pure_condition_node}")
                                elif route_groups_entry_node:
                                    # 有 routeGroupsTransitionEvents：Fallback分支 → Route Groups Intent Recognition
                                    self._safe_append_edge(edges, semantic_node_name, route_groups_entry_node, "condition", branch_condition_id, all_nodes)
                                    logger.info(f"    🔀 routeGroups: Fallback → Route Groups Intent Recognition ({route_groups_entry_node})")
                                elif fallback_text_node:
                                    # 普通情况：Fallback分支 → fallback_text
                                    self._safe_append_edge(edges, semantic_node_name, fallback_text_node, "condition", branch_condition_id, all_nodes)
                            elif target_node:
                                # 意图分支 → 构建连接链：semantic_judgment → transition_code_node → transition_text_nodes → target_node
                                # writed by senlin.deng 2026-02-06
                                # 修复当存在 setParameterActions 时，应该先连接到 Set Parameters 节点，再连接到 jumpto 节点
                                current_source = semantic_node_name
                                
                                # 1. 先连接 code 节点（setParameterActions）- 必须在 text 节点之前
                                if transition_code_node:
                                    self._safe_append_edge(edges, current_source, transition_code_node, "condition", branch_condition_id, all_nodes)
                                    current_source = transition_code_node  # 更新当前源节点
                                
                                # 2. 连接 text 节点链（staticUserResponse）- 在 code 节点之后
                                if transition_text_nodes:
                                    for idx, text_node_name in enumerate(transition_text_nodes):
                                        if idx == 0:
                                            # 第一个 text 节点：从当前源节点（可能是 semantic_node 或 code 节点）连接
                                            self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                        else:
                                            # 后续 text 节点：从前一个 text 节点连接
                                            self._safe_append_edge(edges, transition_text_nodes[idx-1], text_node_name, "default", None, all_nodes)
                                        current_source = text_node_name  # 更新当前源节点
                                
                                # 3. 连接到最终目标
                                if transition_code_node or transition_text_nodes:
                                    # 有中间节点时，使用 default 连接
                                    self._safe_append_edge(edges, current_source, target_node, "default", None, all_nodes)
                                else:
                                    # 没有中间节点时，使用 condition 连接
                                    self._safe_append_edge(edges, current_source, target_node, "condition", branch_condition_id, all_nodes)
                            elif intent_routing_condition_name:
                                # 如果没有找到目标节点，回退到连接到intent_routing_condition节点
                                self._safe_append_edge(edges, semantic_node_name, intent_routing_condition_name, "condition", branch_condition_id, all_nodes)
                        
                        print(f"    ✅ 版本2边生成完成（无参数）: semantic_judgment → 目标节点（通过condition_id边）")
                    else:
                        # 原有逻辑：semanticJudgment直接连接到LLM节点或目标页面（保留兼容性）
                        semantic_conditions = semantic_judgment_node.get('config', {}).get('semantic_conditions', [])
                        default_condition = semantic_judgment_node.get('config', {}).get('default_condition', {})
                        
                        # writed by senlin.deng 2026-01-17
                        # 检查是否有混合路由（Intent + 纯条件路由）
                        has_pure_conditions = semantic_judgment_node.get('_has_pure_conditions', False)
                    
                    # 2. semantic_judgment的每个condition_id → 对应的LLM节点或目标页面
                    # 构建 condition_id → LLM节点的映射
                    condition_id_to_llm = {}
                    for llm_node in semantic_llm_nodes:
                        cond_id = llm_node.get('from_semantic_condition_id')
                        if cond_id:
                            condition_id_to_llm[cond_id] = llm_node.get('name')
                    
                    # 构建 LLM节点 → CODE节点的映射
                    llm_to_code_semantic = {}
                    for code_node in semantic_code_nodes:
                        cond_id = code_node.get('from_semantic_condition_id')
                        if cond_id:
                            # 找到对应的LLM节点
                            llm_name = condition_id_to_llm.get(cond_id)
                            if llm_name:
                                llm_to_code_semantic[llm_name] = code_node.get('name')
                    
                    # 构建 CODE节点 → param_condition节点的映射
                    code_to_param_condition = {}
                    for param_cond in semantic_param_conditions:
                        cond_id = param_cond.get('from_semantic_condition_id')
                        if cond_id:
                            # 找到对应的CODE节点
                            llm_name = condition_id_to_llm.get(cond_id)
                            if llm_name:
                                code_name = llm_to_code_semantic.get(llm_name)
                                if code_name:
                                    code_to_param_condition[code_name] = param_cond.get('name')
                    
                    # 为每个语义条件创建边
                    for branch in semantic_branches:
                        branch_condition_id = branch.get('condition_id')
                        target_page_id = branch.get('target_page_id')
                        target_flow_id = branch.get('target_flow_id')
                        has_param_extraction = branch.get('has_parameter_extraction', False)
                        param_entry = branch.get('param_extraction_entry')
                        
                        # 检查是否有对应的LLM节点（有参数需要提取的情况）
                        llm_node_name = condition_id_to_llm.get(branch_condition_id)
                        
                        if llm_node_name:
                            # 有参数提取：semantic_judgment → LLM
                            self._safe_append_edge(edges, semantic_node_name, llm_node_name, "condition", branch_condition_id, all_nodes)
                            
                            # LLM → CODE
                            code_name = llm_to_code_semantic.get(llm_node_name)
                            if code_name:
                                self._safe_append_edge(edges, llm_node_name, code_name, "default", None, all_nodes)
                                
                                # CODE → param_condition（如果有）
                                param_cond_name = code_to_param_condition.get(code_name)
                                if param_cond_name:
                                    self._safe_append_edge(edges, code_name, param_cond_name, "default", None, all_nodes)
                                    
                                    # param_condition的分支 → target_pages 或 fallback_text
                                    param_cond_node = next((n for n in semantic_param_conditions if n.get('name') == param_cond_name), None)
                                    if param_cond_node:
                                        for param_branch in param_cond_node.get('if_else_conditions', []):
                                            param_cond_id = param_branch.get('condition_id')
                                            param_target_page = param_branch.get('target_page_id')
                                            param_target_flow = param_branch.get('target_flow_id')
                                            
                                            # 检查是否是 fallback 分支（需要连接到 fallback_text）
                                            if param_branch.get('target_fallback_text'):
                                                # fallback 分支 → fallback_text 节点
                                                if fallback_text_node:
                                                    self._safe_append_edge(edges, param_cond_name, fallback_text_node, "condition", param_cond_id, all_nodes)
                                            elif param_target_page or param_target_flow:
                                                jump_node = self._find_jump_node_for_target(param_target_flow, param_target_page, jump_nodes, None, all_nodes)
                                                if jump_node:
                                                    final_target = jump_node['name']
                                                else:
                                                    target = param_target_page or param_target_flow
                                                    final_target = f"page_{target[:8]}"
                                                self._safe_append_edge(edges, param_cond_name, final_target, "condition", param_cond_id, all_nodes)
                                else:
                                    # 没有param_condition，直接从CODE连到目标
                                    if target_page_id or target_flow_id:
                                        jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                                        if jump_node:
                                            final_target = jump_node['name']
                                        else:
                                            target = target_page_id or target_flow_id
                                            final_target = f"page_{target[:8]}"
                                        self._safe_append_edge(edges, code_name, final_target, "default", None, all_nodes)
                        elif target_page_id or target_flow_id:
                            # 无参数提取：semantic_judgment → transition_code_node → transition_text_nodes → 目标页面
                            transition_code_node = branch.get('transition_code_node')
                            transition_text_nodes = branch.get('transition_text_nodes', [])
                            
                            jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                            if jump_node:
                                final_target = jump_node['name']
                            else:
                                target = target_page_id or target_flow_id
                                final_target = f"page_{target[:8]}"
                            # writed by senlin.deng 2026-02-06
                            # 修复当存在 setParameterActions 时，应该先连接到 Set Parameters 节点，再连接到 jumpto 节点
                            # 构建连接链：semantic_judgment → transition_code_node → transition_text_nodes → final_target
                            current_source = semantic_node_name
                            
                            # 1. 先连接 code 节点（setParameterActions）- 必须在 text 节点之前
                            if transition_code_node:
                                self._safe_append_edge(edges, current_source, transition_code_node, "condition", branch_condition_id, all_nodes)
                                current_source = transition_code_node  # 更新当前源节点
                            
                            # 2. 连接 text 节点链（staticUserResponse）- 在 code 节点之后
                            if transition_text_nodes:
                                for idx, text_node_name in enumerate(transition_text_nodes):
                                    if idx == 0:
                                        # 第一个 text 节点：从当前源节点（可能是 semantic_node 或 code 节点）连接
                                        self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                    else:
                                        # 后续 text 节点：从前一个 text 节点连接
                                        self._safe_append_edge(edges, transition_text_nodes[idx-1], text_node_name, "default", None, all_nodes)
                                    current_source = text_node_name  # 更新当前源节点
                            
                            # 3. 连接到最终目标
                            if transition_code_node or transition_text_nodes:
                                # 有中间节点时，使用 default 连接
                                self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                            else:
                                # 没有中间节点时，使用 condition 连接
                                self._safe_append_edge(edges, current_source, final_target, "condition", branch_condition_id, all_nodes)
                        elif branch.get('logical_operator') == 'other':
                            # Fallback分支
                            # writed by senlin.deng 2026-01-18
                            # 新增：如果存在 routeGroupsTransitionEvents，Fallback分支应该连接到 Route Groups Intent Recognition
                            # writed by senlin.deng 2026-01-21
                            # 修复：当 has_pure_conditions=True 时，需要使用 _direct_target 属性来创建边
                            if has_pure_conditions:
                                # 混合路由：Fallback 连接到 Pure Condition Node（通过 _direct_target 属性）
                                direct_target = branch.get('_direct_target')
                                if direct_target:
                                    self._safe_append_edge(edges, semantic_node_name, direct_target, "condition", branch_condition_id, all_nodes)
                                    logger.debug(f"    🔀 混合路由: semantic_judgment Fallback → {direct_target}")
                                else:
                                    # 如果没有 _direct_target，从 semantic_judgment_node 获取 pure_condition_node
                                    pure_condition_node = semantic_judgment_node.get('_pure_condition_node')
                                    if pure_condition_node:
                                        self._safe_append_edge(edges, semantic_node_name, pure_condition_node, "condition", branch_condition_id, all_nodes)
                                        logger.debug(f"    🔀 混合路由: semantic_judgment Fallback → {pure_condition_node}")
                            elif route_groups_entry_node:
                                # 有 routeGroupsTransitionEvents：Fallback分支 → Route Groups Intent Recognition
                                self._safe_append_edge(edges, semantic_node_name, route_groups_entry_node, "condition", branch_condition_id, all_nodes)
                                logger.info(f"    🔀 routeGroups(兼容): Fallback → Route Groups Intent Recognition ({route_groups_entry_node})")
                            elif fallback_text_node:
                                # 普通情况：Fallback分支 → fallback_text
                                self._safe_append_edge(edges, semantic_node_name, fallback_text_node, "condition", branch_condition_id, all_nodes)

                    print(f"    ✅ 版本2边生成完成: semantic_judgment节点 '{semantic_node_name}' 连接了 {len(semantic_branches)} 个分支")
            
            # **Semantic NER 节点边生成逻辑**
            # Semantic NER 节点直接连接在 Intent Recognition 的语义判断节点之后
            # 流程：semantic_judgment(意图识别) → NER_semantic(实体识别) → Code(设置参数值) → 条件路由/目标页面
            if ner_semantic_nodes:
                logger.debug(f"  🔄 生成 Semantic NER 节点的边: {len(ner_semantic_nodes)} 个 NER Semantic 节点")
                
                # write by senlin.deng 2026-02-05: 跟踪已创建的 mixed → condition 边，避免重复
                created_mixed_to_condition_edges = set()
                
                # 为每个 NER Semantic 节点生成边
                for ner_semantic_node in ner_semantic_nodes:
                    ner_semantic_name = ner_semantic_node.get('name')
                    ner_param_name = ner_semantic_node.get('_ner_param_name')
                    ner_condition_branches = ner_semantic_node.get('_condition_branches', [])
                    from_condition_id = ner_semantic_node.get('from_semantic_condition_id')
                    
                    # semantic_judgment → NER Semantic（通过 condition_id 边连接）
                    if from_condition_id and semantic_judgment_node:
                        self._safe_append_edge(edges, semantic_judgment_node.get('name'), ner_semantic_name, "condition", from_condition_id, all_nodes)
                    
                    # NER Semantic → Code（根据 condition_branches）
                    for branch in ner_condition_branches:
                        branch_condition_id = branch.get('condition_id')
                        code_node_name = branch.get('_code_node_name')
                        
                        if code_node_name:
                            # NER Semantic → Code
                            self._safe_append_edge(edges, ner_semantic_name, code_node_name, "condition", branch_condition_id, all_nodes)
                            
                            # Code → 下一个节点（条件路由或目标页面）
                            # write by senlin.deng 2026-02-05: 修复 Semantic NER 模式与 LLM 模式一致
                            # 当存在 Combined Mixed Condition Check 节点时，多个并行 code 节点应该汇聚连接到该节点
                            next_condition_node = branch.get('_next_condition_node')
                            if next_condition_node:
                                # 检查是否有混合条件 code 节点（Combined Mixed Condition Check）
                                # 与 LLM 模式保持一致：code → mixed_node（如果有）→ param_condition
                                mixed_node_name = param_condition_mixed_nodes.get(next_condition_node)
                                if mixed_node_name:
                                    # Code → Combined Mixed Condition Check（多个 code 节点汇聚到同一个 mixed 节点）
                                    self._safe_append_edge(edges, code_node_name, mixed_node_name, "default", None, all_nodes)
                                    # Combined Mixed Condition Check → 条件路由节点（只创建一次）
                                    edge_key = (mixed_node_name, next_condition_node)
                                    if edge_key not in created_mixed_to_condition_edges:
                                        self._safe_append_edge(edges, mixed_node_name, next_condition_node, "default", None, all_nodes)
                                        created_mixed_to_condition_edges.add(edge_key)
                                    logger.debug(f"    🔀 NER Semantic: {code_node_name} → {mixed_node_name} → {next_condition_node}")
                                else:
                                    # Code → 条件路由节点
                                    self._safe_append_edge(edges, code_node_name, next_condition_node, "default", None, all_nodes)
                            else:
                                # 没有参数条件：Code 直接连接到目标页面 / 纯条件路由入口
                                # write by senlin.deng 2026-02-04: 修复需要处理 transition_code_node 和 transition_text_nodes
                                direct_target = branch.get('_direct_target')
                                if direct_target:
                                    target_page_id = direct_target.get('target_page_id')
                                    target_flow_id = direct_target.get('target_flow_id')
                                    # write by senlin.deng 2026-02-04: 获取 transition 节点信息
                                    transition_code_node = direct_target.get('transition_code_node')
                                    transition_text_nodes = direct_target.get('transition_text_nodes', [])
                                    
                                    jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                                    
                                    # 如果 target_page_id 存在，检查该 page 是否有跳转到 flow 的逻辑
                                    if target_page_id and not jump_node and page_id_map:
                                        target_page_flow_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                                        if target_page_flow_info and target_page_flow_info.get('is_always_true'):
                                            target_flow_id_from_page = target_page_flow_info.get('target_flow_id')
                                            if target_flow_id_from_page:
                                                page_data = page_id_map.get(target_page_id)
                                                page_display_name_target = ''
                                                if page_data:
                                                    if 'value' in page_data:
                                                        page_display_name_target = page_data.get('value', {}).get('displayName', '')
                                                    else:
                                                        page_display_name_target = page_data.get('displayName', '')
                                                jump_node = self._find_jump_node_for_target(target_flow_id_from_page, None, jump_nodes, page_display_name_target, all_nodes)
                                    
                                    if jump_node:
                                        final_target = jump_node['name']
                                    else:
                                        target = target_page_id or target_flow_id
                                        final_target = f"page_{target[:8]}"
                                    
                                    # write by senlin.deng 2026-02-04: 构建连接链
                                    # 链路：code_node → transition_code(可选) → transition_text(可选) → final_target
                                    current_source = code_node_name
                                    
                                    # 1. 连接 transition_code 节点（setParameterActions）
                                    if transition_code_node:
                                        self._safe_append_edge(edges, current_source, transition_code_node, "default", None, all_nodes)
                                        current_source = transition_code_node
                                    
                                    # 2. 连接 transition_text 节点链（staticUserResponse）
                                    if transition_text_nodes:
                                        for idx, text_node_name in enumerate(transition_text_nodes):
                                            if idx == 0:
                                                self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                            else:
                                                self._safe_append_edge(edges, transition_text_nodes[idx-1], text_node_name, "default", None, all_nodes)
                                            current_source = text_node_name
                                    
                                    # 3. 连接到最终目标
                                    self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                                else:
                                    # write by senlin.deng 2026-02-04: 修复混合路由场景
                                    # 当 NER Code 节点没有 _direct_target 且没有 _next_condition_node 时
                                    # 这是一个配置问题，应该记录警告
                                    # 注意：不应该使用 semantic_judgment 的 Fallback 分支的目标
                                    # 因为 Fallback 分支是用于纯条件路由的，不是 Intent 路由的
                                    logger.warning(f"    ⚠️ NER Code 节点 '{code_node_name}' 没有设置后续连接 (_next_condition_node 或 _direct_target)")
                    
                    logger.debug(f"    ✅ NER Semantic 节点 '{ner_semantic_name}' 边生成完成")
                
                # 为 NER 条件路由节点生成边
                # write by senlin.deng 2026-02-04: 修复与 LLM 版本 param_condition 边生成保持一致
                # 需要处理：transition_code_node、transition_text_nodes、mixed_condition_code_node
                for ner_cond_node in ner_condition_nodes:
                    ner_cond_name = ner_cond_node.get('name')
                    if_else_conditions = ner_cond_node.get('if_else_conditions', [])
                    
                    # 获取合并的混合条件 code 节点（用于汇聚多个 NER code 节点到 param_condition 之前）
                    combined_mixed_code_node_name = ner_cond_node.get('combined_mixed_condition_code_node')
                    
                    for branch in if_else_conditions:
                        condition_id = branch.get('condition_id')
                        target_page_id = branch.get('target_page_id')
                        target_flow_id = branch.get('target_flow_id')
                        
                        # write by senlin.deng 2026-02-04: 获取 transition 节点信息
                        transition_code_node = branch.get('transition_code_node')
                        transition_text_nodes = branch.get('transition_text_nodes', [])
                        mixed_condition_code_node = branch.get('mixed_condition_code_node')
                        is_always_true = branch.get('is_always_true', False)
                        
                        # 检查是否是 fallback 分支
                        if branch.get('target_fallback_text') or branch.get('logical_operator') == 'other':
                            # Else/Fallback 分支 → fallback
                            # writed by senlin.deng 2026-02-04: 支持 routeGroups
                            if route_groups_entry_node:
                                self._safe_append_edge(edges, ner_cond_name, route_groups_entry_node, "condition", condition_id, all_nodes)
                                logger.info(f"    🔀 routeGroups(NER): Fallback → Route Groups Intent Recognition ({route_groups_entry_node})")
                            elif fallback_text_node:
                                self._safe_append_edge(edges, ner_cond_name, fallback_text_node, "condition", condition_id, all_nodes)
                        elif target_page_id or target_flow_id:
                            # 条件分支 → 目标页面（需要处理中间节点链）
                            jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                            
                            # 如果 target_page_id 存在，检查该 page 是否有跳转到 flow 的逻辑
                            if target_page_id and not jump_node and page_id_map:
                                target_page_flow_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                                if target_page_flow_info and target_page_flow_info.get('is_always_true'):
                                    target_flow_id_from_page = target_page_flow_info.get('target_flow_id')
                                    if target_flow_id_from_page:
                                        page_data = page_id_map.get(target_page_id)
                                        page_display_name_target = ''
                                        if page_data:
                                            if 'value' in page_data:
                                                page_display_name_target = page_data.get('value', {}).get('displayName', '')
                                            else:
                                                page_display_name_target = page_data.get('displayName', '')
                                        jump_node = self._find_jump_node_for_target(target_flow_id_from_page, None, jump_nodes, page_display_name_target, all_nodes)
                            
                            if jump_node:
                                final_target = jump_node['name']
                            else:
                                target = target_page_id or target_flow_id
                                final_target = f"page_{target[:8]}"
                            
                            # write by senlin.deng 2026-02-04: 构建连接链
                            # 链路：ner_cond → mixed_code(可选) → transition_code(可选) → transition_text(可选) → final_target
                            current_source = ner_cond_name
                            first_edge_type = "condition" if not is_always_true else "default"
                            first_edge_condition_id = condition_id if not is_always_true else None
                            
                            # 1. 如果有混合条件 code 节点，先连接到它
                            if mixed_condition_code_node:
                                self._safe_append_edge(edges, current_source, mixed_condition_code_node, first_edge_type, first_edge_condition_id, all_nodes)
                                current_source = mixed_condition_code_node
                                first_edge_type = "default"
                                first_edge_condition_id = None
                            
                            # 2. 连接 transition_code 节点（setParameterActions）
                            if transition_code_node:
                                self._safe_append_edge(edges, current_source, transition_code_node, first_edge_type, first_edge_condition_id, all_nodes)
                                current_source = transition_code_node
                                first_edge_type = "default"
                                first_edge_condition_id = None
                            
                            # 3. 连接 transition_text 节点链（staticUserResponse）
                            if transition_text_nodes:
                                for idx, text_node_name in enumerate(transition_text_nodes):
                                    if idx == 0:
                                        self._safe_append_edge(edges, current_source, text_node_name, first_edge_type, first_edge_condition_id, all_nodes)
                                    else:
                                        self._safe_append_edge(edges, transition_text_nodes[idx-1], text_node_name, "default", None, all_nodes)
                                    current_source = text_node_name
                                first_edge_type = "default"
                                first_edge_condition_id = None
                            
                            # 4. 连接到最终目标
                            # 如果还没有生成任何中间边，使用 condition 连接
                            if first_edge_type == "condition":
                                self._safe_append_edge(edges, current_source, final_target, "condition", condition_id, all_nodes)
                            else:
                                self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                    
                    logger.debug(f"    ✅ NER 条件路由节点 '{ner_cond_name}' 边生成完成")
            
            # **版本1：基础流程 (kb + code + condition)**
            # 1. 基础流程：previous_node → capture → kb → extract_intent_code
            elif previous_node and capture_node:
                self._safe_append_edge(edges, previous_node, capture_node, "default", None, all_nodes)
            
            if capture_node and kb_node and not semantic_judgment_node:
                self._safe_append_edge(edges, capture_node, kb_node, "default", None, all_nodes)
            
            if kb_node and extract_intent_code and not semantic_judgment_node:
                self._safe_append_edge(edges, kb_node, extract_intent_code, "default", None, all_nodes)
            
            # 2. 根据情况连接后续节点
            # 情况1：有intent无parameter - extract_intent_code → intent_routing_condition
            if intent_routing_condition:
                # 连接到intent_routing_condition
                if extract_intent_code:
                    self._safe_append_edge(edges, extract_intent_code, intent_routing_condition, "default", None, all_nodes)
                
                # intent_routing_condition的分支 → target_pages 或 fallback
                intent_routing_node_obj = next((n for n in intent_nodes if n['name'] == intent_routing_condition), None)
                if intent_routing_node_obj:
                    if_else_conditions = intent_routing_node_obj.get('if_else_conditions', [])
                    for branch in if_else_conditions:
                        condition_id = branch.get('condition_id')
                        target_page_id = branch.get('target_page_id')
                        target_flow_id = branch.get('target_flow_id')
                        
                        if condition_id and condition_id.startswith('fallback_condition'):
                            # fallback分支
                            # writed by senlin.deng 2026-01-18
                            # 新增：如果存在 routeGroupsTransitionEvents，Fallback分支应该连接到 Route Groups Intent Recognition
                            if route_groups_entry_node:
                                # 有 routeGroupsTransitionEvents：Fallback分支 → Route Groups Intent Recognition
                                self._safe_append_edge(edges, intent_routing_condition, route_groups_entry_node, "condition", condition_id, all_nodes)
                                logger.info(f"    🔀 routeGroups(intent_routing): Fallback → Route Groups Intent Recognition ({route_groups_entry_node})")
                            elif fallback_text_node:
                                # 普通情况：Fallback分支 → fallback_text
                                self._safe_append_edge(edges, intent_routing_condition, fallback_text_node, "condition", condition_id, all_nodes)
                        elif target_page_id or target_flow_id:
                            # 检查是否需要跳转到另一个 flow
                            jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)

                            # 检查是否有 transition_code_node
                            transition_code_node = branch.get('transition_code_node')
                            
                            # 检查是否为始终为 true 的条件（从 transition_info 中获取）
                            is_always_true = branch.get('is_always_true', False)

                            # 如果 target_page_id 存在，检查该 page 是否有跳转到 flow 的逻辑（condition 为 true）
                            if target_page_id and not jump_node and page_id_map:
                                target_page_flow_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                                if target_page_flow_info and target_page_flow_info.get('is_always_true'):
                                    # 该 page 有跳转到 flow 的逻辑，且 condition 为 true，直接跳转到 flow
                                    target_flow_id_from_page = target_page_flow_info.get('target_flow_id')
                                    if target_flow_id_from_page:
                                        # 获取 page 的 displayName
                                        page_data = page_id_map.get(target_page_id)
                                        page_display_name = ''
                                        if page_data:
                                            if 'value' in page_data:
                                                page_display_name = page_data.get('value', {}).get('displayName', '')
                                            else:
                                                page_display_name = page_data.get('displayName', '')
                                        
                                        # 查找或创建对应的 jump 节点（传入 page_display_name 用于精确匹配，同时检查 all_nodes）
                                        jump_node = self._find_jump_node_for_target(target_flow_id_from_page, None, jump_nodes, page_display_name, all_nodes)
                                        if not jump_node:
                                            # 如果找不到 jump 节点，创建一个新的
                                            # 直接使用 jump_to_{page_id前8位}，不添加计数器
                                            jump_node_name = f"jump_to_{target_page_id[:8]}"
                                            
                                            # 确保节点名称唯一（检查 jump_nodes、当前 nodes 列表和 all_nodes）
                                            existing_names = {n.get('name') for n in jump_nodes}
                                            existing_names.update({n.get('name') for n in nodes})
                                            if all_nodes:
                                                existing_names.update({n.get('name') for n in all_nodes})
                                            
                                            # 如果名称已存在，检查是否是相同的 jump 节点
                                            if jump_node_name in existing_names:
                                                if all_nodes:
                                                    for node in all_nodes:
                                                        if node.get('type') == 'jump' and node.get('name') == jump_node_name:
                                                            jump_flow_uuid = node.get('jump_flow_uuid')
                                                            if jump_flow_uuid == target_flow_id_from_page:
                                                                jump_node = node
                                                                jump_node_name = node.get('name')
                                                                break
                                            
                                            # 如果还没有找到匹配的节点，创建新的
                                            if not jump_node:
                                                # title 使用 page_display_name 或 flow_id，name 用于唯一性
                                                if page_display_name:
                                                    title = f"jump_to_{page_display_name}"
                                                else:
                                                    title = f"jump_to_{target_flow_id_from_page[:8]}"
                                                
                                                jump_node = {
                                                    "type": "jump",
                                                    "name": jump_node_name,
                                                    "title": title,  # title 使用 displayName，name 用于唯一性
                                                    "jump_type": "flow",
                                                    "jump_robot_id": "",
                                                    "jump_robot_name": "",
                                                    "jump_carry_history_number": 5,
                                                    "jump_flow_name": page_display_name if page_display_name else "",
                                                    "jump_flow_uuid": target_flow_id_from_page,
                                                    "jump_carry_userinput": True,
                                                    "transition_info": {
                                                        "target_page_id": None,
                                                        "target_flow_id": target_flow_id_from_page,
                                                        "page_display_name": page_display_name
                                                    }
                                                }
                                                jump_nodes.append(jump_node)
                                                print(f'    ✅ Created jump node for targetPageId page: {jump_node_name} -> {target_flow_id_from_page[:8]}...')
                                            else:
                                                print(f'    ✅ Reusing existing jump node: {jump_node_name} -> {target_flow_id_from_page[:8]}...')

                            if jump_node:
                                # 跳转到另一个 flow
                                final_target = jump_node['name']
                            else:
                                # 跳转到 page
                                target_page = target_page_id or target_flow_id
                                final_target = f"page_{target_page[:8]}"

                            # 获取 transition_text_nodes
                            transition_text_nodes = branch.get('transition_text_nodes', [])
                            
                            # 构建连接链：condition → code节点(setParameterActions) → text节点链(staticUserResponse) → final_target
                            # 注意：setParameterActions 应该在 staticUserResponse 之前执行
                            current_source = intent_routing_condition
                            
                            # 1. 先连接 code 节点（setParameterActions）- 必须在 text 节点之前
                            if transition_code_node:
                                if is_always_true:
                                    self._safe_append_edge(edges, current_source, transition_code_node, "default", None, all_nodes)
                                else:
                                    self._safe_append_edge(edges, current_source, transition_code_node, "condition", condition_id, all_nodes)
                                current_source = transition_code_node  # 更新当前源节点
                            
                            # 2. 连接 text 节点链（staticUserResponse）- 在 code 节点之后
                            if transition_text_nodes:
                                for idx, text_node_name in enumerate(transition_text_nodes):
                                    if idx == 0:
                                        # 第一个 text 节点：从当前源节点（可能是 condition 或 code 节点）连接
                                        self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                    else:
                                        # 后续 text 节点：从前一个 text 节点连接
                                        self._safe_append_edge(edges, transition_text_nodes[idx-1], text_node_name, "default", None, all_nodes)
                                    current_source = text_node_name  # 更新当前源节点
                            
                            # 3. 连接到最终目标
                            if is_always_true:
                                self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                            else:
                                # 只有在没有 text 节点和 code 节点时，才使用 condition 连接
                                if not transition_text_nodes and not transition_code_node:
                                    self._safe_append_edge(edges, current_source, final_target, "condition", condition_id, all_nodes)
                                else:
                                    # 有中间节点时，使用 default 连接
                                    self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
            
            # 情况4：intent单独抽取+多个condition分支（新增）
            # 特征：必须同时有以下4个节点（缺一不可）：
            # 1. check_intent 节点 (title: "Check if Intent is")
            # 2. llm_extract_param节点 (title: "Extract Parameters from User Input" - 注意区分情况2的"for")
            # 3. parse_params节点 (title: "Parse Parameters from LLM" - 注意区分情况2的"for")
            # 4. param_condition节点 (title: "Route by Parameter Value" - 注意区分情况2的"Parameter Routing for")
            check_intent_node_p4 = None  # pattern 4 专用变量，避免与情况2混淆
            llm_extract_param_node = None
            parse_params_node = None
            param_condition_node_p4 = None  # pattern 4 专用变量
            
            for node in intent_nodes:
                node_type = node.get('type')
                node_name = node.get('name')
                title = node.get('title', '')
                
                # pattern 4特征：使用更精确的title匹配
                if node_type == 'condition' and 'Check if Intent is' in title:
                    check_intent_node_p4 = node
                # pattern 4: "Extract Parameters from User Input" (区分情况2的"Extract Parameters for")
                elif node_type == 'llmVariableAssignment' and 'Extract Parameters from User Input' in title:
                    llm_extract_param_node = node_name
                # pattern 4: "Parse Parameters from LLM" (区分情况2的"Parse Parameters for")
                elif node_type == 'code' and 'Parse Parameters from LLM' in title:
                    parse_params_node = node_name
                # pattern 4: "Route by Parameter Value" (区分情况2的"Parameter Routing for")
                elif node_type == 'condition' and 'Route by Parameter Value' in title:
                    param_condition_node_p4 = node
            
            # 只有同时满足所有4个条件才是pattern 4
            has_pattern4_nodes = check_intent_node_p4 and llm_extract_param_node and parse_params_node and param_condition_node_p4
            
            if has_pattern4_nodes:
                # 这是新情况4：intent单独抽取+多个condition分支
                print(f"  📝 Detected and processing pattern 4: intent extraction + separate conditions")
                
                # 连接：extract_intent_code → check_intent_node_p4
                if extract_intent_code:
                    self._safe_append_edge(edges, extract_intent_code, check_intent_node_p4['name'], "default", None, all_nodes)
                
                # 处理check_intent_node_p4的分支
                if_else_conditions = check_intent_node_p4.get('if_else_conditions', [])
                for branch in if_else_conditions:
                    condition_id = branch.get('condition_id')
                    target_node_ref = branch.get('target_node')
                    
                    if target_node_ref:
                        self._safe_append_edge(edges, check_intent_node_p4['name'], target_node_ref, "condition", condition_id, all_nodes)
                
                # 连接：llm → code → param_condition（不需要capture2）
                if llm_extract_param_node and parse_params_node:
                    print(f"  🔗 Adding edge: {llm_extract_param_node} → {parse_params_node}")
                    self._safe_append_edge(edges, llm_extract_param_node, parse_params_node, "default", None, all_nodes)
                
                if parse_params_node and param_condition_node_p4:
                    print(f"  🔗 Adding edge: {parse_params_node} → {param_condition_node_p4['name']}")
                    self._safe_append_edge(edges, parse_params_node, param_condition_node_p4['name'], "default", None, all_nodes)
                
                # 处理param_condition_node_p4的分支 → target_pages或fallback
                if param_condition_node_p4:
                    param_name = param_condition_node_p4['name']
                    if_else_conditions = param_condition_node_p4.get('if_else_conditions', [])
                    
                    for branch in if_else_conditions:
                        condition_id = branch.get('condition_id')
                        target_page_id = branch.get('target_page_id')
                        target_flow_id = branch.get('target_flow_id')
                        target_node_ref = branch.get('target_node')
                        
                        if target_node_ref:
                            # 直接跳转到指定节点（如fallback）
                            self._safe_append_edge(edges, param_name, target_node_ref, "condition", condition_id, all_nodes)
                        elif target_page_id or target_flow_id:
                            # 跳转到page或flow
                            jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                            
                            # 如果 target_page_id 存在，检查该 page 是否有跳转到 flow 的逻辑
                            if target_page_id and not jump_node and page_id_map:
                                target_page_flow_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                                if target_page_flow_info and target_page_flow_info.get('is_always_true'):
                                    target_flow_id_from_page = target_page_flow_info.get('target_flow_id')
                                    if target_flow_id_from_page:
                                        page_data = page_id_map.get(target_page_id)
                                        page_display_name = ''
                                        if page_data:
                                            if 'value' in page_data:
                                                page_display_name = page_data.get('value', {}).get('displayName', '')
                                            else:
                                                page_display_name = page_data.get('displayName', '')
                                        
                                        jump_node = self._find_jump_node_for_target(target_flow_id_from_page, None, jump_nodes, page_display_name, all_nodes)
                            
                            if jump_node:
                                final_target = jump_node['name']
                            else:
                                # 检查目标页面是否只是跳转中转站（只有 jump 到其他 flow）
                                target_is_relay = False
                                if target_page_id and page_id_map:
                                    relay_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                                    if relay_info and relay_info.get('is_always_true'):
                                        relay_flow_id = relay_info.get('target_flow_id')
                                        if relay_flow_id:
                                            target_is_relay = True
                                            # 获取目标页面的 displayName
                                            relay_page_data = page_id_map.get(target_page_id)
                                            relay_display_name = ''
                                            if relay_page_data:
                                                if 'value' in relay_page_data:
                                                    relay_display_name = relay_page_data.get('value', {}).get('displayName', '')
                                                else:
                                                    relay_display_name = relay_page_data.get('displayName', '')
                                            
                                            # 查找或创建 jump 节点
                                            relay_jump = self._find_jump_node_for_target(relay_flow_id, None, jump_nodes, relay_display_name, all_nodes)
                                            if relay_jump:
                                                final_target = relay_jump['name']
                                            else:
                                                # 创建新的 jump 节点
                                                relay_jump_name = f"jump_to_{target_page_id[:8]}"
                                                existing_names = {n.get('name') for n in jump_nodes}
                                                existing_names.update({n.get('name') for n in nodes})
                                                if all_nodes:
                                                    existing_names.update({n.get('name') for n in all_nodes})
                                                
                                                if relay_jump_name not in existing_names:
                                                    relay_jump = {
                                                        "type": "jump",
                                                        "name": relay_jump_name,
                                                        "title": f"jump_to_{relay_display_name}" if relay_display_name else f"jump_to_{relay_flow_id[:8]}",
                                                        "jump_type": "flow",
                                                        "jump_robot_id": "",
                                                        "jump_robot_name": "",
                                                        "jump_carry_history_number": 5,
                                                        "jump_flow_name": relay_display_name if relay_display_name else "",
                                                        "jump_flow_uuid": relay_flow_id,
                                                        "jump_carry_userinput": True,
                                                        "transition_info": {
                                                            "target_page_id": None,
                                                            "target_flow_id": relay_flow_id,
                                                            "page_display_name": relay_display_name
                                                        }
                                                    }
                                                    jump_nodes.append(relay_jump)
                                                    nodes.append(relay_jump)
                                                    print(f'    ✅ Created jump node for relay page (case1): {relay_jump_name}')
                                                final_target = relay_jump_name
                                
                                if not target_is_relay:
                                    # 目标页面不是跳转中转站，使用 page_xxx
                                    target_page = target_page_id or target_flow_id
                                    final_target = f"page_{target_page[:8]}"
                            
                            # 获取 transition_text_nodes 和 transition_code_node
                            transition_text_nodes = branch.get('transition_text_nodes', [])
                            transition_code_node = branch.get('transition_code_node')
                            
                            # 构建连接链：param → code节点(setParameterActions) → text节点链(staticUserResponse) → final_target
                            # 注意：setParameterActions 应该在 staticUserResponse 之前执行
                            current_source = param_name
                            
                            # 1. 先连接 code 节点（setParameterActions）- 必须在 text 节点之前
                            if transition_code_node:
                                self._safe_append_edge(edges, current_source, transition_code_node, "condition", condition_id, all_nodes)
                                current_source = transition_code_node  # 更新当前源节点
                            
                            # 2. 连接 text 节点链（staticUserResponse）- 在 code 节点之后
                            if transition_text_nodes:
                                for idx, text_node_name in enumerate(transition_text_nodes):
                                    if idx == 0:
                                        # 第一个 text 节点：从当前源节点（可能是 param 或 code 节点）连接
                                        self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                    else:
                                        # 后续 text 节点：从前一个 text 节点连接
                                        self._safe_append_edge(edges, transition_text_nodes[idx-1], text_node_name, "default", None, all_nodes)
                                    current_source = text_node_name  # 更新当前源节点
                            
                            # 3. 连接到最终目标
                            if not transition_text_nodes and not transition_code_node:
                                # 没有中间节点，直接连接
                                self._safe_append_edge(edges, current_source, final_target, "condition", condition_id, all_nodes)
                            else:
                                # 有中间节点，使用 default 连接
                                self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
            
            # 情况2：有intent有parameter - 链式结构  
            # 注意：如果已经是pattern 4，则跳过pattern 2的处理
            elif first_intent_check and not has_pattern4_nodes:
                # 连接到第一个intent_check
                if extract_intent_code:
                    edges.append({
                        "source_node": extract_intent_code,
                        "target_node": first_intent_check,
                        "connection_type": "default"
                    })
                
                # 为每个intent_check创建edges
                for intent_check_node in intent_check_nodes:
                    intent_check_name = intent_check_node['name']
                    if_else_conditions = intent_check_node.get('if_else_conditions', [])
                    
                    for branch in if_else_conditions:
                        condition_id = branch.get('condition_id')
                        target_node_ref = branch.get('target_node')
                        target_page_id = branch.get('target_page_id')
                        target_flow_id = branch.get('target_flow_id')
                        
                        if target_node_ref:
                            mixed_node_name = param_condition_mixed_nodes.get(target_node_ref)
                            if mixed_node_name:
                                self._safe_append_edge(edges, intent_check_name, mixed_node_name, "condition", condition_id, all_nodes)
                                self._safe_append_edge(edges, mixed_node_name, target_node_ref, "default", None, all_nodes)
                            else:
                                self._safe_append_edge(edges, intent_check_name, target_node_ref, "condition", condition_id, all_nodes)
                        elif target_page_id or target_flow_id:
                            # 直接跳转到 page（当 skip_param_condition=True 且没有变量提取时）
                            jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                            transition_code_node = branch.get('transition_code_node')
                            transition_text_nodes = branch.get('transition_text_nodes', [])
                            
                            # 如果 target_page_id 存在，检查该 page 是否有跳转到 flow 的逻辑
                            if target_page_id and not jump_node and page_id_map:
                                target_page_flow_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                                if target_page_flow_info and target_page_flow_info.get('is_always_true'):
                                    target_flow_id_from_page = target_page_flow_info.get('target_flow_id')
                                    if target_flow_id_from_page:
                                        page_data = page_id_map.get(target_page_id)
                                        page_display_name_target = ''
                                        if page_data:
                                            if 'value' in page_data:
                                                page_display_name_target = page_data.get('value', {}).get('displayName', '')
                                            else:
                                                page_display_name_target = page_data.get('displayName', '')
                                        jump_node = self._find_jump_node_for_target(target_flow_id_from_page, None, jump_nodes, page_display_name_target, all_nodes)
                            
                            if jump_node:
                                final_target = jump_node['name']
                            else:
                                target_page = target_page_id or target_flow_id
                                final_target = f"page_{target_page[:8]}"
                            
                            # write by senlin.deng 2026-01-20
                            # 修复：在直接跳转flow的节点链的情况下，不生成beforeTransition相关节点的问题
                            # 如果有 beforeTransition 节点，构建连接链：intent_check → code(setParameter) → text → final_target
                            current_source = intent_check_name
                            if transition_code_node:
                                self._safe_append_edge(edges, current_source, transition_code_node, "condition", condition_id, all_nodes)
                                current_source = transition_code_node
                            
                            if transition_text_nodes:
                                for idx, text_node_name in enumerate(transition_text_nodes):
                                    if idx == 0 and not transition_code_node:
                                        self._safe_append_edge(edges, current_source, text_node_name, "condition", condition_id, all_nodes)
                                    else:
                                        self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                    current_source = text_node_name
                            
                            if transition_code_node or transition_text_nodes:
                                self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                            else:
                                self._safe_append_edge(edges, current_source, final_target, "condition", condition_id, all_nodes)
                            print(f"    ✅ Added direct edge: {intent_check_name} → {final_target} (skip param_condition, no var extraction)")
                
                # LLM → CODE的映射和edges
                for llm_name, code_name in llm_to_code.items():
                    self._safe_append_edge(edges, llm_name, code_name, "default", None, all_nodes)
                    
                    # CODE → param_condition 或 CODE → direct_target
                    # 先检查 CODE 节点是否有 _direct_target 标记
                    code_node_obj = next((n for n in intent_nodes if n['name'] == code_name), None)
                    if code_node_obj and code_node_obj.get('_direct_target'):
                        # 有 _direct_target 标记，直接连接到目标 page，跳过 param_condition
                        direct_target = code_node_obj.get('_direct_target')
                        target_page_id = direct_target.get('target_page_id')
                        target_flow_id = direct_target.get('target_flow_id')
                        transition_code_node = code_node_obj.get('_direct_transition_code_node')
                        transition_text_nodes = code_node_obj.get('_direct_transition_text_nodes', [])
                        
                        # 检查是否需要跳转到另一个 flow
                        jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                        
                        # 如果 target_page_id 存在，检查该 page 是否有跳转到 flow 的逻辑
                        if target_page_id and not jump_node and page_id_map:
                            target_page_flow_info = self._check_target_page_has_flow_transition(target_page_id, page_id_map)
                            if target_page_flow_info and target_page_flow_info.get('is_always_true'):
                                target_flow_id_from_page = target_page_flow_info.get('target_flow_id')
                                if target_flow_id_from_page:
                                    page_data = page_id_map.get(target_page_id)
                                    page_display_name_target = ''
                                    if page_data:
                                        if 'value' in page_data:
                                            page_display_name_target = page_data.get('value', {}).get('displayName', '')
                                        else:
                                            page_display_name_target = page_data.get('displayName', '')
                                    jump_node = self._find_jump_node_for_target(target_flow_id_from_page, None, jump_nodes, page_display_name_target, all_nodes)
                        
                        if jump_node:
                            final_target = jump_node['name']
                        else:
                            target_page = target_page_id or target_flow_id
                            final_target = f"page_{target_page[:8]}"
                        
                        current_source = code_name
                        if transition_code_node:
                            self._safe_append_edge(edges, current_source, transition_code_node, "default", None, all_nodes)
                            current_source = transition_code_node
                        
                        if transition_text_nodes:
                            for idx, text_node_name in enumerate(transition_text_nodes):
                                if idx == 0 and not transition_code_node:
                                    self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                else:
                                    self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                current_source = text_node_name
                        
                        self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                        print(f"    ✅ Added direct edge: {code_name} → {final_target} (skip param_condition)")
                    else:
                        # 正常情况：找到这个LLM对应的param_condition
                        # 通过title匹配：Extract Parameters for X → Parameter Routing for X
                        llm_node_obj = next((n for n in intent_nodes if n['name'] == llm_name), None)
                        if llm_node_obj:
                            llm_title = llm_node_obj.get('title', '')
                            if 'for ' in llm_title:
                                intent_name = llm_title.split('for ')[-1]
                                # 找对应的param_condition
                                for param_node in param_condition_nodes:
                                    param_title = param_node.get('title', '')
                                    if f'for {intent_name}' in param_title:
                                        param_node_name = param_node['name']
                                        mixed_node_name = param_condition_mixed_nodes.get(param_node_name)
                                        if mixed_node_name:
                                            self._safe_append_edge(edges, code_name, mixed_node_name, "default", None, all_nodes)
                                            self._safe_append_edge(edges, mixed_node_name, param_node_name, "default", None, all_nodes)
                                        else:
                                            self._safe_append_edge(edges, code_name, param_node_name, "default", None, all_nodes)
                                        break
                
                # param_condition的分支 → code(setParameter) → target_pages或fallback
                for param_node in param_condition_nodes:
                    param_name = param_node['name']
                    if_else_conditions = param_node.get('if_else_conditions', [])
                    
                    for branch in if_else_conditions:
                        condition_id = branch.get('condition_id')
                        target_page_id = branch.get('target_page_id')
                        target_flow_id = branch.get('target_flow_id')
                        transition_code_node = branch.get('transition_code_node')
                        
                        if target_page_id or target_flow_id:
                            # writed by senlin.deng 2026-01-22
                            # 修复：param_condition 分支应该直接连接到目标 page 的节点，
                            # 而不是跳过目标 page 直接连接到 jump 节点
                            # 让目标 page 自己处理后续的跳转逻辑
                            
                            # 只有当明确是跳转到 flow（有 target_flow_id 且无 target_page_id）时才使用 jump 节点
                            jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes, None, all_nodes)
                            
                            # 检查是否为始终为 true 的条件（从 branch 中获取）
                            is_always_true = branch.get('is_always_true', False)

                            if jump_node:
                                # 明确跳转到另一个 flow（有 target_flow_id 且无 target_page_id）
                                final_target = jump_node['name']
                            else:
                                # 跳转到 page - 直接使用 page_xxx 占位符，不跳过目标 page
                                target_page = target_page_id or target_flow_id
                                final_target = f"page_{target_page[:8]}"

                            # 获取 transition_text_nodes
                            transition_text_nodes = branch.get('transition_text_nodes', [])
                            
                            # 构建连接链：param → code节点(setParameterActions) → text节点链(staticUserResponse) → final_target
                            # 注意：setParameterActions 应该在 staticUserResponse 之前执行
                            current_source = param_name
                            
                            # 1. 先连接 code 节点（setParameterActions）- 必须在 text 节点之前
                            if transition_code_node:
                                if is_always_true:
                                    self._safe_append_edge(edges, current_source, transition_code_node, "default", None, all_nodes)
                                else:
                                    self._safe_append_edge(edges, current_source, transition_code_node, "condition", condition_id, all_nodes)
                                current_source = transition_code_node  # 更新当前源节点
                            
                            # 2. 连接 text 节点链（staticUserResponse）- 在 code 节点之后
                            if transition_text_nodes:
                                for idx, text_node_name in enumerate(transition_text_nodes):
                                    if idx == 0:
                                        # 第一个 text 节点：从当前源节点（可能是 param 或 code 节点）连接
                                        self._safe_append_edge(edges, current_source, text_node_name, "default", None, all_nodes)
                                    else:
                                        # 后续 text 节点：从前一个 text 节点连接
                                        self._safe_append_edge(edges, transition_text_nodes[idx-1], text_node_name, "default", None, all_nodes)
                                    current_source = text_node_name  # 更新当前源节点
                            
                            # 3. 连接到最终目标
                            if is_always_true:
                                self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                            else:
                                # 只有在没有 text 节点和 code 节点时，才使用 condition 连接
                                if not transition_text_nodes and not transition_code_node:
                                    self._safe_append_edge(edges, current_source, final_target, "condition", condition_id, all_nodes)
                                else:
                                    # 有中间节点时，使用 default 连接
                                    self._safe_append_edge(edges, current_source, final_target, "default", None, all_nodes)
                        elif condition_id and condition_id.startswith('param_fallback_'):
                            # fallback分支
                            # writed by senlin.deng 2026-01-18
                            # 新增：如果存在 routeGroupsTransitionEvents，Fallback分支应该连接到 Route Groups Intent Recognition
                            if route_groups_entry_node:
                                # 有 routeGroupsTransitionEvents：Fallback分支 → Route Groups Intent Recognition
                                self._safe_append_edge(edges, param_name, route_groups_entry_node, "condition", condition_id, all_nodes)
                                logger.info(f"    🔀 routeGroups(param): Fallback → Route Groups Intent Recognition ({route_groups_entry_node})")
                            elif fallback_text_node:
                                # 普通情况：Fallback分支 → fallback_text
                                self._safe_append_edge(edges, param_name, fallback_text_node, "condition", condition_id, all_nodes)
            
            # 3. fallback → capture (loop for retry)
            # All patterns should support fallback loop to allow user retry
            if fallback_text_node and capture_node:
                self._safe_append_edge(edges, fallback_text_node, capture_node, "default", None, all_nodes)
            
            print(f"  - Generated {len(intent_nodes)} intent-related nodes")
            print(f"  - Generated {len(condition_branches)} condition branches")
        
        # 4. 处理 jump 节点的边连接（在所有节点生成后）
        # 找到 page 的最后一个节点，然后连接到 jump 节点
        # 包括从 _generate_jump_nodes_for_page_transitions 生成的 jump 节点
        # 和从 _generate_global_transition_nodes 生成的 jump 节点（标记为 _needs_connection）
        all_jump_nodes = jump_nodes + [n for n in global_nodes if n.get('type') == 'jump' and n.get('_needs_connection')]
        
        if all_jump_nodes:
            # 找到 page 的最后一个节点
            # 方法：找到所有没有出边的节点（除了指向其他 page 或 jump 节点）
            # 或者找到所有节点中最后一个节点
            last_node = None
            
            # 方法1: 找到所有节点中最后一个节点（按生成顺序）
            if nodes:
                # 找到最后一个非 jump 节点
                for node in reversed(nodes):
                    if node.get('type') != 'jump':
                        last_node = node.get('name')
                        break
            
            # 方法2: 如果找不到，使用 previous_node 或 entry_node_name
            if not last_node:
                last_node = previous_node if previous_node else entry_node_name
            
            # 方法3: 如果还是找不到，找到所有没有出边的节点
            if not last_node:
                # 找到所有作为 source_node 的节点
                source_nodes = {e.get('source_node') for e in edges if e.get('source_node')}
                # 找到所有节点名称
                all_node_names = {n.get('name') for n in nodes if n.get('name')}
                # 找到没有出边的节点（不在 source_nodes 中）
                nodes_without_outgoing = all_node_names - source_nodes
                if nodes_without_outgoing:
                    # 排除 jump 节点
                    nodes_without_outgoing = {n for n in nodes_without_outgoing 
                                            if not any(j.get('name') == n for j in all_jump_nodes)}
                    if nodes_without_outgoing:
                        # 选择第一个
                        last_node = next(iter(nodes_without_outgoing))
            
            # **FIX: 对于没有 content 的 page（如 JumpTo_Common_EAT_CRC），如果只有 jump 节点，**
            # **应该创建一个占位节点或者直接使用 jump 节点作为 entry node**
            # **但是，由于 jump 节点不能有出边，我们需要特殊处理：**
            # **如果 page 没有 content，但有 _needs_connection 的 jump 节点，**
            # **我们应该在 all_nodes 中查找是否有其他 page 的 jump 节点指向这个 page，**
            # **然后连接到这个 jump 节点。**
            # **但是，在 generate_workflow_from_page 中，我们不知道其他 page 的信息。**
            # **所以，我们采用另一种方法：如果 page 没有 content，但有 _needs_connection 的 jump 节点，**
            # **我们应该创建一个占位节点（code 节点，用于 setParameterActions），然后连接到 jump 节点。**
            if not last_node:
                # 检查是否有 _needs_connection 的 jump 节点（这些节点通常来自 _generate_global_transition_nodes）
                needs_connection_jumps = [n for n in all_jump_nodes if n.get('_needs_connection')]
                if needs_connection_jumps:
                    # 对于没有 content 的 page，如果有 setParameterActions，我们应该创建一个 code 节点
                    for jump_node in needs_connection_jumps:
                        set_param_actions = jump_node.get('transition_info', {}).get('set_parameter_actions', [])
                        if set_param_actions:
                            # 创建 code 节点用于 setParameterActions
                            code_node, _ = generate_setparameter_code_node(
                                set_param_actions, page_id, "SetParameters", self._generate_unique_node_name
                            )
                            if code_node:
                                nodes.append(code_node)
                                last_node = code_node['name']
                                # 如果还没有 entry node，使用 code 节点作为 entry node
                                if entry_node_name is None:
                                    entry_node_name = code_node['name']
                                print(f"    - Created placeholder code node for page without content: {code_node['name']}")
                                break
                    
                    # 如果还是没有 last_node，说明这个 page 真的没有任何 content，只有 jump 节点
                    # 这种情况下，jump 节点本身应该作为 entry node，但是 jump 节点不能有出边
                    # 所以，我们需要在后续的流程中处理这种情况（比如在 convert_to_multiple_workflows 中）
                    # 这里我们暂时跳过，让 jump 节点保持 _needs_connection 标记
                    if not last_node:
                        print(f"    ⚠️  Warning: Page {page_id[:8]} has no content and only jump nodes, will be handled in post-processing")
            
            # 添加从 page 的最后一个节点到 jump 节点的边
            # 注意：如果 last_node 是条件节点，不应该用 default 边连接到 jump 节点
            # 因为条件节点应该通过条件分支（condition 边）来连接到目标
            if last_node:
                # 检查 last_node 是否是条件节点
                is_condition_node = any(
                    n.get('name') == last_node and n.get('type') == 'condition'
                    for n in nodes
                )
                
                for jump_node in all_jump_nodes:
                    jump_node_name = jump_node.get('name')
                    # 检查是否已经有边连接到这个 jump 节点
                    has_edge = any(e.get('target_node') == jump_node_name for e in edges)
                    if not has_edge:
                        # 如果 last_node 是条件节点，跳过 default 边的添加
                        # 条件节点应该通过条件分支（condition 边）来连接
                        if is_condition_node:
                            # 条件节点应该通过条件分支连接，跳过 default 边
                            continue
                        
                        edges.append({
                            "source_node": last_node,
                            "target_node": jump_node_name,
                            "connection_type": "default"
                        })
                        print(f"    - Added edge: {last_node} → {jump_node_name}")
                    # 移除标记
                    if '_needs_connection' in jump_node:
                        del jump_node['_needs_connection']
            else:
                print(f"    ⚠️  Warning: Could not find last node for page {page_id[:8]}...")
        
        # 4. 处理 Pattern 3 中 is_always_true 的直接连接（跳过 condition 节点）
        # 这些路由会直接从上游节点连到目标 page/jump，不经过 Condition Routing
        direct_connections_from_p3 = []
        for node in intent_nodes:
            if '_direct_connections' in node:
                direct_connections_from_p3 = node['_direct_connections']
                del node['_direct_connections']  # Clean up marker
                break
        
        if direct_connections_from_p3:
            logger.debug(f"  ✓ Processing {len(direct_connections_from_p3)} is_always_true direct connections (Pattern 3)")
            source_for_direct = previous_node
            if not source_for_direct:
                # 找到第一个非 condition/jump 节点作为 source
                for node in nodes:
                    if node.get('type') not in ('condition', 'jump') and node.get('name'):
                        source_for_direct = node.get('name')
                        break
            
            if source_for_direct:
                for direct_conn in direct_connections_from_p3:
                    target_page_id = direct_conn.get('target_page_id')
                    target_flow_id = direct_conn.get('target_flow_id')
                    transition_code_node = direct_conn.get('transition_code_node')
                    transition_text_nodes = direct_conn.get('transition_text_nodes', [])
                    
                    # 如果有 code 节点（setParameterActions），先连到 code
                    source_for_target = source_for_direct
                    if transition_code_node:
                        self._safe_append_edge(edges, source_for_target, transition_code_node, "default", None, nodes)
                        source_for_target = transition_code_node
                    
                    # 再连接 beforeTransition 的 text 节点（如果有）
                    for text_node in transition_text_nodes:
                        self._safe_append_edge(edges, source_for_target, text_node, "default", None, nodes)
                        source_for_target = text_node
                    
                    # 确定最终目标（jump 或 page）
                    final_target = None
                    if target_flow_id and not target_page_id:
                        # 查找对应的 jump 节点
                        jump_node = self._find_jump_node_for_target(
                            target_flow_id, target_page_id, jump_nodes, None, all_nodes
                        )
                        if jump_node:
                            final_target = jump_node.get('name')
                        else:
                            # 如果没找到对应 jump 节点，补建一个，避免 is_always_true 直连丢失
                            jump_node_name = self._generate_unique_node_name(
                                f"jump_to_flow_{target_flow_id[:8]}",
                                page_id
                            )
                            jump_node = {
                                "type": "jump",
                                "name": jump_node_name,
                                "title": f"jump_to_{target_flow_id[:8]}",
                                "jump_type": "flow",
                                "jump_robot_id": "",
                                "jump_robot_name": "",
                                "jump_carry_history_number": 5,
                                "jump_flow_name": "",
                                "jump_flow_uuid": target_flow_id,
                                "jump_carry_userinput": True
                            }
                            jump_nodes.append(jump_node)
                            nodes.append(jump_node)
                            if all_nodes is not None:
                                all_nodes.append(jump_node)
                            final_target = jump_node_name
                            logger.info(f"    ✅ Created missing jump node for direct connection: {jump_node_name} -> {target_flow_id[:8]}...")
                    
                    if not final_target and target_page_id:
                        final_target = f"page_{target_page_id[:8]}"
                    
                    if final_target:
                        self._safe_append_edge(edges, source_for_target, final_target, "default", None, nodes)
                        logger.debug(f"    ✓ Added direct edge (is_always_true): {source_for_target} → {final_target}")
        
        # 5. 兜底逻辑：确保纯条件页面（Pattern 3）的 condition 节点和目标 page 之间有边
        #
        # 问题场景：
        # - Page 本身没有 intent，只有 condition 分支（如 CCLA-CCCL-24/26/28/41/42 等）
        # - 我们已经在 _generate_pure_condition_nodes 中生成了 condition 节点和 condition_branches
        # - 但在复杂的 edges 生成逻辑中，这类纯条件页面的边有时会遗漏，导致目标 page 成为"孤岛节点"
        #
        # 解决方案：
        # - 如果当前 page 存在 title 为 "Condition Routing" 的 condition 节点，
        #   且 condition_branches 中存在 target_page_id/target_flow_id，
        #   则确保：
        #   1) previous_node → mixed_condition_code_nodes(如果有) → condition_node 有边
        #   2) condition_node → target_page / jump 节点 有 condition 边
        try:
            if condition_branches:
                # 查找纯条件页面的 condition 节点（Pattern 3 使用标题 "Condition Routing"）
                pure_condition_nodes = [
                    n for n in intent_nodes
                    if n.get('type') == 'condition' and n.get('title') == 'Condition Routing'
                ]
                
                if pure_condition_nodes:
                    condition_node_name = pure_condition_nodes[0].get('name')

                    # 查找合并的混合条件 code 节点（只有一个）
                    combined_mixed_code_node = None
                    for branch in condition_branches:
                        node_name = branch.get('combined_mixed_code_node')
                        if node_name:
                            combined_mixed_code_node = node_name
                            break

                    # 4.1 连接入口节点
                    # Avoid using transition nodes (setParameter/beforeTransition) as entry
                    transition_nodes = set()
                    for branch in condition_branches:
                        code_node = branch.get('transition_code_node')
                        if code_node:
                            transition_nodes.add(code_node)
                        for text_node in branch.get('transition_text_nodes', []) or []:
                            transition_nodes.add(text_node)
                    
                    # 4.1.1 如果有合并的混合条件 code 节点，它就是这个 Pattern 3 page 的入口
                    # 先连接 combined_mixed_code_node → condition_node
                    if combined_mixed_code_node:
                        if condition_node_name:
                            self._safe_append_edge(
                                edges,
                                combined_mixed_code_node,
                                condition_node_name,
                                "default",
                                None,
                                nodes
                            )
                        # 只有当 previous_node 不是 transition 节点时，才连接到 combined_mixed_code_node
                        if previous_node and previous_node not in transition_nodes and previous_node != combined_mixed_code_node:
                            self._safe_append_edge(
                                edges,
                                previous_node,
                                combined_mixed_code_node,
                                "default",
                                None,
                                nodes
                            )
                    else:
                        # 没有混合条件节点
                        source_for_condition = previous_node
                        if source_for_condition in transition_nodes:
                            source_for_condition = None
                        if not source_for_condition:
                            # 如果没有 previous_node（例如纯 Input 页，只生成了 code 节点），
                            # 使用第一个非 condition/jump 节点作为入口
                            for node in nodes:
                                node_type = node.get('type')
                                node_name = node.get('name', '')
                                if (
                                    node_type not in ('condition', 'jump')
                                    and node_name
                                    and node_name not in transition_nodes
                                ):
                                    source_for_condition = node_name
                                    break
                        
                        if source_for_condition and condition_node_name:
                            self._safe_append_edge(
                                edges,
                                source_for_condition,
                                condition_node_name,
                                "default",
                                None,
                                nodes
                            )

                    # 4.2 condition_node → target_page / jump（condition 连接）
                    # writed by senlin.deng 2026-01-17
                    # 获取 Pure Condition Routing 节点实际拥有的 condition_id 列表，修复该节点连接到不属于真正要跳转的下一个节点
                    # 这样可以避免使用 Intent 分支（condition_id 以 intent_ 开头）的信息来生成边
                    pure_condition_node_obj = pure_condition_nodes[0]
                    pure_condition_ids = set(
                        b.get('condition_id') 
                        for b in pure_condition_node_obj.get('if_else_conditions', [])
                        if b.get('condition_id')
                    )
                    
                    for branch in condition_branches:
                        condition_id = branch.get('condition_id')
                        target_page_id = branch.get('target_page_id')
                        target_flow_id = branch.get('target_flow_id')

                        if not condition_id or (not target_page_id and not target_flow_id):
                            continue
                        
                        # writed by senlin.deng 2026-01-17
                        # 只处理 Pure Condition Routing 节点实际拥有的分支
                        # 跳过 Intent 分支（这些边应该从 Semantic Judgment 节点出发，不是从 Pure Condition Routing 节点）
                        if condition_id not in pure_condition_ids:
                            logger.debug(f"    🔀 跳过非 Pure Condition 分支: condition_id={condition_id[:20] if len(condition_id) > 20 else condition_id}...")
                            continue

                        # 优先查找 jump 节点（如果目标是 flow 或中转站）
                        final_target = None
                        jump_node = self._find_jump_node_for_target(
                            target_flow_id,
                            target_page_id,
                            jump_nodes,
                            None,
                            all_nodes
                        )
                        if jump_node:
                            final_target = jump_node.get('name')
                        else:
                            # write by senlin.deng 2026-01-22
                            # 如果Intent+condition混合路由情况下，是 targetFlowId 且没有 jump 节点，补建一个 jump_to 节点
                            if target_flow_id and not target_page_id:
                                jump_node_name = self._generate_unique_node_name(
                                    f"jump_to_flow_{target_flow_id[:8]}",
                                    page_id
                                )
                                jump_node = {
                                    "type": "jump",
                                    "name": jump_node_name,
                                    "title": f"jump_to_{target_flow_id[:8]}",
                                    "jump_type": "flow",
                                    "jump_robot_id": "",
                                    "jump_robot_name": "",
                                    "jump_carry_history_number": 5,
                                    "jump_flow_name": "",
                                    "jump_flow_uuid": target_flow_id,
                                    "jump_carry_userinput": True
                                }
                                jump_nodes.append(jump_node)
                                nodes.append(jump_node)
                                if all_nodes is not None:
                                    all_nodes.append(jump_node)
                                final_target = jump_node_name
                                logger.info(f"    ✅ Created missing jump node for targetFlowId: {jump_node_name} -> {target_flow_id[:8]}...")
                            else:
                                # 否则直接连接到 page 节点
                                target_raw = target_page_id or target_flow_id
                                if target_raw:
                                    final_target = f"page_{target_raw[:8]}"

                        if final_target and condition_node_name:
                            transition_code_node = branch.get('transition_code_node')
                            transition_text_nodes = branch.get('transition_text_nodes', [])
                            
                            # Condition edge should point to the first transition node if present
                            first_target = transition_code_node or (transition_text_nodes[0] if transition_text_nodes else final_target)
                            
                            self._safe_append_edge(
                                edges,
                                condition_node_name,
                                first_target,
                                "condition",
                                condition_id,
                                nodes
                            )
                            
                            # Chain code/text nodes to final target
                            current_source = first_target
                            if transition_code_node and transition_text_nodes:
                                for text_node in transition_text_nodes:
                                    self._safe_append_edge(edges, current_source, text_node, "default", None, nodes)
                                    current_source = text_node
                            elif transition_text_nodes and first_target != final_target:
                                # first_target is the first text node
                                for text_node in transition_text_nodes[1:]:
                                    self._safe_append_edge(edges, current_source, text_node, "default", None, nodes)
                                    current_source = text_node
                            
                            if current_source != final_target:
                                self._safe_append_edge(edges, current_source, final_target, "default", None, nodes)
                            
                            # Clean up any incorrect incoming edges to transition nodes
                            if transition_code_node:
                                allowed_sources = {condition_node_name}
                                edges = [
                                    e for e in edges
                                    if not (
                                        e.get("target_node") == transition_code_node
                                        and e.get("source_node") not in allowed_sources
                                    )
                                ]
                            
                            if transition_text_nodes:
                                first_text = transition_text_nodes[0]
                                allowed_sources = {transition_code_node or condition_node_name}
                                edges = [
                                    e for e in edges
                                    if not (
                                        e.get("target_node") == first_text
                                        and e.get("source_node") not in allowed_sources
                                    )
                                ]
                                
                                for idx in range(1, len(transition_text_nodes)):
                                    text_node = transition_text_nodes[idx]
                                    prev_node = transition_text_nodes[idx - 1]
                                    edges = [
                                        e for e in edges
                                        if not (
                                            e.get("target_node") == text_node
                                            and e.get("source_node") != prev_node
                                        )
                                    ]
                                
                                # writed by senlin.deng 2026-01-21
                                # 清理非最后一个 text_node 到 final_target 的出边
                                # 只有最后一个 BeforeTransition_Response 节点应该连接到下一个 page 或 jump_to 节点
                                if len(transition_text_nodes) > 1:
                                    last_text_node = transition_text_nodes[-1]
                                    # 删除非最后一个 text_node 到 final_target 的边
                                    edges = [
                                        e for e in edges
                                        if not (
                                            e.get("source_node") in transition_text_nodes[:-1]  # 非最后一个 text_node
                                            and e.get("target_node") == final_target  # 连接到 final_target
                                        )
                                    ]
                            
                            # 混合条件兜底：确保 Condition_1 等分支一定有 condition 边
                            if branch.get('combined_mixed_code_node'):
                                has_condition_edge = any(
                                    e.get('source_node') == condition_node_name
                                    and e.get('target_node') == first_target
                                    and e.get('connection_type') == 'condition'
                                    and e.get('condition_id') == condition_id
                                    for e in edges
                                )
                                if not has_condition_edge:
                                    self._safe_append_edge(
                                        edges,
                                        condition_node_name,
                                        first_target,
                                        "condition",
                                        condition_id,
                                        nodes
                                    )
                    
                    # writed by senlin.deng 2026-01-18
                    # 处理 Pure Condition Routing 的 Other 分支
                    # 如果有 routeGroupsTransitionEvents，连接到 Route Groups Intent Recognition
                    # 否则连接到 fallback_text_node
                    
                    # 检查 page 是否有 routeGroupsTransitionEvents（即使 route_groups_entry_node 可能为 None）
                    page_value_for_check = page.get('value', {}) if 'value' in page else page
                    has_route_groups_events = bool(page_value_for_check.get('routeGroupsTransitionEvents', []))
                    
                    for branch in pure_condition_node_obj.get('if_else_conditions', []):
                        if branch.get('logical_operator') == 'other':
                            other_condition_id = branch.get('condition_id')
                            next_node = branch.get('_next_node')
                            
                            # 当有 routeGroupsTransitionEvents 时，删除到 Fallback_Message 的边，连接到 Route Groups
                            if has_route_groups_events or route_groups_entry_node:
                                # 先移除可能存在的到 fallback_text (Fallback_Message) 的边
                                if next_node:
                                    edges_to_remove = []
                                    for idx, edge in enumerate(edges):
                                        if (edge.get('source_node') == condition_node_name and 
                                            edge.get('target_node') == next_node):
                                            edges_to_remove.append(idx)
                                    for idx in reversed(edges_to_remove):
                                        removed_edge = edges.pop(idx)
                                        logger.info(f"    🔀 移除旧边: {condition_node_name} → {next_node} (有 routeGroupsTransitionEvents)")
                                    
                                    # 同时从 nodes 中移除 Fallback/Jump to Main Agent 节点（因为不再需要）
                                    # write by senlin.deng 2026-01-21: 同时匹配 'Fallback Message' 和 'Jump to Main Agent'
                                    nodes_to_remove = []
                                    for idx, node in enumerate(nodes):
                                        if node.get('name') == next_node and node.get('title') in ['Fallback Message', 'Jump to Main Agent']:
                                            nodes_to_remove.append(idx)
                                    for idx in reversed(nodes_to_remove):
                                        removed_node = nodes.pop(idx)
                                        logger.info(f"    🔀 移除 {removed_node.get('title')} 节点: {next_node} (有 routeGroupsTransitionEvents)")
                                
                                # 如果有 route_groups_entry_node，连接到它
                                if route_groups_entry_node:
                                    has_edge_to_route_groups = any(
                                        edge.get('source_node') == condition_node_name and 
                                        edge.get('target_node') == route_groups_entry_node
                                        for edge in edges
                                    )
                                    if not has_edge_to_route_groups:
                                        self._safe_append_edge(
                                            edges,
                                            condition_node_name,
                                            route_groups_entry_node,
                                            "condition",
                                            other_condition_id,
                                            nodes
                                        )
                                        logger.info(f"    🔀 Pure Condition Routing Other → Route Groups Intent Recognition ({route_groups_entry_node})")
                                else:
                                    # 有 routeGroupsTransitionEvents 但没有生成入口节点，不添加边
                                    logger.debug(f"    🔀 有 routeGroupsTransitionEvents 但无入口节点，不添加 Other 分支边")
                            elif next_node:
                                # 普通情况（无 routeGroupsTransitionEvents）：Other 分支 → fallback_text
                                self._safe_append_edge(
                                    edges,
                                    condition_node_name,
                                    next_node,
                                    "condition",
                                    other_condition_id,
                                    nodes
                                )
                                logger.debug(f"    🔀 Pure Condition Routing Other → {next_node}")
                            break  # 只有一个 Other 分支
        except Exception as e:
            # 兜底逻辑失败时不影响主流程，只打印日志方便排查
            print(f"  ⚠️ Warning: failed to apply fallback pure-condition edges for page {page_id[:8]}: {e}")

        # 如果 entry_node_name 仍然为 None，但有节点，设置入口节点
        # 优先选择带有 _is_entry_node 标记的节点，否则选择第一个非 jump/transition 节点
        if entry_node_name is None and nodes:
            # 优先选择带有 _is_entry_node 标记的节点
            for node in nodes:
                if node.get('_is_entry_node'):
                    entry_node_name = node.get('name')
                    print(f"  - Set entry node (_is_entry_node): {entry_node_name}")
                    break
            
            # 其次选择非 jump/transition 节点
            if entry_node_name is None:
                transition_names = {n.get('name') for n in nodes if n.get('title', '').startswith('Set Parameters')}
                for node in nodes:
                    if node.get('type') != 'jump' and node.get('name') and node.get('name') not in transition_names:
                        entry_node_name = node.get('name')
                        print(f"  - Set entry node (non-jump/non-transition): {entry_node_name}")
                        break
            
            # 如果所有节点都是 jump 或 transition 节点，使用第一个 jump 节点
            if entry_node_name is None:
                for node in nodes:
                    if node.get('name'):
                        entry_node_name = node.get('name')
                        print(f"  - Set entry node (fallback): {entry_node_name}")
                        break
        
        print(f"  - Total: {len(nodes)} nodes, {len(edges)} edges")
        
        return nodes, edges, entry_node_name
    
    def convert_to_multiple_workflows(
        self,
        fulfillments_file: str = 'fulfillments.json',
        flow_file: str = 'input/exported_flow_TXNAndSTMT_Deeplink.json',
        lang: str = 'en',
        output_dir: str = '.',
        entities_file: str = None
    ) -> List[str]:
        """
        将 flow 和 fulfillments 转换为多个独立的 workflow
        每个 flow-level triggerIntent 对应一个独立的 workflow
        
        Args:
            fulfillments_file: fulfillments.json 文件路径
            flow_file: exported_flow_*.json 文件路径
            lang: 语言
            output_dir: 输出目录
            entities_file: step1 处理后的 entities 文件路径（如 entities_zh.json），用于获取 synonyms
            
        Returns:
            生成的 workflow 名称列表（用于后续 step3 和 step5）
        """
        logger.info(f'Step 2: 工作流转换 - 语言: {lang}')
        
        # 1. 加载数据
        self.lang = lang
        with open(flow_file, 'r', encoding='utf-8') as f:
            flow_data = json.load(f)
        # 1.1 加载实体候选值映射（用于 LLM 提示动态候选）
        self._load_entity_candidates(flow_file)
        
        # 1.2 加载 step1 处理后的 entities 数据（包含 synonyms）
        if entities_file:
            self._load_entities_with_synonyms(entities_file)
            logger.info(f'Loaded {len(self.entities_with_synonyms)} entities with synonyms')
        with open(fulfillments_file, 'r', encoding='utf-8') as f:
            fulfillments_data = json.load(f)
        
        pages = fulfillments_data.get('pages', [])
        
        # 2. 创建 page_id 到 page 数据的映射
        page_id_map = {}
        for page in pages:
            # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
            page_id = page.get('key') or page.get('pageId', '')
            if page_id:
                page_id_map[page_id] = page
        
        # 3. 解析 flow 层级的 transitionEvents
        flow_obj = flow_data.get('flow', {}).get('flow', {})
        transition_events = flow_obj.get('transitionEvents', [])
        
        # logger.info(f'解析到的flow_obj: {json.dumps(transition_events, indent=2, ensure_ascii=False)}')
        # exit()
        # 3.1 收集 flow 层级的 slots（用于 start page 抽槽）
        flow_slot_infos = extract_flow_slots(flow_data)

        # 3.2 收集 flow 层级的条件（start page 条件）
        flow_conditions = []
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
            if comparator:
                flow_conditions.append({
                    "comparator": comparator,
                    "rhs": rhs_value,
                    "lhs_expressions": lhs_exprs,
                    "target_page_id": target_page_id,
                    "target_flow_id": target_flow_id
                })
        
        logger.info(f'找到 {len(transition_events)} 个 flow-level intents')
        
        if not transition_events:
            logger.warning('No flow-level intents found. Generating single default workflow.')
            # 如果没有 flow-level 意图，生成一个默认的 workflow
            return self._generate_single_default_workflow(
                pages, lang, output_dir
            )
        # write by senlin.deng 2026-01-25
        # 修复：新增分组策略，解决由于start page中有相同意图，但是condition不同，导致生成flow被覆盖的问题
        # 4. 分组策略：
        # - 有 intent：按 triggerIntentId 合并（忽略 target），同意图合并为一个 workflow
        # - 无 intent：按 (target_page_id, target_flow_id) 分组
        workflow_groups = {}  # key: ("intent", intent_id) 或 ("target", target_page_id, target_flow_id)
        
        for event in transition_events:
            trigger_intent_id = event.get('triggerIntentId', '')
            handler = event.get('transitionEventHandler', {})
            target_page_id = handler.get('targetPageId')
            target_flow_id = handler.get('targetFlowId')
            
            # 有 intent 时只按 intent 分组；否则按 target 分组
            if trigger_intent_id:
                group_key = ("intent", trigger_intent_id)
            else:
                group_key = ("target", target_page_id, target_flow_id)
            if group_key not in workflow_groups:
                workflow_groups[group_key] = []
            else:
                # write by senlin.deng 2026-01-28
                # 无 intent 时，同 target 仅保留第一个 event
                if not trigger_intent_id:
                    print(f'  ⏭️ Skip duplicate no-intent event for target: {target_page_id or ""} {target_flow_id or ""}')
                    continue
            workflow_groups[group_key].append(event)
        
        logger.info(f'找到 {len(transition_events)} 个 transitionEvents，合并为 {len(workflow_groups)} 个 workflow')
        # 5. 为每个分组生成一个 workflow（包含该分组的所有条件分支）
        generated_workflows = []
        # logger.info(self.intents_mapping)
        # exit()
        for group_idx, (group_key, events) in enumerate(workflow_groups.items(), 1):
            group_type = group_key[0]
            if group_type == "intent":
                trigger_intent_id = group_key[1]
                target_page_id = None
                target_flow_id = None
            else:
                trigger_intent_id = ""
                target_page_id = group_key[1]
                target_flow_id = group_key[2]
            
            # 收集该分组所有 events 的 flow 条件
            flow_conditions = []
            for event in events:
                event_conditions = extract_flow_conditions_for_event(event)
                flow_conditions.extend(event_conditions)
            
            # 获取 intent 名称（改进的逻辑）
            if trigger_intent_id and trigger_intent_id in self.intents_mapping:
                # 从映射中获取意图名称
                intent_name = self.intents_mapping[trigger_intent_id]
                print(f'\n[INTENT] Workflow #{group_idx}: 从 intents_mapping 获取名称 = "{intent_name}"')
            elif trigger_intent_id:
                # 如果有 ID 但映射中没有，使用 ID 本身（可能是完整名称）
                intent_name = trigger_intent_id
                print(f'\n[WARNING] Workflow #{group_idx}: intents_mapping 中未找到，使用 ID = "{trigger_intent_id}"')
                print(f'   Available mappings: {len(self.intents_mapping)} intents')
                # 显示前几个映射帮助调试
                if self.intents_mapping:
                    sample_keys = list(self.intents_mapping.keys())[:3]
                    print(f'   Sample mapping keys: {sample_keys}')
            else:
                # 修改workflow name 为 condition 表达式
                # write by senlin.deng 2025-12-23
                # 完全没有 intent ID，尝试从 condition 中构建表达式作为名称
                intent_name = None
                for event in events:
                    condition = event.get('condition', {})
                    restriction = condition.get('restriction', condition)
                    if isinstance(restriction, dict):
                        # 提取 comparator
                        comparator = restriction.get('comparator', '')
                        
                        # 提取 lhs 表达式
                        lhs = restriction.get('lhs', {})
                        lhs_parts = []
                        if isinstance(lhs, dict):
                            expressions = lhs.get('member', {}).get('expressions', [])
                            for expr in expressions:
                                val = expr.get('value', '')
                                if val:
                                    # 去掉 $ 符号
                                    clean_val = val.replace('$', '').strip()
                                    if clean_val:
                                        lhs_parts.append(clean_val)
                        
                        # 提取 rhs 值
                        rhs = restriction.get('rhs', {})
                        rhs_value = ''
                        if isinstance(rhs, dict):
                            rhs_value = rhs.get('value', '')
                            if rhs_value is None:
                                rhs_value = ''
                        
                        # 只取 $session.params. 后面的部分（即最后一个 expression）
                        if lhs_parts:
                            # 只使用最后一个部分作为名称
                            # 例如: ["session", "params", "JumpTo_xxx"] -> "JumpTo_xxx"
                            intent_name = lhs_parts[-1]
                            
                            if intent_name:
                                print(f'\n[CONDITION] Workflow #{group_idx}: 从 condition 构建表达式')
                                print(f'   LHS: {lhs_parts}')
                                print(f'   Comparator: {comparator}')
                                print(f'   RHS: {rhs_value}')
                                print(f'   生成名称: "{intent_name}"')
                                break
                # logger.info(f'intent_name: {intent_name}')
                # exit()
                # 如果仍然没有找到，使用序号作为 fallback
                if not intent_name:
                    intent_name = f"intent_{group_idx}"
                    print(f'\n⚠️  Workflow #{group_idx}: 无 triggerIntentId 且无 condition，使用 fallback = "{intent_name}"')
            
            # 清洗 intent_name 作为文件名（移除特殊字符）
            safe_intent_name = self._sanitize_filename(intent_name)

            logger.info(f'\n{"="*60}')
            logger.info(f'📍 [{group_idx}/{len(workflow_groups)}] Processing Workflow: {intent_name}')
            logger.info(f'{"="*60}')
            logger.info(f'   Trigger Intent ID: {trigger_intent_id}')
            logger.info(f'   Target Page ID: {target_page_id}')
            logger.info(f'   Target Flow ID: {target_flow_id}')
            logger.info(f'   Workflow Name: {safe_intent_name}')
            logger.info(f'   Merged {len(events)} transitionEvents with {len(flow_conditions)} conditions')
            
            # 检查 events 列表是否为空
            if not events:
                logger.error(f'❌ Workflow #{group_idx}: events 列表为空，跳过该 workflow')
                print(f'   ⚠️  Skipping workflow due to empty events list')
                continue
            
            # 使用第一个 event 作为主要 event（用于获取其他信息）
            # 确保 main_event 有正确的结构
            main_event = events[0]
            
            # 验证 main_event 结构：确保有 transitionEventHandler 字段
            if not isinstance(main_event, dict):
                logger.error(f'❌ Workflow #{group_idx}: main_event 不是字典类型: {type(main_event)}')
                print(f'   ⚠️  Skipping workflow due to invalid main_event type')
                continue
            
            # 确保 transitionEventHandler 存在（如果不存在，创建一个空的）
            if 'transitionEventHandler' not in main_event:
                logger.warning(f'⚠️  Workflow #{group_idx}: main_event 缺少 transitionEventHandler，使用默认值')
                main_event['transitionEventHandler'] = {
                    'targetPageId': target_page_id,
                    'targetFlowId': target_flow_id,
                    'beforeTransition': {}
                }
            
            
            # 生成该 intent 的独立 workflow（包含所有条件分支）
            try:
                workflow_name = self._generate_single_intent_workflow(
                    intent_id=trigger_intent_id,
                    intent_name=intent_name,
                    safe_intent_name=safe_intent_name,
                    event=main_event,
                    events=events,
                    target_page_id=target_page_id,
                    target_flow_id=target_flow_id,
                    page_id_map=page_id_map,
                    lang=lang,
                    output_dir=output_dir,
                    flow_slot_infos=flow_slot_infos,
                    flow_conditions=flow_conditions  # 传入所有合并的条件
                )
                
                if workflow_name:
                    generated_workflows.append(workflow_name)
            except Exception as e:
                logger.error(f'❌ Workflow #{group_idx}: 生成 workflow 时出错: {str(e)}')
                print(f'   ⚠️  Error generating workflow: {str(e)}')
                import traceback
                traceback.print_exc()
                continue
        
        # 5. 处理所有 pages 的 transitionEvents，确保所有 jump 节点都被创建和连接
        # 只从非路由组部分（pages 的 transitionEvents）查看，如果遇到需要跳转到 flow，就创建 jump 节点
        
        for workflow_name in generated_workflows:
            nodes_file = os.path.join(output_dir, f'nodes_config_{workflow_name}.json')
            edges_file = os.path.join(output_dir, f'edge_config_{workflow_name}.json')
            
            if not os.path.exists(nodes_file) or not os.path.exists(edges_file):
                continue
            
            try:
                # 读取现有的 nodes 和 edges 配置
                with open(nodes_file, 'r', encoding='utf-8') as f:
                    nodes_config = json.load(f)
                existing_nodes = nodes_config.get("nodes", [])
                
                # 获取所有 jump 节点
                all_jump_nodes_in_workflow = [
                    node for node in existing_nodes 
                    if node.get("type") == "jump" and node.get("jump_flow_uuid")
                ]
                
                if all_jump_nodes_in_workflow:
                    print(f'  [PROCESSING] Processing {len(all_jump_nodes_in_workflow)} jump nodes for {workflow_name}...')
                    self._add_edges_for_jump_nodes(
                        edges_file, 
                        all_jump_nodes_in_workflow, 
                        existing_nodes, 
                        flow_data,
                        nodes_file
                    )
            except Exception as e:
                print(f'  ❌ Error processing jump nodes for {workflow_name}: {e}')
                import traceback
                traceback.print_exc()
        
        # 6. 保存 workflow 列表文件（供 step3 和 step5 使用）
        workflow_list_file = os.path.join(output_dir, 'generated_workflows.json')
        with open(workflow_list_file, 'w', encoding='utf-8') as f:
            json.dump({
                "workflows": generated_workflows,
                "count": len(generated_workflows)
            }, f, ensure_ascii=False, indent=2)
        
        logger.info(f'✅ Step 2 完成: 生成 {len(generated_workflows)} 个 workflows')
        
        return generated_workflows
    
    def _sanitize_filename(self, name: str) -> str:
        """将名称转换为安全的文件名"""
        import re
        # 移除或替换特殊字符
        safe_name = re.sub(r'[<>:"/\\|?*\s]', '_', name)
        safe_name = re.sub(r'_+', '_', safe_name)  # 合并多个下划线
        safe_name = safe_name.strip('_')  # 移除首尾下划线
        return safe_name.lower()
    
    def _generate_single_intent_workflow(
        self,
        intent_id: str,
        intent_name: str,
        safe_intent_name: str,
        event: Dict[str, Any],
        target_page_id: str,
        target_flow_id: str,
        page_id_map: Dict[str, Any],
        lang: str,
        output_dir: str,
        flow_slot_infos: List[Dict[str, Any]] = None,
        flow_conditions: List[Dict[str, Any]] = None,
        events: List[Dict[str, Any]] = None
    ) -> str:
        """
        为单个 intent 生成独立的 workflow
        
        注意：不再生成 flow 层级的意图识别节点（capture → kb → code → condition）
        这些节点将在外层配置，工作流中只包含 pages 层级的节点
        flow start 链路为：start/slot → (intent参数抽取) → (条件节点) → setParameter → page/jump
        
        Returns:
            workflow 名称（不含扩展名）
        """
        events_list = events if events else [event]
        all_nodes = []
        all_edges = []
        
        # 1. 添加 start 节点
        start_node = {"type": "start", "name": f"start_{safe_intent_name}"}
        all_nodes.append(start_node)

        # 1.0 如果 flow 层级定义了 slots（start page 抽槽），先插入 capture → llm → parse 链路
        chain_source = start_node["name"]
        # 拆分到子模块：构建 flow 抽槽链
        if flow_slot_infos:
            fs_nodes, fs_edges, chain_source = build_flow_slot_chain(
                safe_intent_name=safe_intent_name,
                flow_slot_infos=flow_slot_infos,
                entity_candidates=self.entity_candidates,
                lang=self.lang,
                gen_var=self._generate_variable_name,
                start_name=chain_source,
                global_config=self.global_config
            )
            all_nodes.extend(fs_nodes)
            all_edges.extend(fs_edges)

        # 1.1 如果该 intent 定义了参数（实体抽槽），在开始链路前插入参数提取节点
        # 根据 ner_version 选择 Semantic 或 LLM 模式
        need_slot_extraction = bool(intent_id and intent_id in self.intent_parameters_map)
        slot_capture_node = None
        slot_llm_node = None
        slot_parse_node = None
        slot_ner_semantic_node = None
        slot_ner_code_nodes = []
        
        if need_slot_extraction:
            parameters = self.intent_parameters_map.get(intent_id, [])
            param_names = [p.get('name', '') or p.get('id', '') for p in parameters]
            slot_capture_node = {
                "type": "captureUserReply",
                "name": f"capture_{safe_intent_name}",
                "title": "Capture User Input (Flow Start)",
                "variable_assign": "last_user_response"
            }
            
            # =====================================
            # Semantic NER 版本：使用 SemanticJudgment + Code 节点
            # =====================================
            if self.ner_version == 'semantic' and parameters:
                logger.debug(f"  🔄 [FlowStart] 使用 Semantic NER 版本为意图 {intent_name} 生成参数提取节点")
                
                ner_gen = self._init_ner_generator()
                if isinstance(ner_gen, SemanticNERNodeGenerator):
                    try:
                        semantic_nodes, semantic_branches = ner_gen.generate_parameter_nodes(
                            page_id=f"flow_start_{safe_intent_name}",
                            intent_name=intent_name,
                            condition_id=f"flow_start_{intent_name}",
                            trans_info_list=[],  # Flow start 没有 transition info
                            parameters=parameters,
                            capture_variable="last_user_response",
                            gen_unique_node_name=self._generate_unique_node_name,
                            gen_variable_name=self._generate_variable_name,
                            lang=lang
                        )
                        
                        if semantic_nodes:
                            # 获取所有 semantic 和 code 节点
                            all_ner_semantic_nodes = [n for n in semantic_nodes if n.get('type') == 'semanticJudgment']
                            all_ner_code_nodes = [n for n in semantic_nodes if n.get('type') == 'code']
                            
                            if all_ner_semantic_nodes:
                                # 添加 capture 节点
                                all_nodes.append(slot_capture_node)
                                
                                # 添加所有 semantic 和 code 节点
                                all_nodes.extend(all_ner_semantic_nodes)
                                all_nodes.extend(all_ner_code_nodes)
                                
                                # 构建连线：chain_source → capture
                                all_edges.append({
                                    "source_node": chain_source,
                                    "target_node": slot_capture_node["name"],
                                    "connection_type": "default"
                                })
                                
                                # 构建连线：capture → 第一个 semantic
                                first_semantic = all_ner_semantic_nodes[0]
                                all_edges.append({
                                    "source_node": slot_capture_node["name"],
                                    "target_node": first_semantic["name"],
                                    "connection_type": "default"
                                })
                                
                                # 为每个 semantic 节点生成边
                                for i, semantic_node in enumerate(all_ner_semantic_nodes):
                                    # 获取该 semantic 节点的条件分支
                                    condition_branches = semantic_node.get('_condition_branches', [])
                                    
                                    # 该 semantic 节点对应的 code 节点
                                    semantic_code_nodes = [n for n in all_ner_code_nodes 
                                                          if n.get('_ner_param_name') == semantic_node.get('_ner_param_name')]
                                    
                                    # 生成 semantic → code 的分支连线
                                    for branch in condition_branches:
                                        branch_condition_id = branch.get('condition_id', '')
                                        code_node_name = branch.get('_code_node_name', '')
                                        if branch_condition_id and code_node_name:
                                            all_edges.append({
                                                "source_node": semantic_node["name"],
                                                "target_node": code_node_name,
                                                "connection_type": "condition",
                                                "condition_id": branch_condition_id
                                            })
                                    
                                    # 如果不是最后一个 semantic，生成 code → 下一个 semantic 的连线
                                    if i < len(all_ner_semantic_nodes) - 1:
                                        next_semantic = all_ner_semantic_nodes[i + 1]
                                        for code_node in semantic_code_nodes:
                                            all_edges.append({
                                                "source_node": code_node["name"],
                                                "target_node": next_semantic["name"],
                                                "connection_type": "default"
                                            })
                                
                                # 标记最后一个 semantic 的所有 code 节点，它们需要汇聚到下一个主流程节点
                                last_semantic = all_ner_semantic_nodes[-1]
                                last_semantic_code_nodes = [n for n in all_ner_code_nodes 
                                                           if n.get('_ner_param_name') == last_semantic.get('_ner_param_name')]
                                
                                # 存储汇聚信息到第一个 semantic 节点，供后续使用
                                slot_ner_semantic_node = first_semantic
                                slot_ner_semantic_node['_flow_start_ner'] = True
                                slot_ner_semantic_node['_all_semantic_nodes'] = [n['name'] for n in all_ner_semantic_nodes]
                                slot_ner_semantic_node['_all_code_nodes'] = [n['name'] for n in all_ner_code_nodes]
                                slot_ner_semantic_node['_last_code_node_names'] = [n['name'] for n in last_semantic_code_nodes]
                                slot_ner_code_nodes = last_semantic_code_nodes
                                
                                # 设置 chain_source 为特殊标记，表示后续节点需要从多个 code 节点汇聚
                                # 记录所有最后一级 code 节点名，供后续汇聚连线使用
                                chain_source = f"__NER_CONVERGE__{','.join([n['name'] for n in last_semantic_code_nodes])}"
                                
                                logger.debug(f"    ✅ Semantic NER 生成了 {len(all_ner_semantic_nodes)} 个 semantic 节点和 {len(all_ner_code_nodes)} 个 code 节点")
                    except Exception as e:
                        logger.warning(f"    ⚠️ Semantic NER 生成失败，回退到 LLM 版本: {e}")
                        import traceback
                        traceback.print_exc()
                        slot_ner_semantic_node = None
            
            # =====================================
            # LLM NER 版本：使用 LLM + Code 节点（默认）
            # =====================================
            if not slot_ner_semantic_node:
                # write by senlin.deng 2026-01-27
                # 修复：flow start抽槽节点链不提示词设置不符合
                llm_output_variable = self._generate_variable_name()

                # 构建 LLM 提示词（与 page-level 的参数抽取保持一致）
                output_template = "{\n"
                for param_name in sorted(param_names):
                    param_name_normalized = param_name.replace('-', '_')
                    output_template += f'  "{param_name_normalized}": "",\n'
                output_template += "}"

                hint_lines = []
                for param in parameters:
                    param_name = param.get('name', '') or param.get('id', '')
                    param_name_normalized = param_name.replace('-', '_')
                    entity_type = param.get('entityType', '') or param.get('entityTypeDisplayName', '')

                    if entity_type and self.entity_candidates:
                        entity_key = f"@{entity_type}" if not entity_type.startswith('@') else entity_type
                        candidates = self.entity_candidates.get(entity_key, {}).get(lang, [])
                        if not candidates:
                            candidates = self.entity_candidates.get(entity_type, {}).get(lang, [])

                        if candidates:
                            hint_line = f'- {param_name_normalized}: allowed values ({lang}) = ' + ", ".join(candidates)

                            entity_display_name = entity_type.lstrip('@')
                            entity_data = self.entities_with_synonyms.get(entity_display_name, {}).get(lang, [])
                            if entity_data:
                                synonym_lines = []
                                for entry in entity_data:
                                    value = entry.get('value', '')
                                    synonyms = entry.get('synonyms', [])
                                    if value and synonyms:
                                        synonyms_str = "、".join(synonyms)
                                        synonym_lines.append(f'   "{value}"(synonyms: {synonyms_str})')
                                if synonym_lines:
                                    hint_line += '\n' + '\n'.join(synonym_lines)

                            hint_lines.append(hint_line)

                hint_text = ''
                if hint_lines:
                    hint_text = '\n##Hints (Use one of the allowed values for each parameter)\n' + "\n".join(hint_lines) + '\n'

                flow_prompt = f'''#Role
You are an information extraction specialist. Your task is to extract parameters from the user's reply.

##User Input
{{{{last_user_response}}}}

##Output Template
{output_template}

##Instructions
Extract the required parameters from user input and return in JSON format. If a parameter is not found, use empty string.
{hint_text}'''
                slot_llm_node = {
                    "type": "llmVariableAssignment",
                    "name": f"llm_extract_params_{safe_intent_name}",
                    "title": f"Extract Parameters from User Input (Flow Start)",
                    "variable_assign": llm_output_variable,
                    "prompt_template": flow_prompt,
                    "llm_name": self.global_config.get("llmcodemodel", "azure-gpt-4o"),
                    "chat_history_flag": self.global_config.get("enable_short_memory", False),
                    "chat_history_count": self.global_config.get("short_chat_count", 5),
                    "intent_name": intent_name
                }
                sorted_vars = sorted([p.replace("-", "_") for p in param_names])
                return_dict = ",\n".join(
                    [f'        "{v}": data["{v}"] if "{v}" in data else ""' for v in sorted_vars]
                )
                parse_code = f'''import json
import re

def main({llm_output_variable}) -> dict:
    match = re.search(r'([{{].*?[}}])', {llm_output_variable}, re.DOTALL)
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
                slot_parse_node = {
                    "type": "code",
                    "name": f"parse_params_{safe_intent_name}",
                    "title": f"Parse Parameters from LLM (Flow Start)",
                    "code": parse_code,
                    "outputs": sorted_vars,
                    "args": [llm_output_variable],
                }
                all_nodes.extend([slot_capture_node, slot_llm_node, slot_parse_node])
                all_edges.append({
                    "source_node": chain_source,
                    "target_node": slot_capture_node["name"],
                    "connection_type": "default"
                })
                all_edges.append({
                    "source_node": slot_capture_node["name"],
                    "target_node": slot_llm_node["name"],
                    "connection_type": "default"
                })
                all_edges.append({
                    "source_node": slot_llm_node["name"],
                    "target_node": slot_parse_node["name"],
                    "connection_type": "default"
                })
                chain_source = slot_parse_node["name"]
        
        # 2. 为 flow-level 的每个 event 生成 setParameterActions 节点
        # 这些操作应该在意图识别成功后、跳转到目标page之前执行
        def _resolve_page_id(page_id: str) -> str:
            if not page_id:
                return page_id
            if page_id in page_id_map:
                return page_id
            page_id_prefix = page_id[:8] if len(page_id) >= 8 else page_id
            matching_pages = [pid for pid in page_id_map.keys() if pid[:8] == page_id_prefix]
            return matching_pages[0] if matching_pages else page_id
        
        def _get_page_display_name(page_id: str) -> str:
            if not page_id:
                return ""
            resolved_id = _resolve_page_id(page_id)
            page_data = page_id_map.get(resolved_id, {})
            return page_data.get("value", {}).get("displayName", "")
        
        def _build_flow_set_params_node(set_parameter_actions: List[Dict[str, Any]], node_name: str, title_suffix: str) -> Dict[str, Any]:
            if not set_parameter_actions:
                # 无 setParameterActions 时，不生成节点
                return None
            
            print(f'   Found {len(set_parameter_actions)} parameter actions in beforeTransition')
            
            # 生成变量赋值代码
            code_lines = []
            output_variables = []
            input_variables = []  # 收集输入变量（从$引用中提取）
            
            for action in set_parameter_actions:
                parameter = action.get('parameter', '')
                value = action.get('value')  # 注意：不设默认值，保留 None
                
                # 使用通用解析函数处理值
                # 支持：变量引用、系统函数（如 GET_FIELD）、对象、null、数字、字符串等
                value_code, input_variables = parse_dialogflow_value(value, input_variables)
                code_lines.append(f"    {parameter} = {value_code}")
                print(f'      • {parameter} = {value_code}')
                
                output_variables.append(parameter)
            
            # 构建完整的 Python 函数
            if input_variables:
                func_signature = f"def main({', '.join(input_variables)}) -> dict:"
            else:
                func_signature = "def main() -> dict:"
            
            code_content = func_signature + "\n"
            code_content += "\n".join(code_lines) + "\n"
            code_content += "    return {\n"
            for var in output_variables:
                code_content += f'        "{var}": {var},\n'
            code_content += "    }"
            
            return {
                "type": "code",
                "name": node_name,
                "title": f"Set Parameters before transition ({title_suffix})",
                "code": code_content,
                "outputs": output_variables,
                "args": input_variables
            }
        
        def _build_condition_key(cond: Dict[str, Any]):
            lhs_var = cond.get("lhs_var")
            comparator = cond.get("comparator") or ""
            rhs = cond.get("rhs")
            if not lhs_var:
                return None
            # write by senlin.deng 2026-01-26
            # 修复：由于Flow start condition节点变量小写，导致节点后续连接匹配不正确的问题
            if isinstance(lhs_var, str):
                lhs_var = lhs_var.lower()
            if isinstance(rhs, list):
                rhs_filtered = [v for v in rhs if v and v != "true" and v is not True]
                if not rhs_filtered:
                    return None
                rhs_normalized = tuple(sorted([str(v) for v in rhs_filtered]))
            else:
                rhs_normalized = str(rhs)
            return (
                lhs_var,
                rhs_normalized,
                comparator,
                cond.get("target_page_id"),
                cond.get("target_flow_id")
            )
        
        event_infos = []
        condition_target_map = {}
        no_condition_events = []
        
        for idx, evt in enumerate(events_list, 1):
            handler = evt.get('transitionEventHandler', {})
            evt_target_page_id = handler.get('targetPageId')
            evt_target_flow_id = handler.get('targetFlowId')
            before_transition = handler.get('beforeTransition', {})
            set_parameter_actions = before_transition.get('setParameterActions', [])
            
            # 使用 event.name 作为稳定的后缀，避免同名节点冲突
            event_name = evt.get('name') or f"event_{idx}"
            import re
            safe_event_name = re.sub(r'[^a-zA-Z0-9_]', '_', event_name)
            if len(safe_event_name) > 12:
                safe_event_name = safe_event_name[:12]
            node_base = f"set_params_{safe_intent_name}_{safe_event_name}"
            set_param_node_name = self._generate_unique_node_name(node_base, "")
            
            title_suffix = _get_page_display_name(evt_target_page_id)
            if not title_suffix and evt_target_flow_id:
                title_suffix = evt_target_flow_id[:8]
            if not title_suffix:
                title_suffix = intent_name
            
            set_param_node = _build_flow_set_params_node(set_parameter_actions, set_param_node_name, title_suffix)
            set_param_node_name_actual = None
            if set_param_node:
                all_nodes.append(set_param_node)
                set_param_node_name_actual = set_param_node_name
                print(f'   ✅ Generated parameter setting code node: {set_param_node_name}')
            else:
                print(f'   ⏭️  Skipped parameter setting node (no setParameterActions): {set_param_node_name}')
            
            event_conditions = extract_flow_conditions_for_event(evt)
            cond_key = None
            if event_conditions:
                cond_key = _build_condition_key(event_conditions[0])
                if cond_key and set_param_node_name_actual:
                    condition_target_map[cond_key] = set_param_node_name_actual
            else:
                if set_param_node_name_actual:
                    no_condition_events.append(set_param_node_name_actual)
            
            event_infos.append({
                "event": evt,
                "target_page_id": evt_target_page_id,
                "target_flow_id": evt_target_flow_id,
                "set_param_node_name": set_param_node_name_actual,
                "condition_key": cond_key
            })

        # 2.1 如果 flow 层级定义了条件（start page 条件），插入条件节点
        other_target_node = None
        if no_condition_events:
            # 有 set_params 节点的情况
            other_target_node = no_condition_events[0]
            if len(no_condition_events) > 1:
                logger.warning(f"⚠️  多个无条件 event 存在，仅使用第一个作为 other 分支: {other_target_node}")
        else:
            # 没有 set_params 节点，但有无条件 event 的情况，直接使用目标
            no_condition_event_infos = [info for info in event_infos if not info.get("condition_key") and not info.get("set_param_node_name")]
            if no_condition_event_infos:
                first_no_cond_info = no_condition_event_infos[0]
                page_id_for_target = first_no_cond_info.get("resolved_page_id") or first_no_cond_info.get("target_page_id")
                if page_id_for_target:
                    other_target_node = f"page_{page_id_for_target[:8]}"
                elif first_no_cond_info.get("target_flow_id"):
                    other_target_node = f"jump_to_flow_{first_no_cond_info.get('target_flow_id')[:8]}"
                if len(no_condition_event_infos) > 1:
                    logger.warning(f"⚠️  多个无条件 event（无 set_params）存在，仅使用第一个作为 other 分支: {other_target_node}")
        has_flow_condition_node = False
        if flow_conditions:
            cond_nodes, cond_edges, chain_source = build_flow_condition_chain(
                safe_intent_name=safe_intent_name,
                flow_conditions=flow_conditions,
                chain_source=chain_source,
                gen_node_name=lambda base: self._generate_unique_node_name(base, ""),
                target_page_id=target_page_id,
                target_flow_id=target_flow_id,
                filter_by_target=not bool(intent_id),
                branch_target_node_map=condition_target_map,
                other_target_node=other_target_node
            )
            all_nodes.extend(cond_nodes)
            all_edges.extend(cond_edges)
            has_flow_condition_node = bool(cond_nodes)
        elif other_target_node and len(event_infos) > 1:
            logger.warning("⚠️  存在多个 event 但没有可用条件，other 分支无法区分，默认仅连接一个目标")
        
        # 3. 收集该 intent 跳转到的所有 pages（递归收集）
        target_page_ids = set()
        for info in event_infos:
            evt_target_page_id = info.get("target_page_id")
            evt_target_flow_id = info.get("target_flow_id")
            if evt_target_page_id:
                print(f'   Target page ID: {evt_target_page_id}')
                print(f'   Page ID in map: {evt_target_page_id in page_id_map}')
                resolved_page_id = _resolve_page_id(evt_target_page_id)
                if resolved_page_id != evt_target_page_id:
                    print(f'   Found page by prefix, using: {resolved_page_id}')
                info["resolved_page_id"] = resolved_page_id
                if resolved_page_id in page_id_map:
                    collect_related_pages(resolved_page_id, page_id_map, target_page_ids)
                else:
                    print(f'   ⚠️  Warning: Target page {evt_target_page_id} not found in page_id_map')
                    print(f'   Available page IDs (first 5): {list(page_id_map.keys())[:5]}')
            else:
                info["resolved_page_id"] = None
                print(f'   ⚠️  Warning: No target_page_id provided for intent {intent_name}')
                if evt_target_flow_id:
                    print(f'   Target flow ID: {evt_target_flow_id} (but no target page)')
        
        print(f'   Collected {len(target_page_ids)} related pages')
        
        # 如果没有任何 pages 被收集，输出警告
        if not target_page_ids:
            logger.warning(f'   ⚠️  Warning: No pages collected for intent {intent_name}')
            logger.warning(f'      This workflow will only have a start node')
            if event_infos:
                sample_page_id = event_infos[0].get("target_page_id")
                sample_flow_id = event_infos[0].get("target_flow_id")
                print(f'      Sample target page ID: {sample_page_id}')
                print(f'      Sample target flow ID: {sample_flow_id}')
                if sample_page_id:
                    print(f'      Checking if page exists in map...')
                    # 检查 page_id_map 中是否有类似的 page
                    page_id_prefix = sample_page_id[:8] if len(sample_page_id) >= 8 else sample_page_id
                    similar_pages = [pid for pid in page_id_map.keys() if pid[:8] == page_id_prefix]
                    if similar_pages:
                        print(f'      Found similar pages with prefix {page_id_prefix}: {similar_pages[:3]}')
                    else:
                        print(f'      No similar pages found. Total pages in map: {len(page_id_map)}')
        
        # 4. 为每个 related page 生成节点
        page_id_to_entry = {}
        
        for page_id in target_page_ids:
            if page_id in page_id_map:
                page_data = page_id_map[page_id]
                print(f'   Processing page: {page_data.get("value", {}).get("displayName", page_id[:8])}')
                
                nodes, edges, entry_node_name = self.generate_workflow_from_page(page_data, lang, page_id_map, all_nodes)
                
                all_nodes.extend(nodes)
                all_edges.extend(edges)
                
                # 记录映射：如果 entry_node 是 jump 节点，找到该 page 的第一个非 jump 节点
                if entry_node_name:
                    # 检查 entry_node 是否是 jump 节点
                    entry_node_obj = next((n for n in nodes if n.get('name') == entry_node_name), None)
                    if entry_node_obj and entry_node_obj.get('type') == 'jump':
                        # entry_node 是 jump 节点，优先找带有 _is_entry_node 标记的节点，否则找第一个非 jump/transition 节点
                        found_non_jump = False
                        for node in nodes:
                            if node.get('_is_entry_node'):
                                page_id_to_entry[page_id[:8]] = node.get('name')
                                found_non_jump = True
                                break
                        if not found_non_jump:
                            transition_names = {n.get('name') for n in nodes if n.get('title', '').startswith('Set Parameters')}
                            for node in nodes:
                                if node.get('type') != 'jump' and node.get('name') and node.get('name') not in transition_names:
                                    page_id_to_entry[page_id[:8]] = node.get('name')
                                    found_non_jump = True
                                    break
                        if not found_non_jump:
                            # 如果找不到非 jump 节点，说明该 page 只有 jump 节点
                            # 这种情况下，应该记录 jump 节点作为 entry
                            # 因为 jump 节点可以有入边（其他节点可以连接到 jump 节点）
                            # 但是，jump 节点不能有出边（jump 节点不能作为 source）
                            page_id_to_entry[page_id[:8]] = entry_node_name
                            print(f'  ⚠️  Warning: Page {page_id[:8]} only has jump node, using jump node as entry: {entry_node_name}')
                    else:
                        # entry_node 不是 jump 节点，正常记录
                        page_id_to_entry[page_id[:8]] = entry_node_name
                elif nodes:
                    # entry_node_name 为 None，但有节点生成
                    # 优先选择带有 _is_entry_node 标记的节点，否则使用第一个非 transition 节点
                    entry_found = None
                    for node in nodes:
                        if node.get('_is_entry_node'):
                            entry_found = node.get('name')
                            break
                    if not entry_found:
                        # 查找第一个非 transition 节点（避免选择 Set Parameters）
                        transition_names = {n.get('name') for n in nodes if n.get('title', '').startswith('Set Parameters')}
                        for node in nodes:
                            node_name = node.get('name')
                            if node_name and node_name not in transition_names:
                                entry_found = node_name
                                break
                        if not entry_found:
                            entry_found = nodes[0].get('name')
                    if entry_found:
                        page_id_to_entry[page_id[:8]] = entry_found
                        print(f'  ⚠️  Warning: Page {page_id[:8]} entry_node_name was None, using: {entry_found}')
        
        # 4.1 构建 jump 节点（用于 targetFlowId）
        def _get_or_create_jump_node(target_flow_uuid: str) -> str:
            for node in all_nodes:
                if node.get("type") == "jump" and node.get("jump_flow_uuid") == target_flow_uuid:
                    return node.get("name")
            jump_node_name = f"jump_to_flow_{target_flow_uuid[:8]}"
            if any(n.get("name") == jump_node_name for n in all_nodes):
                jump_node_name = self._generate_unique_node_name(jump_node_name, "")
            jump_node = {
                "type": "jump",
                "name": jump_node_name,
                "title": f"jump_to_{target_flow_uuid[:8]}",
                "jump_type": "flow",
                "jump_robot_id": "",
                "jump_robot_name": "",
                "jump_carry_history_number": 5,
                "jump_flow_name": "",
                "jump_flow_uuid": target_flow_uuid,
                "jump_carry_userinput": True
            }
            all_nodes.append(jump_node)
            return jump_node_name
        
        # 4.2 如果没有条件节点，则直接连接到第一个有 set_params 节点的 event，或直接连接到目标
        if not has_flow_condition_node:
            # 解析 __NER_CONVERGE__ 特殊标记
            converge_sources = []
            actual_chain_source = chain_source
            if chain_source and chain_source.startswith("__NER_CONVERGE__"):
                converge_part = chain_source[len("__NER_CONVERGE__"):]
                converge_sources = [s.strip() for s in converge_part.split(",") if s.strip()]
                actual_chain_source = None  # 将由汇聚源替代
            
            if event_infos:
                first_info = event_infos[0]
                if len(event_infos) > 1:
                    logger.warning("⚠️  多个 event 但没有条件节点，默认连接第一个 event")
                
                # 如果有 set_params 节点，连接到 set_params 节点
                if first_info.get("set_param_node_name"):
                    target_node = first_info["set_param_node_name"]
                    if converge_sources:
                        # 多个源节点汇聚到目标
                        for src_node in converge_sources:
                            all_edges.append({
                                "source_node": src_node,
                                "target_node": target_node,
                                "connection_type": "default"
                            })
                        print(f'   ✅ Converge connected: [{", ".join(converge_sources)}] -> {target_node}')
                    else:
                        all_edges.append({
                            "source_node": actual_chain_source,
                            "target_node": target_node,
                            "connection_type": "default"
                        })
                        print(f'   ✅ Connected: {actual_chain_source} -> {target_node}')
                else:
                    # 没有 set_params 节点，直接连接到目标
                    target_name = None
                    page_id_for_edge = first_info.get("resolved_page_id") or first_info.get("target_page_id")
                    if page_id_for_edge:
                        target_name = f"page_{page_id_for_edge[:8]}"
                    elif first_info.get("target_flow_id"):
                        target_name = _get_or_create_jump_node(first_info.get("target_flow_id"))
                    if target_name:
                        if converge_sources:
                            # 多个源节点汇聚到目标
                            for src_node in converge_sources:
                                all_edges.append({
                                    "source_node": src_node,
                                    "target_node": target_name,
                                    "connection_type": "default"
                                })
                            print(f'   ✅ Converge connected: [{", ".join(converge_sources)}] -> {target_name}')
                        else:
                            all_edges.append({
                                "source_node": actual_chain_source,
                                "target_node": target_name,
                                "connection_type": "default"
                            })
                            print(f'   ✅ Connected: {actual_chain_source} -> {target_name}')
                    else:
                        logger.warning(f"⚠️  未找到 event 目标，跳过连接")
            else:
                print(f'   ⚠️  Warning: No events found, start node has no connection')
        
        # 4.3 set_params 节点连接到对应目标（page 或 flow）
        for info in event_infos:
            set_param_node_name = info.get("set_param_node_name")
            if not set_param_node_name:
                # 没有 set_params 节点，跳过（已在 4.2 中直接连接到目标）
                continue
            
            target_name = None
            page_id_for_edge = info.get("resolved_page_id") or info.get("target_page_id")
            if page_id_for_edge:
                target_name = f"page_{page_id_for_edge[:8]}"
            elif info.get("target_flow_id"):
                target_name = _get_or_create_jump_node(info.get("target_flow_id"))
            if target_name:
                all_edges.append({
                    "source_node": set_param_node_name,
                    "target_node": target_name,
                    "connection_type": "default"
                })
            else:
                logger.warning(f"⚠️  未找到 event 目标，跳过连接: {set_param_node_name}")
        
        # 5. 解析 page 引用并过滤无效边
        # 首先，找到所有 jump 节点名称和 condition 节点名称
        jump_node_names = {node.get('name') for node in all_nodes if node.get('type') == 'jump'}
        condition_node_names = {node.get('name') for node in all_nodes if node.get('type') == 'condition'}
        
        # 先解析 page_xxx 引用
        # 使用列表副本，以便在迭代时删除元素
        edges_to_remove = []
        for edge in all_edges:
            target = edge.get('target_node', '')
            source = edge.get('source_node', '')
            # 如果 source_node 是 jump 节点，标记删除这条边（jump 节点不应该有出边）
            if source in jump_node_names:
                edges_to_remove.append(edge)
                print(f'  ⚠️  Warning: Marking edge for removal (source is jump node): {source} -> {target}')
                continue
            if target.startswith('page_'):
                page_prefix = target.replace('page_', '')
                if page_prefix in page_id_to_entry:
                    resolved_target = page_id_to_entry[page_prefix]
                    # jump 节点可以有入边（其他节点可以连接到 jump 节点）
                    # 但是，jump 节点不能有出边（jump 节点不能作为 source）
                    # 所以，如果 source 是 jump 节点，不应该创建这条边
                    if source in jump_node_names:
                        edges_to_remove.append(edge)
                        print(f'  ⚠️  Warning: Marking edge for removal (source is jump node, jump nodes should not have outgoing edges): {source} -> {resolved_target}')
                        continue
                    # 如果 source 不是 jump 节点，但是 resolved_target 是 jump 节点，这是允许的（因为 jump 节点可以作为 target）
                    # 所以，只需要检查 source 是否是 jump 节点即可
                    edge['target_node'] = resolved_target
                else:
                    # 如果 page_xxx 没有被解析，说明该 page 没有被收集到 target_page_ids 中
                    # 需要收集该 page 并生成节点
                    print(f'  ⚠️  Warning: page_{page_prefix} not found in page_id_to_entry, collecting missing page...')
                    # 尝试从 page_id_map 中查找该 page
                    for page_id in page_id_map.keys():
                        if page_id[:8] == page_prefix:
                            if page_id not in target_page_ids:
                                target_page_ids.add(page_id)
                                page_data = page_id_map[page_id]
                                print(f'   Processing missing page: {page_data.get("value", {}).get("displayName", page_id[:8])}')
                                nodes, edges, entry_node_name = self.generate_workflow_from_page(page_data, lang, page_id_map, all_nodes)
                                all_nodes.extend(nodes)
                                all_edges.extend(edges)
                                if entry_node_name:
                                    entry_node_obj = next((n for n in nodes if n.get('name') == entry_node_name), None)
                                    if entry_node_obj and entry_node_obj.get('type') == 'jump':
                                        # entry_node 是 jump 节点，优先找带有 _is_entry_node 标记的节点
                                        found_non_jump = False
                                        for node in nodes:
                                            if node.get('_is_entry_node'):
                                                page_id_to_entry[page_id[:8]] = node.get('name')
                                                found_non_jump = True
                                                break
                                        if not found_non_jump:
                                            transition_names = {n.get('name') for n in nodes if n.get('title', '').startswith('Set Parameters')}
                                            for node in nodes:
                                                if node.get('type') != 'jump' and node.get('name') and node.get('name') not in transition_names:
                                                    page_id_to_entry[page_id[:8]] = node.get('name')
                                                    found_non_jump = True
                                                    break
                                        if not found_non_jump:
                                            page_id_to_entry[page_id[:8]] = entry_node_name
                                            print(f'  ⚠️  Warning: Page {page_id[:8]} only has jump node, using jump node as entry: {entry_node_name}')
                                    else:
                                        page_id_to_entry[page_id[:8]] = entry_node_name
                                elif nodes:
                                    # entry_node_name 为 None，但有节点生成
                                    # 优先选择带有 _is_entry_node 标记的节点
                                    entry_found = None
                                    for node in nodes:
                                        if node.get('_is_entry_node'):
                                            entry_found = node.get('name')
                                            break
                                    if not entry_found:
                                        transition_names = {n.get('name') for n in nodes if n.get('title', '').startswith('Set Parameters')}
                                        for node in nodes:
                                            node_name = node.get('name')
                                            if node_name and node_name not in transition_names:
                                                entry_found = node_name
                                                break
                                        if not entry_found:
                                            entry_found = nodes[0].get('name')
                                    if entry_found:
                                        page_id_to_entry[page_id[:8]] = entry_found
                                        print(f'  ⚠️  Warning: Missing page {page_id[:8]} entry_node_name was None, using: {entry_found}')
                                # 更新 jump_node_names 和 condition_node_names（因为可能添加了新的节点）
                                jump_node_names.update({node.get('name') for node in nodes if node.get('type') == 'jump'})
                                condition_node_names.update({node.get('name') for node in nodes if node.get('type') == 'condition'})
                                # 重新解析该边
                                if page_prefix in page_id_to_entry:
                                    resolved_target = page_id_to_entry[page_prefix]
                                    # jump 节点可以有入边（其他节点可以连接到 jump 节点）
                                    # 但是，jump 节点不能有出边（jump 节点不能作为 source）
                                    # 所以，如果 source 是 jump 节点，删除这条边
                                    if source in jump_node_names:
                                        # 从 all_edges 中删除这条边
                                        all_edges.remove(edge)
                                        print(f'  ⚠️  Warning: Removing edge from jump node (jump nodes should not have outgoing edges): {source} -> {resolved_target}')
                                    else:
                                        # 如果 source 不是 jump 节点，但是 resolved_target 是 jump 节点，这是允许的（因为 jump 节点可以作为 target）
                                        edge['target_node'] = resolved_target
                                else:
                                    # 如果 page_xxx 没有被解析，说明该 page 没有被收集到 target_page_ids 中
                                    # 或者该 page 只有 jump 节点但没有被记录
                                    # 这种情况下，如果 source 是 jump 节点，不应该创建这条边
                                    if source in jump_node_names:
                                        edges_to_remove.append(edge)
                                        print(f'  ⚠️  Warning: Marking edge for removal (source is jump node and page {page_prefix} has no entry): {source} -> {target}')
                                    # 否则，保留这条边，等待后续处理
                            break
        
        # 删除标记的边
        for edge in edges_to_remove:
            if edge in all_edges:
                all_edges.remove(edge)
                print(f'  ✅ Removed edge from jump node: {edge.get("source_node")} -> {edge.get("target_node")}')
        
        # 过滤无效边并去重
        # 首先，重新收集所有 jump 节点名称（确保包含所有节点）
        jump_node_names = {node.get('name') for node in all_nodes if node.get('type') == 'jump'}
        
        # 收集所有有效的边（先不过滤重复）
        valid_edges = []
        for edge in all_edges:
            target = edge.get('target_node', '')
            source = edge.get('source_node', '')
            
            # 过滤掉所有以 jump 节点作为 source_node 的边（jump 节点不应该有出边）
            if source in jump_node_names:
                print(f'  ⚠️  Warning: Skipping edge from jump node (jump nodes should not have outgoing edges): {source} -> {target}')
                continue
            
            # 检查并过滤掉自己到自己的边
            if source and target and source == target:
                print(f'  ⚠️  Warning: Skipping self-loop edge: {source} -> {target}')
                continue
            
            # 检查并过滤掉 jump 节点到 jump 节点的边
            if source in jump_node_names and target in jump_node_names:
                print(f'  ⚠️  Warning: Skipping edge between jump nodes: {source} -> {target}')
                continue
            
            valid_edges.append(edge)
        
        # 然后，对于非 condition 节点，去重 default 连接
        fixed_edges = []
        # 记录每个 source_node 的 default 出边（用于去重，condition 节点除外）
        source_default_edges = {}  # source_node -> edge (只保留一条 default 出边)
        source_condition_targets = {}  # source_node -> set of target_nodes (用于 condition 连接去重)
        
        for edge in valid_edges:
            target = edge.get('target_node', '')
            source = edge.get('source_node', '')
            connection_type = edge.get('connection_type', 'default')
            
            # 再次检查：过滤掉所有以 jump 节点作为 source_node 的边（jump 节点不应该有出边）
            if source in jump_node_names:
                print(f'  ⚠️  Warning: Skipping edge from jump node (jump nodes should not have outgoing edges): {source} -> {target}')
                continue
            
            # 对于非 condition 节点，检查是否有重复的出边
            if source not in condition_node_names:
                # 对于 default 连接，非 condition 节点应该只有一条出边
                if connection_type == 'default':
                    if source not in source_default_edges:
                        source_default_edges[source] = edge
                    else:
                        # 已经有 default 出边，需要决定保留哪一条
                        existing_edge = source_default_edges[source]
                        existing_target = existing_edge.get('target_node', '')
                        
                        # 优先级：非 jump 节点 > jump 节点
                        # 如果现有的是 jump 节点，新的不是 jump 节点，替换
                        if existing_target in jump_node_names and target not in jump_node_names:
                            print(f'  ⚠️  Warning: Replacing default edge (preferring page over jump): {source} -> {existing_target} -> {target}')
                            source_default_edges[source] = edge
                        # 如果现有的是非 jump 节点，新的也是非 jump 节点，保留现有的（按出现顺序）
                        elif existing_target not in jump_node_names and target not in jump_node_names:
                            print(f'  ⚠️  Warning: Skipping duplicate default edge (non-condition node should have only one default outgoing edge): {source} -> {target} (keeping: {source} -> {existing_target})')
                            continue
                        # 如果现有的是非 jump 节点，新的是 jump 节点，保留现有的
                        elif existing_target not in jump_node_names and target in jump_node_names:
                            print(f'  ⚠️  Warning: Skipping edge (preferring page over jump): {source} -> {target} (keeping: {source} -> {existing_target})')
                            continue
                        # 如果现有的和新的都是 jump 节点，保留现有的
                        else:
                            print(f'  ⚠️  Warning: Skipping duplicate default edge to jump node: {source} -> {target} (keeping: {source} -> {existing_target})')
                            continue
                else:
                    # 对于 condition 连接，检查是否有相同的 target_node
                    if source not in source_condition_targets:
                        source_condition_targets[source] = set()
                    if target in source_condition_targets[source]:
                        print(f'  ⚠️  Warning: Skipping duplicate condition edge (non-condition node should not have multiple condition edges to same target): {source} -> {target}')
                        continue
                    source_condition_targets[source].add(target)
                    fixed_edges.append(edge)
            else:
                # condition 节点可以有多个出边
                fixed_edges.append(edge)
        
        # 添加所有保留的 default 出边
        for edge in source_default_edges.values():
            fixed_edges.append(edge)
        
        all_edges = fixed_edges
        
        # 6. 保存 workflow 文件
        import os
        output_nodes_file = os.path.join(output_dir, f'nodes_config_{safe_intent_name}.json')
        output_edges_file = os.path.join(output_dir, f'edge_config_{safe_intent_name}.json')
        
        nodes_config = {"nodes": all_nodes}
        
        # write by senlin.deng 2026-01-18
        # 兜底的删除空条件 condition 节点，并重连上下游
        # 后处理：删除空条件 condition 节点，并重连上下游
        all_nodes, all_edges = remove_empty_condition_nodes(all_nodes, all_edges, verbose=True)

        # 后处理：过滤掉所有无效的边
        filtered_edges = filter_invalid_edges(all_edges, all_nodes)
        edges_config = {"edges": filtered_edges}
        
        with open(output_nodes_file, 'w', encoding='utf-8') as f:
            json.dump(nodes_config, f, ensure_ascii=False, indent=2)
        
        with open(output_edges_file, 'w', encoding='utf-8') as f:
            json.dump(edges_config, f, ensure_ascii=False, indent=2)
        
        return safe_intent_name
    
    def _collect_related_pages(
        self,
        page_id: str,
        page_id_map: Dict[str, Any],
        collected: set,
        max_depth: int = 10,
        current_depth: int = 0
    ):
        """
        递归收集与该 page 相关的所有 pages（通过 transitionEvents 跳转）
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
    
    def _extract_all_target_flow_ids_from_flow(
        self, 
        flow_data: Dict[str, Any],
        exclude_flow_ids: set = None
    ) -> List[Dict[str, Any]]:
        """
        从 flow 数据中提取所有没有 targetPageId 的 targetFlowId
        包括：
        1. agentTransitionRouteGroups 中的 targetFlowId
        2. flow.conversationEvents 中的 targetFlowId
        3. 其他可能遗漏的 targetFlowId
        
        Args:
            flow_data: flow 数据字典
            exclude_flow_ids: 要排除的 targetFlowId 集合（已经在 flow.flow.transitionEvents 中处理过的）
            
        Returns:
            jump_nodes: jump 节点列表
        """
        if exclude_flow_ids is None:
            exclude_flow_ids = set()
        
        # 先构建 flow_id 到 flow_name 的映射
        flow_id_to_name = {}
        # 从 flow.flow.flowId 和 flow.flow.displayName 获取主 flow
        main_flow = flow_data.get("flow", {}).get("flow", {})
        if main_flow:
            flow_id = main_flow.get("flowId")
            flow_name = main_flow.get("displayName", "")
            if flow_id and flow_name:
                flow_id_to_name[flow_id] = flow_name
        
        # 从 flow.flows 数组中获取所有 flow
        flows = flow_data.get("flow", {}).get("flows", [])
        for flow_item in flows:
            flow_key = flow_item.get("key", "")
            flow_value = flow_item.get("value", {})
            flow_name = flow_value.get("displayName", "")
            if flow_key and flow_name:
                flow_id_to_name[flow_key] = flow_name
        
        jump_nodes = []
        target_flow_ids_seen = set()
        
        def find_target_flow_ids(obj, path=""):
            """递归查找所有 targetFlowId"""
            if isinstance(obj, dict):
                # 检查是否有 targetFlowId
                if "targetFlowId" in obj:
                    target_flow_id = obj.get("targetFlowId")
                    target_page_id = obj.get("targetPageId")
                    
                    # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
                    if target_flow_id and not target_page_id:
                        # 排除已经在 flow.flow.transitionEvents 中处理过的 targetFlowId
                        if target_flow_id in exclude_flow_ids:
                            return
                        
                        # 避免重复添加相同的 targetFlowId
                        if target_flow_id not in target_flow_ids_seen:
                            target_flow_ids_seen.add(target_flow_id)
                            
                            # 尝试从 flow_id_to_name 映射中获取 flow name
                            flow_name = flow_id_to_name.get(target_flow_id, "")
                            if flow_name:
                                # 使用 flow name 生成节点名称（清理特殊字符）
                                import re
                                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', flow_name)
                                safe_name = re.sub(r'_+', '_', safe_name).strip('_')
                                jump_node_name = f"jump_to_flow_{safe_name}"
                            else:
                                # 如果找不到 flow name，使用 flow ID 的前8位
                                jump_node_name = f"jump_to_flow_{target_flow_id[:8]}"
                            
                            # 确保节点名称唯一
                            counter = 0
                            original_name = jump_node_name
                            while any(n.get("name") == jump_node_name for n in jump_nodes):
                                counter += 1
                                jump_node_name = f"{original_name}_{counter}"
                            
                            # 生成 title: 使用 jump_to_DisplayName 格式
                            if flow_name:
                                title = f"jump_to_{flow_name}"
                            else:
                                title = f"jump_to_{target_flow_id[:8]}"
                            
                            jump_node = {
                                "type": "jump",
                                "name": jump_node_name,
                                "title": title,
                                "jump_type": "flow",
                                "jump_robot_id": "",
                                "jump_robot_name": "",
                                "jump_carry_history_number": 5,
                                "jump_flow_name": flow_name,  # 保存 flow name
                                "jump_flow_uuid": target_flow_id,
                                "jump_carry_userinput": True
                            }
                            
                            jump_nodes.append(jump_node)
                            if flow_name:
                                print(f'  ✅ Found targetFlowId: {target_flow_id[:8]}... -> {flow_name} (from {path[:50]})')
                            else:
                                print(f'  ✅ Found targetFlowId: {target_flow_id[:8]}... (from {path[:50]}, name not found)')
                
                # 递归处理所有值
                for key, value in obj.items():
                    find_target_flow_ids(value, f"{path}.{key}" if path else key)
            
            elif isinstance(obj, list):
                for idx, item in enumerate(obj):
                    find_target_flow_ids(item, f"{path}[{idx}]")
        
        # 递归查找所有 targetFlowId
        find_target_flow_ids(flow_data)
        
        return jump_nodes
    
    def _add_jump_nodes_to_all_workflows(
        self,
        output_dir: str,
        generated_workflows: List[str],
        jump_nodes: List[Dict[str, Any]],
        flow_data: Dict[str, Any] = None
    ):
        """
        将 jump 节点添加到所有生成的 workflow 文件中，并添加对应的边连接
        
        Args:
            output_dir: 输出目录
            generated_workflows: 生成的 workflow 名称列表
            jump_nodes: 要添加的 jump 节点列表
            flow_data: flow 数据字典（用于查找 page 和添加边连接）
        """
        import os
        
        if not jump_nodes:
            return
        
        print(f'\n[ADDING] Adding {len(jump_nodes)} jump nodes to all workflow files...')
        
        # 为每个 workflow 文件添加 jump 节点和边
        for workflow_name in generated_workflows:
            nodes_file = os.path.join(output_dir, f'nodes_config_{workflow_name}.json')
            edges_file = os.path.join(output_dir, f'edge_config_{workflow_name}.json')
            
            if not os.path.exists(nodes_file):
                print(f'  ⚠️  Warning: {nodes_file} not found, skipping')
                continue
            
            try:
                # 读取现有的 nodes 配置
                with open(nodes_file, 'r', encoding='utf-8') as f:
                    nodes_config = json.load(f)
                
                existing_nodes = nodes_config.get("nodes", [])
                
                # 检查哪些 jump 节点已经存在
                existing_jump_uuids = {
                    node.get("jump_flow_uuid") 
                    for node in existing_nodes 
                    if node.get("type") == "jump" and node.get("jump_flow_uuid")
                }
                
                # 只添加不存在的 jump 节点
                new_jump_nodes = [
                    node for node in jump_nodes 
                    if node.get("jump_flow_uuid") not in existing_jump_uuids
                ]
                
                if new_jump_nodes:
                    # 确保节点名称唯一
                    existing_names = {n.get("name") for n in existing_nodes}
                    for jump_node in new_jump_nodes:
                        original_name = jump_node["name"]
                        counter = 0
                        while jump_node["name"] in existing_names:
                            counter += 1
                            jump_node["name"] = f"{original_name}_{counter}"
                        existing_names.add(jump_node["name"])
                    
                    # 添加新的 jump 节点
                    existing_nodes.extend(new_jump_nodes)
                    nodes_config["nodes"] = existing_nodes
                    
                    # 保存更新后的 nodes 文件
                    with open(nodes_file, 'w', encoding='utf-8') as f:
                        json.dump(nodes_config, f, ensure_ascii=False, indent=2)
                    
                    print(f'  ✅ Added {len(new_jump_nodes)} jump nodes to {workflow_name}')
                    
                    # 添加边连接（为新添加的 jump 节点）
                    if os.path.exists(edges_file) and flow_data:
                        self._add_edges_for_jump_nodes(
                            edges_file, 
                            new_jump_nodes, 
                            existing_nodes, 
                            flow_data,
                            nodes_file
                        )
                else:
                    print(f'  ⏭️  All jump nodes already exist in {workflow_name}')
                
                # 无论是否添加了新节点，都要检查并添加边连接（因为可能之前没有添加边）
                if os.path.exists(edges_file) and flow_data:
                    # 获取所有 jump 节点（包括已存在的和新添加的）
                    all_jump_nodes_in_workflow = [
                        node for node in existing_nodes 
                        if node.get("type") == "jump" and node.get("jump_flow_uuid")
                    ]
                    if all_jump_nodes_in_workflow:
                        print(f'  [CHECKING] Checking edges for {len(all_jump_nodes_in_workflow)} jump nodes...')
                        self._add_edges_for_jump_nodes(
                            edges_file, 
                            all_jump_nodes_in_workflow, 
                            existing_nodes, 
                            flow_data,
                            nodes_file
                        )
            
            except Exception as e:
                print(f'  ❌ Error adding jump nodes to {workflow_name}: {e}')
                import traceback
                traceback.print_exc()
        
        # 如果存在合并的 nodes_config.json，也添加 jump 节点
        merged_nodes_file = os.path.join(output_dir, 'nodes_config.json')
        if os.path.exists(merged_nodes_file):
            try:
                with open(merged_nodes_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                
                existing_nodes = config_data.get("nodes", [])
                
                # 检查哪些 jump 节点已经存在
                existing_jump_uuids = {
                    node.get("jump_flow_uuid") 
                    for node in existing_nodes 
                    if node.get("type") == "jump" and node.get("jump_flow_uuid")
                }
                
                # 只添加不存在的 jump 节点
                new_jump_nodes = [
                    node for node in jump_nodes 
                    if node.get("jump_flow_uuid") not in existing_jump_uuids
                ]
                
                if new_jump_nodes:
                    # 确保节点名称唯一
                    existing_names = {n.get("name") for n in existing_nodes}
                    for jump_node in new_jump_nodes:
                        original_name = jump_node["name"]
                        counter = 0
                        while jump_node["name"] in existing_names:
                            counter += 1
                            jump_node["name"] = f"{original_name}_{counter}"
                        existing_names.add(jump_node["name"])
                    
                    # 添加新的 jump 节点
                    existing_nodes.extend(new_jump_nodes)
                    config_data["nodes"] = existing_nodes
                    
                    # 保存更新后的文件
                    with open(merged_nodes_file, 'w', encoding='utf-8') as f:
                        json.dump(config_data, f, ensure_ascii=False, indent=2)
                    
                    print(f'  ✅ Added {len(new_jump_nodes)} jump nodes to merged nodes_config.json')
                else:
                    print(f'  ⏭️  All jump nodes already exist in merged nodes_config.json')
            
            except Exception as e:
                print(f'  ❌ Error adding jump nodes to merged nodes_config.json: {e}')
                import traceback
                traceback.print_exc()
    
    def _add_edges_for_jump_nodes(
        self,
        edges_file: str,
        jump_nodes: List[Dict[str, Any]],
        all_nodes: List[Dict[str, Any]],
        flow_data: Dict[str, Any],
        nodes_file: str = None
    ):
        """
        为 jump 节点添加边连接
        从 exported_flow 中找到有 targetFlowId 的 page，然后找到这些 page 的最后一个节点，连接到 jump 节点
        如果条件右侧值为 true，直接跳转（不需要条件判断）
        
        Args:
            edges_file: edge_config 文件路径
            jump_nodes: 要添加边的 jump 节点列表
            all_nodes: 所有节点列表
            flow_data: flow 数据字典
            nodes_file: nodes_config 文件路径（可选，如果提供了，新创建的 jump 节点会被保存）
        """
        import json
        
        # 读取现有的 edges 配置
        with open(edges_file, 'r', encoding='utf-8') as f:
            edges_config = json.load(f)
        
        edges = edges_config.get("edges", [])
        existing_edges = {(e.get("source_node"), e.get("target_node"), e.get("condition_id")) for e in edges}
        
        # 创建 jump_flow_uuid 到 jump_node 的映射
        jump_nodes_map = {node.get("jump_flow_uuid"): node for node in jump_nodes}
        
        # 从 flow_data 中找到所有有 targetFlowId 的 page
        # 尝试多种路径：flow.flow.pages 或 flow.pages
        flow_obj = flow_data.get("flow", {})
        pages = flow_obj.get("flow", {}).get("pages", []) or flow_obj.get("pages", [])
        
        print(f'  [DEBUG] Found {len(pages)} pages in flow_data')
        print(f'  [DEBUG] Found {len(jump_nodes_map)} jump nodes in map')
        
        new_edges = []
        
        for page_item in pages:
            page_key = page_item.get("key", "")
            page_value = page_item.get("value", {})
            page_display_name = page_value.get("displayName", "")
            transition_events = page_value.get("transitionEvents", [])
            
            for event in transition_events:
                handler = event.get("transitionEventHandler", {})
                target_flow_id = handler.get("targetFlowId")
                target_page_id = handler.get("targetPageId")
                condition = event.get("condition", {})
                
                # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
                if target_flow_id and not target_page_id:
                    jump_node = jump_nodes_map.get(target_flow_id)
                    
                    # 如果找到了 jump 节点，但 jump_flow_name 为空，使用 page 的 displayName 更新它
                    if jump_node and page_display_name and not jump_node.get("jump_flow_name"):
                        # 更新 title 和 jump_flow_name
                        jump_node["title"] = f"jump_to_{page_display_name}"
                        jump_node["jump_flow_name"] = page_display_name
                        print(f'    ✅ Updated jump node {jump_node.get("name")} with page displayName: {page_display_name}')
                        
                        # 如果提供了 nodes_file，也更新文件中的节点
                        if nodes_file:
                            import os
                            if os.path.exists(nodes_file):
                                try:
                                    with open(nodes_file, 'r', encoding='utf-8') as f:
                                        nodes_config = json.load(f)
                                    existing_nodes_in_file = nodes_config.get("nodes", [])
                                    
                                    # 找到对应的节点并更新
                                    for n in existing_nodes_in_file:
                                        if n.get("jump_flow_uuid") == target_flow_id:
                                            n["title"] = jump_node["title"]
                                            n["jump_flow_name"] = jump_node["jump_flow_name"]
                                            break
                                    
                                    nodes_config["nodes"] = existing_nodes_in_file
                                    with open(nodes_file, 'w', encoding='utf-8') as f:
                                        json.dump(nodes_config, f, ensure_ascii=False, indent=2)
                                    
                                    print(f'    💾 Updated jump node in {nodes_file}')
                                except Exception as e:
                                    print(f'    ⚠️  Warning: Failed to update jump node in {nodes_file}: {e}')
                    
                    # 如果找不到 jump 节点，创建一个新的（使用 page 的 displayName）
                    if not jump_node:
                        import re
                        # 使用 page 的 displayName 生成节点名称
                        if page_display_name:
                            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', page_display_name)
                            safe_name = re.sub(r'_+', '_', safe_name).strip('_')
                            jump_node_name = f"jump_to_flow_{safe_name}"
                        else:
                            # 如果找不到 displayName，使用 flow ID 的前8位
                            jump_node_name = f"jump_to_flow_{target_flow_id[:8]}"
                        
                        # 确保节点名称唯一
                        existing_node_names = {n.get("name") for n in all_nodes}
                        counter = 0
                        original_name = jump_node_name
                        while jump_node_name in existing_node_names:
                            counter += 1
                            jump_node_name = f"{original_name}_{counter}"
                        
                        # 创建新的 jump 节点
                        # 生成 title: 使用 jump_to_DisplayName 格式
                        if page_display_name:
                            title = f"jump_to_{page_display_name}"
                        else:
                            title = f"jump_to_{target_flow_id[:8]}"
                        
                        jump_node = {
                            "type": "jump",
                            "name": jump_node_name,
                            "title": title,
                            "jump_type": "flow",
                            "jump_robot_id": "",
                            "jump_robot_name": "",
                            "jump_carry_history_number": 5,
                            "jump_flow_name": page_display_name if page_display_name else "",
                            "jump_flow_uuid": target_flow_id,
                            "jump_carry_userinput": True
                        }
                        
                        # 添加到 jump_nodes 列表和映射中
                        jump_nodes.append(jump_node)
                        jump_nodes_map[target_flow_id] = jump_node
                        all_nodes.append(jump_node)
                        
                        print(f'    ✅ Created new jump node: {jump_node_name} for targetFlowId {target_flow_id[:8]}... (from page: {page_display_name or page_key[:8]})')
                        
                        # 如果提供了 nodes_file，保存新创建的 jump 节点或更新已存在的节点
                        if nodes_file:
                            import os
                            if os.path.exists(nodes_file):
                                try:
                                    with open(nodes_file, 'r', encoding='utf-8') as f:
                                        nodes_config = json.load(f)
                                    existing_nodes_in_file = nodes_config.get("nodes", [])
                                    
                                    # 检查是否已经存在（通过 jump_flow_uuid）
                                    existing_node_in_file = None
                                    for n in existing_nodes_in_file:
                                        if n.get("jump_flow_uuid") == target_flow_id:
                                            existing_node_in_file = n
                                            break
                                    
                                    if existing_node_in_file:
                                        # 更新已存在的节点
                                        existing_node_in_file["title"] = jump_node["title"]
                                        existing_node_in_file["jump_flow_name"] = jump_node["jump_flow_name"]
                                        print(f'    💾 Updated existing jump node in {nodes_file}')
                                    else:
                                        # 添加新节点
                                        existing_nodes_in_file.append(jump_node)
                                        print(f'    💾 Saved new jump node to {nodes_file}')
                                    
                                    nodes_config["nodes"] = existing_nodes_in_file
                                    with open(nodes_file, 'w', encoding='utf-8') as f:
                                        json.dump(nodes_config, f, ensure_ascii=False, indent=2)
                                except Exception as e:
                                    print(f'    ⚠️  Warning: Failed to save/update jump node in {nodes_file}: {e}')
                    
                    if jump_node:
                        jump_node_name = jump_node.get("name")
                        
                        # 检查条件：如果右侧值为 true，直接跳转（不需要条件判断）
                        # 支持多种格式：condition.restriction 或 condition 直接
                        restriction = condition.get("restriction", condition)
                        condition_rhs = restriction.get("rhs", {}) if isinstance(restriction, dict) else {}
                        condition_value = condition_rhs.get("value") if isinstance(condition_rhs, dict) else None
                        comparator = restriction.get("comparator", "") if isinstance(restriction, dict) else condition.get("comparator", "")
                        condition_string = event.get("conditionString", "")
                        is_literal_condition = condition_string.strip().lower() in ("true", "false") if condition_string else False
                        
                        # 判断是否为始终为 true 的条件
                        is_always_true = (
                            (comparator == "GLOBAL" and condition_value == "true") or
                            (comparator == "GLOBAL" and condition_value == True) or
                            (condition_value == "true") or
                            (condition_value == True) or
                            (not condition or (not restriction and not condition.get("comparator")))  # 没有条件也视为直接跳转
                        )
                        # Literal conditionString should not bypass condition nodes.
                        if is_literal_condition:
                            is_always_true = False
                        
                        # 找到该 page 的最后一个节点
                        page_prefix = page_key[:8] if page_key else ""
                        if not page_prefix:
                            print(f'    ⚠️  Warning: Empty page_key for page {page_value.get("displayName", "Unknown")}')
                            continue
                        
                        page_last_node = self._find_page_last_node(
                            page_prefix, 
                            all_nodes, 
                            edges
                        )
                        
                        if page_last_node:
                            # 检查是否已经有边连接到这个 jump 节点
                            edge_key = (page_last_node, jump_node_name, None)
                            if edge_key not in existing_edges:
                                if is_always_true:
                                    # 直接跳转（default 连接）
                                    new_edges.append({
                                        "source_node": page_last_node,
                                        "target_node": jump_node_name,
                                        "connection_type": "default"
                                    })
                                    print(f'    ✅ Added direct edge (always true): {page_last_node} → {jump_node_name} (page: {page_prefix})')
                                else:
                                    # 需要条件判断（condition 连接）
                                    condition_id = condition.get("conditionId") or event.get("name") or f"jump_to_{target_flow_id[:8]}"
                                    new_edges.append({
                                        "source_node": page_last_node,
                                        "target_node": jump_node_name,
                                        "connection_type": "condition",
                                        "condition_id": condition_id
                                    })
                                    print(f'    ✅ Added conditional edge: {page_last_node} → {jump_node_name} (condition: {condition_id}, page: {page_prefix})')
                        else:
                            print(f'    ⚠️  Warning: Could not find last node for page {page_prefix} ({page_value.get("displayName", "Unknown")})')
                    else:
                        print(f'    ⚠️  Warning: Jump node not found for targetFlowId {target_flow_id[:8]}... (page: {page_value.get("displayName", "Unknown")})')
        
        # 方法2: 从条件分支中查找，如果有 target_flow_id 且无 target_page_id，就连接到 jump 节点
        print(f'  [METHOD 2] Checking condition branches for jump connections...')
        for node in all_nodes:
            if node.get("type") == "condition":
                condition_name = node.get("name")
                if_else_conditions = node.get("if_else_conditions", [])

                # writed by senlin.deng 2026-01-17
                # 检查是否是 Pure Condition Routing 节点（混合路由中的纯条件判断节点）
                is_pure_condition_routing = node.get("title") == "Condition Routing" and any(
                    branch.get("_next_node") for branch in if_else_conditions
                )

                for branch in if_else_conditions:
                    target_flow_id = branch.get("target_flow_id")
                    target_page_id = branch.get("target_page_id")
                    condition_id = branch.get("condition_id")

                    # writed by senlin.deng 2026-01-17
                    # Pure Condition Routing 的 Other 分支（有 _next_node）只连接到 Fallback Message，不走通用逻辑
                    if is_pure_condition_routing and branch.get("_next_node"):
                        logger.debug(f"    🔀 跳过 Pure Condition Routing 的 Other 分支通用边生成: {condition_name} [{branch.get('condition_name')}] → {branch.get('_next_node')}")
                        continue

                    # 只有当有 targetFlowId 且无 targetPageId 时才需要连接到 jump 节点
                    if target_flow_id and not target_page_id:
                        jump_node = jump_nodes_map.get(target_flow_id)
                        if not jump_node:
                            # 兜底：如果没有 jump 节点，创建一个最小可用的 jump 节点
                            jump_node_name = f"jump_to_flow_{target_flow_id[:8]}"
                            existing_node_names = {n.get("name") for n in all_nodes}
                            counter = 0
                            original_name = jump_node_name
                            while jump_node_name in existing_node_names:
                                counter += 1
                                jump_node_name = f"{original_name}_{counter}"

                            jump_node = {
                                "type": "jump",
                                "name": jump_node_name,
                                "title": f"jump_to_{target_flow_id[:8]}",
                                "jump_type": "flow",
                                "jump_robot_id": "",
                                "jump_robot_name": "",
                                "jump_carry_history_number": 5,
                                "jump_flow_name": "",
                                "jump_flow_uuid": target_flow_id,
                                "jump_carry_userinput": True
                            }

                            jump_nodes.append(jump_node)
                            jump_nodes_map[target_flow_id] = jump_node
                            all_nodes.append(jump_node)
                            print(f'    ✅ Created fallback jump node: {jump_node_name} for targetFlowId {target_flow_id[:8]}...')

                            if nodes_file:
                                import os
                                if os.path.exists(nodes_file):
                                    try:
                                        with open(nodes_file, 'r', encoding='utf-8') as f:
                                            nodes_config = json.load(f)
                                        existing_nodes_in_file = nodes_config.get("nodes", [])
                                        existing_nodes_in_file.append(jump_node)
                                        nodes_config["nodes"] = existing_nodes_in_file
                                        with open(nodes_file, 'w', encoding='utf-8') as f:
                                            json.dump(nodes_config, f, ensure_ascii=False, indent=2)
                                        print(f'    💾 Saved fallback jump node to {nodes_file}')
                                    except Exception as e:
                                        print(f'    ⚠️  Warning: Failed to save fallback jump node in {nodes_file}: {e}')

                        jump_node_name = jump_node.get("name")
                        transition_code_node = branch.get("transition_code_node")
                        transition_text_nodes = branch.get("transition_text_nodes", [])

                        # condition → (code?) → (text nodes?) → jump
                        first_target = transition_code_node or (transition_text_nodes[0] if transition_text_nodes else jump_node_name)
                        edge_key = (condition_name, first_target, condition_id)
                        if edge_key not in existing_edges:
                            new_edges.append({
                                "source_node": condition_name,
                                "target_node": first_target,
                                "connection_type": "condition",
                                "condition_id": condition_id
                            })
                            existing_edges.add(edge_key)
                            print(f'    ✅ Added edge: {condition_name} → {first_target} (condition: {condition_id})')

                        current_source = first_target
                        if transition_code_node and transition_text_nodes:
                            for text_node_name in transition_text_nodes:
                                edge_key = (current_source, text_node_name, None)
                                if edge_key in existing_edges:
                                    continue
                                new_edges.append({
                                    "source_node": current_source,
                                    "target_node": text_node_name,
                                    "connection_type": "default"
                                })
                                existing_edges.add(edge_key)
                                current_source = text_node_name
                        elif transition_text_nodes and first_target != jump_node_name:
                            for text_node_name in transition_text_nodes[1:]:
                                edge_key = (current_source, text_node_name, None)
                                if edge_key in existing_edges:
                                    continue
                                new_edges.append({
                                    "source_node": current_source,
                                    "target_node": text_node_name,
                                    "connection_type": "default"
                                })
                                existing_edges.add(edge_key)
                                current_source = text_node_name

                        # write by senlin.deng 2026-01-29
                        # 清理错误的边：不允许 code 节点直接连到非首个 BeforeTransition_Response
                        if transition_code_node and transition_text_nodes and len(transition_text_nodes) > 1:
                            invalid_targets = set(transition_text_nodes[1:])
                            edges = [
                                e for e in edges
                                if not (
                                    e.get("source_node") == transition_code_node
                                    and e.get("target_node") in invalid_targets
                                )
                            ]
                            new_edges = [
                                e for e in new_edges
                                if not (
                                    e.get("source_node") == transition_code_node
                                    and e.get("target_node") in invalid_targets
                                )
                            ]
                            for node_name in invalid_targets:
                                existing_edges.discard((transition_code_node, node_name, None))
                                if condition_id:
                                    existing_edges.discard((transition_code_node, node_name, condition_id))

                        if current_source != jump_node_name:
                            edge_key = (current_source, jump_node_name, None)
                            if edge_key not in existing_edges:
                                new_edges.append({
                                    "source_node": current_source,
                                    "target_node": jump_node_name,
                                    "connection_type": "default"
                                })
                                existing_edges.add(edge_key)

                        # write by senlin.deng 2026-01-26
                        # 如果有多个 BeforeTransition_Response 节点，移除非最后一个节点到 jump 的边
                        # 必须保证所有 BeforeTransition_Response 都连接完了，才能从最后一个连接到 jump 节点
                        if transition_text_nodes and len(transition_text_nodes) > 1:
                            last_text_node = transition_text_nodes[-1]
                            non_last_text_nodes = transition_text_nodes[:-1]
                            
                            # 移除非最后一个 BeforeTransition_Response 节点到 jump 的边（从 existing edges）
                            removed_bt_jump_edges = 0
                            filtered_existing = []
                            for e in edges:
                                if (
                                    e.get("source_node") in non_last_text_nodes
                                    and e.get("target_node") == jump_node_name
                                ):
                                    removed_bt_jump_edges += 1
                                    continue
                                filtered_existing.append(e)
                            if removed_bt_jump_edges:
                                edges = filtered_existing
                                print(f'    ✅ Removed {removed_bt_jump_edges} edge(s) from non-last BeforeTransition_Response to jump node')
                            
                            # 移除非最后一个 BeforeTransition_Response 节点到 jump 的边（从 new_edges）
                            removed_bt_jump_new = 0
                            filtered_new = []
                            for e in new_edges:
                                if (
                                    e.get("source_node") in non_last_text_nodes
                                    and e.get("target_node") == jump_node_name
                                ):
                                    removed_bt_jump_new += 1
                                    continue
                                filtered_new.append(e)
                            if removed_bt_jump_new:
                                new_edges = filtered_new
                                print(f'    ✅ Removed {removed_bt_jump_new} new edge(s) from non-last BeforeTransition_Response to jump node')
                            
                            # 从 existing_edges set 中移除这些边
                            for node_name in non_last_text_nodes:
                                existing_edges.discard((node_name, jump_node_name, None))
                                if condition_id:
                                    existing_edges.discard((node_name, jump_node_name, condition_id))

                        # 移除 condition 直接连到 jump 的边（仅当存在中间节点时）
                        # 如果没有中间节点（transition_code_node 和 transition_text_nodes 都为空），
                        # 则保留 condition 直接连到 jump 的边
                        if transition_code_node or transition_text_nodes:
                            removed_direct_edges = 0
                            filtered_existing = []
                            for e in edges:
                                if (
                                    e.get("source_node") == condition_name
                                    and e.get("target_node") == jump_node_name
                                ):
                                    removed_direct_edges += 1
                                    continue
                                filtered_existing.append(e)
                            if removed_direct_edges:
                                edges = filtered_existing
                                print(f'    ✅ Removed {removed_direct_edges} direct edge(s): {condition_name} → {jump_node_name} (has intermediate nodes)')
                        # write by senlin.deng 2026-01-22
                        # 如果同时存在 setParameterActions 与 staticUserResponse，
                        # 不允许 code 节点直接连 jump（必须经过 BeforeTransition_Response）
                        if transition_code_node and transition_text_nodes:
                            removed_code_jump = 0
                            filtered_existing = []
                            for e in edges:
                                if (
                                    e.get("source_node") == transition_code_node
                                    and e.get("target_node") == jump_node_name
                                ):
                                    removed_code_jump += 1
                                    continue
                                filtered_existing.append(e)
                            edges = filtered_existing

                            filtered_new = []
                            for e in new_edges:
                                if (
                                    e.get("source_node") == transition_code_node
                                    and e.get("target_node") == jump_node_name
                                ):
                                    removed_code_jump += 1
                                    continue
                                filtered_new.append(e)
                            new_edges = filtered_new

                            existing_edges.discard((transition_code_node, jump_node_name, None))
                            if condition_id:
                                existing_edges.discard((transition_code_node, jump_node_name, condition_id))

                            if removed_code_jump:
                                print(f'    ✅ Removed {removed_code_jump} direct edge(s): {transition_code_node} → {jump_node_name} (has beforeTransition response)')
        
        # 方法3: 检查是否有未连接的 jump 节点（仅用于调试，不添加备用边）
        print(f'  [METHOD 3] Checking for unconnected jump nodes...')
        # 找到所有已经有边的 jump 节点（包括新添加的边）
        jump_nodes_with_edges = {e.get("target_node") for e in edges if e.get("target_node", "").startswith("jump_to_flow")}
        jump_nodes_with_edges.update({e.get("target_node") for e in new_edges if e.get("target_node", "").startswith("jump_to_flow")})
        
        # 检查未连接的 jump 节点
        unconnected_jump_nodes = []
        for jump_node in jump_nodes:
            jump_node_name = jump_node.get("name")
            if jump_node_name not in jump_nodes_with_edges:
                unconnected_jump_nodes.append(jump_node_name)
        
        if unconnected_jump_nodes:
            print(f'    ⚠️  Warning: Found {len(unconnected_jump_nodes)} unconnected jump nodes: {", ".join(unconnected_jump_nodes)}')
            print(f'    ⚠️  These jump nodes may need manual connection or are not used in this workflow')
        else:
            print(f'    ✅ All jump nodes have connections')
        
        # 添加新边到 edges 列表
        if new_edges:
            edges.extend(new_edges)
        
        # write by senlin.deng 2026-01-21
        # 后处理：当同一目标存在多个 BeforeTransition_Response 入边时，仅保留最后一个
        # 仅对 page_xxx 目标执行去重；jump 节点允许多个 beforeTransition 入边
        before_transition_nodes = {
            n.get("name") for n in all_nodes
            if n.get("type") == "textReply" and n.get("title") == "BeforeTransition_Response"
        }
        if before_transition_nodes:
            node_order = {n.get("name"): idx for idx, n in enumerate(all_nodes) if n.get("name")}
            
            # 删除 BeforeTransition_Response 跳跃连接（只保留相邻的链路边）
            edges = [
                e for e in edges
                if not (
                    e.get("source_node") in before_transition_nodes
                    and e.get("target_node") in before_transition_nodes
                    and (
                        node_order.get(e.get("target_node"), -1)
                        - node_order.get(e.get("source_node"), -1)
                        != 1
                    )
                )
            ]
            
            # 记录每个 before_transition 节点是否指向另一个 before_transition（用于判断链路末端）
            bt_out_to_bt = {n: False for n in before_transition_nodes}
            for e in edges:
                src = e.get("source_node")
                tgt = e.get("target_node")
                if src in before_transition_nodes and tgt in before_transition_nodes:
                    bt_out_to_bt[src] = True
            
            # 目标节点为 page_xxx（jump 节点不做去重）
            targets = set()
            for e in edges:
                tgt = e.get("target_node")
                if tgt and tgt.startswith("page_"):
                    targets.add(tgt)
            
            # 为每个目标保留最后一个 BeforeTransition_Response
            for target in targets:
                incoming = [
                    e for e in edges
                    if e.get("target_node") == target and e.get("source_node") in before_transition_nodes
                ]
                if len(incoming) <= 1:
                    continue
                
                # 优先保留链路末端（不再指向其他 BeforeTransition_Response）
                keep_candidates = [
                    e for e in incoming if not bt_out_to_bt.get(e.get("source_node"))
                ]
                if not keep_candidates:
                    keep_candidates = incoming
                
                # 若仍有多个，按 all_nodes 中出现顺序保留最后一个
                keep_edge = max(
                    keep_candidates,
                    key=lambda e: node_order.get(e.get("source_node"), -1)
                )
                
                # 删除其他多余边
                removed_count = 0
                filtered_edges = []
                for e in edges:
                    if (
                        e.get("target_node") == target
                        and e.get("source_node") in before_transition_nodes
                        and e is not keep_edge
                    ):
                        removed_count += 1
                        continue
                    filtered_edges.append(e)
                edges = filtered_edges
                
                if removed_count > 0:
                    print(f'    ✅ Removed {removed_count} edge(s) from non-last BeforeTransition_Response to page {target}')
        # write by senlin.deng 2026-01-21
        # 对所有条件节点兜底：条件节点的 Other 分支若无出边，连接到 Jump to Main Agent
        # 仅当该 Other 分支没有任何出边时才补连
        condition_nodes = [
            n for n in all_nodes
            if n.get("type") == "condition" and n.get("if_else_conditions")
        ]
        if condition_nodes:
            # 复用已有的 Jump to Main Agent 节点
            jump_to_main_agent_nodes = {
                n.get("name"): n
                for n in all_nodes
                if n.get("type") == "jump" and n.get("title") == "Jump to Main Agent"
            }

            def _extract_page_prefix(node_name: str) -> str:
                if not node_name:
                    return ""
                import re
                match = re.search(r"[0-9a-fA-F]{8}", node_name)
                return match.group(0) if match else ""

            def _get_jump_to_main_agent_for_condition(condition_name: str) -> str:
                page_prefix = _extract_page_prefix(condition_name)
                if page_prefix:
                    for name in jump_to_main_agent_nodes:
                        if page_prefix in name:
                            return name
                # 如果没有匹配的节点，创建一个全局的 Jump to Main Agent
                global_name = "jump_to_main_agent_global"
                if global_name not in jump_to_main_agent_nodes:
                    jump_node = self._create_jump_to_main_agent_node(global_name)
                    all_nodes.append(jump_node)
                    jump_to_main_agent_nodes[global_name] = jump_node
                    # 如果提供了 nodes_file，同步保存
                    if nodes_file:
                        import os
                        if os.path.exists(nodes_file):
                            try:
                                with open(nodes_file, 'r', encoding='utf-8') as f:
                                    nodes_config = json.load(f)
                                existing_nodes_in_file = nodes_config.get("nodes", [])
                                existing_nodes_in_file.append(jump_node)
                                nodes_config["nodes"] = existing_nodes_in_file
                                with open(nodes_file, 'w', encoding='utf-8') as f:
                                    json.dump(nodes_config, f, ensure_ascii=False, indent=2)
                            except Exception as e:
                                print(f'    ⚠️  Warning: Failed to save global jump node in {nodes_file}: {e}')
                return global_name

            # 记录已有出边，避免重复添加
            existing_edge_keys = {
                (e.get("source_node"), e.get("target_node"), e.get("condition_id"))
                for e in edges
            }

            for cond_node in condition_nodes:
                cond_name = cond_node.get("name")
                if_else_conditions = cond_node.get("if_else_conditions", [])
                for branch in if_else_conditions:
                    if branch.get("logical_operator") != "other":
                        continue
                    other_condition_id = branch.get("condition_id")
                    if not other_condition_id:
                        continue
                    # 检查 other 分支是否已有出边
                    has_out_edge = any(
                        e.get("source_node") == cond_name
                        and e.get("condition_id") == other_condition_id
                        for e in edges
                    )
                    if has_out_edge:
                        continue
                    # 添加兜底边
                    jump_target = _get_jump_to_main_agent_for_condition(cond_name)
                    edge_key = (cond_name, jump_target, other_condition_id)
                    if edge_key in existing_edge_keys:
                        continue
                    edges.append({
                        "source_node": cond_name,
                        "target_node": jump_target,
                        "connection_type": "condition",
                        "condition_id": other_condition_id
                    })
                    existing_edge_keys.add(edge_key)
                    print(f'    ✅ Added fallback edge: {cond_name} → {jump_target} (Other)')
        
        # write by senlin.deng 2026-01-18
        # 兜底的删除空条件 condition 节点，并重连上下游
        # 后处理：过滤掉所有无效的边（无论是否有新边添加，都要过滤）
        # 后处理：删除空条件 condition 节点，并重连上下游
        all_nodes, edges = remove_empty_condition_nodes(all_nodes, edges, verbose=True)

        filtered_edges = filter_invalid_edges(edges, all_nodes)
        edges_config["edges"] = filtered_edges
        
        # 保存更新后的文件
        with open(edges_file, 'w', encoding='utf-8') as f:
            json.dump(edges_config, f, ensure_ascii=False, indent=2)
        
        if new_edges:
            print(f'  ✅ Added {len(new_edges)} edges to {edges_file}')
        else:
            print(f'  ⏭️  No new edges to add, but filtered invalid edges')
    
    def _find_page_last_node(
        self,
        page_prefix: str,
        all_nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]]
    ) -> str:
        """
        找到 page 的最后一个节点
        
        Args:
            page_prefix: page ID 的前8位
            all_nodes: 所有节点列表
            edges: 所有边列表
            
        Returns:
            page 的最后一个节点名称，如果找不到则返回 None
        """
        # 找到所有包含 page_prefix 的节点
        page_nodes = [
            node.get("name") 
            for node in all_nodes 
            if page_prefix in node.get("name", "")
        ]
        
        if not page_nodes:
            print(f'      [DEBUG] No nodes found with page_prefix {page_prefix}')
            # 尝试查找指向 page_xxx 的边，找到这些边的 source_node
            page_target = f"page_{page_prefix}"
            for edge in edges:
                if edge.get("target_node") == page_target:
                    source = edge.get("source_node")
                    if source:
                        print(f'      [DEBUG] Found page entry via edge: {source} → {page_target}')
                        # 返回指向 page 的节点，作为 page 的最后一个节点
                        return source
            return None
        
        # 找到所有作为 source_node 的节点
        source_nodes = {e.get("source_node") for e in edges if e.get("source_node")}
        
        # 找到没有出边的节点（不在 source_nodes 中）
        nodes_without_outgoing = set(page_nodes) - source_nodes
        
        if nodes_without_outgoing:
            # 选择第一个
            last_node = next(iter(nodes_without_outgoing))
            print(f'      [DEBUG] Found last node (no outgoing): {last_node} (from {len(page_nodes)} page nodes)')
            return last_node
        
        # 如果找不到，返回最后一个 page 节点
        if page_nodes:
            last_node = page_nodes[-1]
            print(f'      [DEBUG] Using last page node: {last_node} (from {len(page_nodes)} page nodes)')
            return last_node
        
        return None
    
    def _generate_single_default_workflow(
        self,
        pages: List[Dict[str, Any]],
        lang: str,
        output_dir: str
    ) -> List[str]:
        """
        当没有 flow-level 意图时，生成一个默认的 workflow（兼容旧逻辑）
        """
        print('\n⚠️  Generating single default workflow (no flow-level intents)')
        
        all_nodes = []
        all_edges = []
        
        # 添加 start 节点
        start_node = {"type": "start", "name": "start_node"}
        all_nodes.append(start_node)
        
        # 处理所有 pages
        page_id_to_entry = {}
        for page in pages:
            nodes, edges, entry_node_name = self.generate_workflow_from_page(page, lang)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
            
            # 支持两种格式：'key' (转换后的格式) 和 'pageId' (原始格式)
            page_id = page.get('key') or page.get('pageId', '')
            if page_id and entry_node_name:
                page_id_to_entry[page_id[:8]] = entry_node_name
        
        # 解析 page 引用
        fixed_edges = []
        for edge in all_edges:
            target = edge.get('target_node', '')
            if target.startswith('page_'):
                page_prefix = target.replace('page_', '')
                if page_prefix in page_id_to_entry:
                    edge['target_node'] = page_id_to_entry[page_prefix]
            fixed_edges.append(edge)
        
        all_edges = fixed_edges
        
        # 保存
        import os
        output_nodes_file = os.path.join(output_dir, 'nodes_config_default.json')
        output_edges_file = os.path.join(output_dir, 'edge_config_default.json')
        
        nodes_config = {"nodes": all_nodes}
        
        # 后处理：过滤掉所有无效的边
        filtered_edges = filter_invalid_edges(all_edges, all_nodes)
        edges_config = {"edges": filtered_edges}
        
        with open(output_nodes_file, 'w', encoding='utf-8') as f:
            json.dump(nodes_config, f, ensure_ascii=False, indent=2)
        
        with open(output_edges_file, 'w', encoding='utf-8') as f:
            json.dump(edges_config, f, ensure_ascii=False, indent=2)
        
        
        return ['default']
    


def load_intents_mapping(intents_file: str = 'intents_en.json') -> Dict[str, str]:
    """从intents文件中加载意图ID到名称的映射"""
    try:
        with open(intents_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        intents = data.get('intents', [])
        mapping = {}
        
        for intent in intents:
            intent_id = intent.get('id')
            display_name = intent.get('displayName')
            if intent_id and display_name:
                mapping[intent_id] = display_name
        
        print(f'Loaded {len(mapping)} intent mappings')
        return mapping
    
    except FileNotFoundError:
        print(f'Warning: Intent file {intents_file} not found')
        return {}


def load_intents_with_training_phrases(intents_file: str = 'intents_en.json') -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """
    从intents文件中加载意图ID到名称的映射，以及意图名称到训练短语的映射
    
    Args:
        intents_file: intents文件路径
        
    Returns:
        (intents_mapping, intents_training_phrases)
        - intents_mapping: intent_id -> display_name 的映射
        - intents_training_phrases: display_name -> [training_phrases] 的映射
    """
    try:
        with open(intents_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        intents = data.get('intents', [])
        mapping = {}
        training_phrases_mapping = {}
        
        for intent in intents:
            intent_id = intent.get('id')
            display_name = intent.get('displayName')
            training_phrases = intent.get('trainingPhrases', [])
            
            if intent_id and display_name:
                mapping[intent_id] = display_name
                # 使用 display_name 作为 key 存储训练短语
                training_phrases_mapping[display_name] = training_phrases
                # 同时也用 intent_id 作为 key 存储（备用）
                training_phrases_mapping[intent_id] = training_phrases
        
        print(f'Loaded {len(mapping)} intent mappings with training phrases')
        return mapping, training_phrases_mapping
    
    except FileNotFoundError:
        print(f'Warning: Intent file {intents_file} not found')
        return {}, {}


def main():
    """Main function - 生成多个独立的 workflows"""
    # 1. Load intent mappings
    intents_mapping = load_intents_mapping('intents_en.json')
    
    # 2. Create converter
    converter = WorkflowConverter(intents_mapping=intents_mapping, language='en')
    
    # 3. Convert to multiple workflows (新方法)
    generated_workflows = converter.convert_to_multiple_workflows(
        fulfillments_file='fulfillments.json',
        flow_file='exported_flow_TXNAndSTMT_Deeplink.json',
        lang='en',
        output_dir='.'
    )
    
    # 4. 保存 workflow 列表，供 step3 和 step5 使用
    import os
    workflow_list_file = 'generated_workflows.json'
    with open(workflow_list_file, 'w', encoding='utf-8') as f:
        json.dump({
            "workflows": generated_workflows,
            "count": len(generated_workflows)
        }, f, ensure_ascii=False, indent=2)
    
    print(f'\n✅ Workflow list saved to: {workflow_list_file}')
    print(f'   This file will be used by step3 and step5')


if __name__ == '__main__':
    main()

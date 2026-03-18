"""
NER 节点生成器模块 (NER Node Generator)
=========================================
功能：
1. 提供实体抽取（NER）节点的生成逻辑
2. 支持两种版本：
   - LLM 版本：使用 LLM + Code 节点进行实体抽取
   - Semantic 版本：使用 SemanticJudgment + Code 节点进行实体抽取

使用策略模式实现低耦合设计，通过工厂函数根据 ner_version 参数创建对应的生成器。

Created by: senlin.deng
Date: 2026-02-03
Updated: 2026-02-03 - 整合所有 LLM+Code 参数提取逻辑到 LLMNERNodeGenerator
"""

import uuid
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Tuple, Callable, Optional, Set

from logger_config import get_logger

logger = get_logger(__name__)


class NERNodeGeneratorBase(ABC):
    """NER 节点生成器基类"""
    
    def __init__(
        self,
        global_config: Dict[str, Any] = None,
        entities_with_synonyms: Dict[str, Dict[str, List[Dict]]] = None,
        entity_candidates: Dict[str, Dict[str, List[str]]] = None
    ):
        """
        初始化 NER 节点生成器
        
        Args:
            global_config: 全局配置
            entities_with_synonyms: 实体的同义词数据 {displayName -> {lang -> [{value, synonyms}]}}
            entity_candidates: 实体候选值 {entity_type -> {lang -> [values]}}
        """
        self.global_config = global_config or {}
        self.entities_with_synonyms = entities_with_synonyms or {}
        self.entity_candidates = entity_candidates or {}
    
    @abstractmethod
    def generate_parameter_nodes(
        self,
        page_id: str,
        intent_name: str,
        condition_id: str,
        trans_info_list: List[Dict[str, Any]],
        parameters: List[Dict[str, Any]],
        capture_variable: str,
        gen_unique_node_name: Callable[[str, str], str],
        gen_variable_name: Callable[[], str],
        lang: str = 'en',
        node_counter_ref: List[int] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        生成参数提取节点链
        
        Args:
            page_id: 页面ID
            intent_name: 意图名称
            condition_id: 语义判断节点中该意图的条件ID
            trans_info_list: 该意图的所有 transition 信息列表
            parameters: 参数列表
            capture_variable: 用户输入变量名
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            lang: 语言代码
            node_counter_ref: 节点计数器引用
            
        Returns:
            (节点列表, 条件分支列表)
        """
        pass


class LLMNERNodeGenerator(NERNodeGeneratorBase):
    """
    LLM 版本 NER 节点生成器
    
    使用 LLM + Code 节点进行实体抽取：
    [Capture] → [LLM Extract] → [Code Parse] → [Condition]
    
    支持的场景：
    1. 单意图参数提取 (generate_parameter_nodes)
    2. 多条件分支参数提取 (generate_parameter_nodes_v2)
    3. KB 模式下的参数提取 (generate_kb_parameter_nodes)
    4. 链式意图检查的参数提取 (generate_chain_parameter_nodes)
    5. Flow 层级的抽槽 (generate_flow_start_nodes)
    """
    
    def generate_parameter_nodes(
        self,
        page_id: str,
        intent_name: str,
        condition_id: str,
        trans_info_list: List[Dict[str, Any]],
        parameters: List[Dict[str, Any]],
        capture_variable: str,
        gen_unique_node_name: Callable[[str, str], str],
        gen_variable_name: Callable[[], str],
        lang: str = 'en',
        node_counter_ref: List[int] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        生成 LLM + Code 参数提取节点链（主入口方法，兼容 v2 版本）
        
        这是主要的参数提取入口，支持多个条件分支（trans_info_list）。
        
        Args:
            page_id: 页面ID
            intent_name: 意图名称
            condition_id: 语义判断节点中该意图的条件ID
            trans_info_list: 该意图的所有 transition 信息列表
            parameters: 参数列表
            capture_variable: 用户输入变量名
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            lang: 语言代码
            node_counter_ref: 节点计数器引用
            
        Returns:
            (节点列表, 条件分支列表)
        """
        # 导入混合条件生成函数（延迟导入避免循环依赖）
        from step2.page_processor import (
            generate_mixed_condition_code_node,
            generate_combined_mixed_condition_code_node
        )
        
        nodes = []
        condition_branches = []
        
        safe_intent_name = intent_name.replace(' ', '_').replace('-', '_').replace('.', '_')
        
        # 1. 收集所有需要提取的变量（从 parameters 和 conditions 中）
        variables_to_extract = self._collect_variables_to_extract(parameters, trans_info_list)
        
        # 如果没有需要提取的变量，直接返回
        if not variables_to_extract:
            first_trans_info = trans_info_list[0] if trans_info_list else {}
            branch = {
                "condition_id": condition_id,
                "condition_name": f"Intent_{intent_name}",
                "target_page_id": first_trans_info.get('target_page_id'),
                "target_flow_id": first_trans_info.get('target_flow_id'),
                "is_always_true": first_trans_info.get('is_always_true', False),
                "set_parameter_actions": first_trans_info.get('set_parameter_actions', []),
                "from_semantic_node": True
            }
            condition_branches.append(branch)
            return nodes, condition_branches
        
        # 2. 生成 LLM 节点
        llm_output_variable = gen_variable_name()
        llm_node_name = gen_unique_node_name(f'llm_extract_{safe_intent_name}', page_id)
        
        # 构建 hint 文本
        hint_text = self._build_hint_text(
            parameters=parameters,
            trans_info_list=trans_info_list,
            lang=lang
        )
        
        llm_node = self._build_llm_node(
            node_name=llm_node_name,
            variables_to_extract=variables_to_extract,
            llm_output_variable=llm_output_variable,
            capture_variable=capture_variable,
            hint_text=hint_text,
            intent_name=intent_name,
            condition_id=condition_id,
            title=f"Extract Parameters ({intent_name})"
        )
        nodes.append(llm_node)
        
        # 3. 生成 CODE 节点
        code_node_name = gen_unique_node_name(f'parse_params_{safe_intent_name}', page_id)
        
        code_node = self._build_code_node(
            node_name=code_node_name,
            variables_to_extract=variables_to_extract,
            llm_output_variable=llm_output_variable,
            intent_name=intent_name,
            condition_id=condition_id,
            title=f"Parse Parameters ({intent_name})"
        )
        nodes.append(code_node)
        
        # 4. 生成条件判断节点（根据参数值判断跳转）
        condition_node_name = gen_unique_node_name(f'param_condition_{safe_intent_name}', page_id)
        param_if_else = []
        
        # 用于存储混合条件生成的 code 节点
        mixed_condition_code_nodes = {}
        
        for idx, trans_info in enumerate(trans_info_list, 1):
            conditions_list = []
            condition_id_temp = f"param_{condition_id}_{idx}"
            
            # 处理混合 AND+OR 条件（最高优先级）
            if trans_info.get('is_mixed_and_or') and trans_info.get('mixed_and_or_condition'):
                mixed_code_node, output_var = generate_mixed_condition_code_node(
                    trans_info['mixed_and_or_condition'],
                    page_id,
                    gen_unique_node_name
                )
                nodes.append(mixed_code_node)
                mixed_condition_code_nodes[condition_id_temp] = mixed_code_node['name']
                
                conditions_list.append({
                    "condition_type": "variable",
                    "comparison_operator": "=",
                    "condition_value": "True",
                    "condition_variable": output_var
                })
                logical_operator = "and"
            # 处理 AND/OR 条件
            elif trans_info.get('and_conditions_list'):
                and_conditions = trans_info.get('and_conditions_list', [])
                is_or = trans_info.get('is_or_condition', False)
                for cond in and_conditions:
                    conditions_list.append({
                        "condition_type": "variable",
                        "comparison_operator": cond.get('operator', '='),
                        "condition_value": str(cond.get('value', '')) if cond.get('value') is not None else "",
                        "condition_variable": (cond.get('variable', '') or '').replace('-', '_').lower()
                    })
                logical_operator = "or" if is_or else "and"
            elif trans_info.get('has_condition') and trans_info.get('condition_variable'):
                cond_var = (trans_info.get('condition_variable', '') or '').replace('-', '_').lower()
                conditions_list.append({
                    "condition_type": "variable",
                    "comparison_operator": trans_info.get('condition_operator', '='),
                    "condition_value": str(trans_info.get('condition_value', '')),
                    "condition_variable": cond_var
                })
                logical_operator = "and"
            else:
                logical_operator = "other"
            
            # 生成条件名称
            cond_val = trans_info.get('condition_value', '')
            cond_val_short = str(cond_val)[:20].replace(' ', '_').replace('"', '').replace("'", '') if cond_val else ""
            condition_name = f"Route_{intent_name}_{cond_val_short}" if cond_val_short else f"Route_{intent_name}_{idx}"
            
            branch = {
                "condition_id": f"param_{condition_id}_{idx}",
                "condition_name": condition_name,
                "logical_operator": logical_operator if conditions_list else "other",
                "conditions": conditions_list,
                "condition_action": [],
                "target_page_id": trans_info.get('target_page_id'),
                "target_flow_id": trans_info.get('target_flow_id'),
                "is_always_true": trans_info.get('is_always_true', False),
                "set_parameter_actions": trans_info.get('set_parameter_actions', [])
            }
            param_if_else.append(branch)
            condition_branches.append(branch)
        
        # 添加 fallback 分支
        fallback_branch = {
            "condition_id": f"param_fallback_{condition_id}",
            "condition_name": f"Fallback_{intent_name}",
            "logical_operator": "other",
            "conditions": [],
            "condition_action": [],
            "target_fallback_text": True
        }
        param_if_else.append(fallback_branch)
        condition_branches.append(fallback_branch)
        
        condition_node = {
            "type": "condition",
            "name": condition_node_name,
            "title": f"Parameter Routing ({intent_name})",
            "if_else_conditions": param_if_else,
            "intent_name": intent_name,
            "from_semantic_condition_id": condition_id
        }
        nodes.append(condition_node)
        
        return nodes, condition_branches
    
    def generate_parameter_nodes_simple(
        self,
        page_id: str,
        intent_name: str,
        condition_id: str,
        trans_info: Dict[str, Any],
        parameters: List[Dict[str, Any]],
        capture_variable: str,
        gen_unique_node_name: Callable[[str, str], str],
        gen_variable_name: Callable[[], str],
        lang: str = 'en',
        node_counter_ref: List[int] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        为单个意图生成参数提取节点链（简化版本，单个 trans_info）
        
        Args:
            page_id: 页面ID
            intent_name: 意图名称
            condition_id: 语义判断节点中该意图的条件ID
            trans_info: 单个 transition 信息
            parameters: 参数列表
            capture_variable: 用户输入变量名
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            lang: 语言代码
            node_counter_ref: 节点计数器引用
            
        Returns:
            (节点列表, 条件分支列表)
        """
        nodes = []
        condition_branches = []
        
        safe_intent_name = intent_name.replace(' ', '_').replace('-', '_').replace('.', '_')
        
        # 1. 生成 LLM 节点
        llm_output_variable = gen_variable_name()
        llm_node_name = gen_unique_node_name(f'llm_extract_{safe_intent_name}', page_id)
        
        # 构建参数名列表
        param_names = [p.get('name', '') or p.get('id', '') for p in parameters]
        variables_to_extract = set([pn.replace('-', '_') for pn in param_names if pn])
        
        # 构建 hint 文本
        hint_text = self._build_hint_text_from_parameters(parameters, lang)
        
        llm_node = self._build_llm_node(
            node_name=llm_node_name,
            variables_to_extract=variables_to_extract,
            llm_output_variable=llm_output_variable,
            capture_variable=capture_variable,
            hint_text=hint_text,
            intent_name=intent_name,
            condition_id=condition_id,
            title=f"Extract Parameters ({intent_name})"
        )
        nodes.append(llm_node)
        
        # 2. 生成 CODE 节点
        code_node_name = gen_unique_node_name(f'parse_params_{safe_intent_name}', page_id)
        
        # 构建解析代码（简化版本）
        code_lines = ["import json"]
        code_lines.append(f"def main({llm_output_variable}) -> dict:")
        code_lines.append("    try:")
        code_lines.append(f"        params = json.loads({llm_output_variable})")
        code_lines.append("    except:")
        code_lines.append("        params = {}")
        code_lines.append("    return {")
        for param in parameters:
            param_name = param.get('name', '') or param.get('id', '')
            code_lines.append(f'        "{param_name}": params.get("{param_name}"),')
        code_lines.append("    }")
        
        code_node = {
            "type": "code",
            "name": code_node_name,
            "title": f"Parse Parameters ({intent_name})",
            "code": "\n".join(code_lines),
            "outputs": list(param_names),
            "args": [llm_output_variable],
            "intent_name": intent_name,
            "from_semantic_condition_id": condition_id
        }
        nodes.append(code_node)
        
        # 3. 生成条件判断节点（根据参数值判断跳转）
        if trans_info.get('has_condition'):
            condition_node_name = gen_unique_node_name(f'param_condition_{safe_intent_name}', page_id)
            
            param_branches = []
            
            branch = {
                "condition_id": f"param_{condition_id}",
                "condition_name": f"Param_Check_{intent_name}",
                "logical_operator": "and",
                "conditions": [{
                    "condition_type": "variable",
                    "comparison_operator": trans_info.get('condition_operator', '='),
                    "condition_value": str(trans_info.get('condition_value', '')),
                    "condition_variable": (trans_info.get('condition_variable', '') or '').replace('-', '_').lower()
                }],
                "condition_action": [],
                "target_page_id": trans_info.get('target_page_id'),
                "target_flow_id": trans_info.get('target_flow_id'),
                "is_always_true": trans_info.get('is_always_true', False),
                "set_parameter_actions": trans_info.get('set_parameter_actions', [])
            }
            param_branches.append(branch)
            condition_branches.append(branch)
            
            else_branch = {
                "condition_id": f"else_{condition_id}",
                "condition_name": f"Else_{intent_name}",
                "logical_operator": "other",
                "conditions": [],
                "condition_action": []
            }
            param_branches.append(else_branch)
            
            condition_node = {
                "type": "condition",
                "name": condition_node_name,
                "title": f"Parameter Routing ({intent_name})",
                "if_else_conditions": param_branches,
                "intent_name": intent_name,
                "from_semantic_condition_id": condition_id
            }
            nodes.append(condition_node)
        else:
            branch = {
                "condition_id": condition_id,
                "condition_name": f"Intent_{intent_name}",
                "target_page_id": trans_info.get('target_page_id'),
                "target_flow_id": trans_info.get('target_flow_id'),
                "is_always_true": trans_info.get('is_always_true', False),
                "set_parameter_actions": trans_info.get('set_parameter_actions', []),
                "has_parameter_extraction": True,
                "param_extraction_entry": llm_node_name
            }
            condition_branches.append(branch)
        
        return nodes, condition_branches
    
    def generate_kb_parameter_nodes(
        self,
        page_id: str,
        capture_variable: str,
        condition_routes: List[Dict[str, Any]],
        intent_parameters_map: Dict[str, List[Dict[str, Any]]],
        gen_unique_node_name: Callable[[str, str], str],
        gen_variable_name: Callable[[], str],
        lang: str = 'en'
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        为 KB 模式生成参数提取节点（Pattern 4）
        
        流程: capture → kb → extract_intent → check_intent → llm → code → param_condition
        
        Args:
            page_id: 页面ID
            capture_variable: 用户输入变量名
            condition_routes: 条件路由列表
            intent_parameters_map: 意图参数映射
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            lang: 语言代码
            
        Returns:
            (节点列表, 条件分支列表)
        """
        from step2.page_processor import (
            generate_mixed_condition_code_node,
            generate_combined_mixed_condition_code_node
        )
        
        nodes = []
        condition_branches = []
        
        # 1. 收集需要提取的变量
        variables_to_extract = set()
        for cond_info in condition_routes:
            cond_var = cond_info.get('condition_variable')
            if cond_var:
                variables_to_extract.add(cond_var.replace('-', '_'))
        
        if not variables_to_extract:
            logger.warning(f"KB Parameter Nodes: No variables to extract")
            variables_to_extract = set(['fallback_param'])
        
        # 2. 生成 LLM 节点
        llm_variable = gen_variable_name()
        llm_node_name = gen_unique_node_name('llm_extract_param', page_id)
        
        # 构建 hint 文本
        hint_text = self._build_hint_text_from_condition_routes(
            condition_routes=condition_routes,
            intent_parameters_map=intent_parameters_map,
            lang=lang
        )
        
        llm_node = self._build_llm_node(
            node_name=llm_node_name,
            variables_to_extract=variables_to_extract,
            llm_output_variable=llm_variable,
            capture_variable=capture_variable,
            hint_text=hint_text,
            intent_name="",
            condition_id="",
            title="Extract Parameters from User Input"
        )
        nodes.append(llm_node)
        
        # 3. 生成 CODE 节点
        code_node_name = gen_unique_node_name('parse_params', page_id)
        
        code_node = self._build_code_node(
            node_name=code_node_name,
            variables_to_extract=variables_to_extract,
            llm_output_variable=llm_variable,
            intent_name="",
            condition_id="",
            title="Parse Parameters from LLM"
        )
        nodes.append(code_node)
        
        # 4. 生成参数判断 condition 节点
        param_condition_node_name = gen_unique_node_name('param_condition', page_id)
        param_branches = []
        
        for idx, cond_info in enumerate(condition_routes, 1):
            condition_id = f"param_{idx}"
            mixed_code_node_name = None
            
            # 处理混合 AND+OR 条件
            if cond_info.get('is_mixed_and_or') and cond_info.get('mixed_and_or_condition'):
                mixed_code_node, output_var = generate_mixed_condition_code_node(
                    cond_info['mixed_and_or_condition'],
                    page_id,
                    gen_unique_node_name
                )
                nodes.append(mixed_code_node)
                mixed_code_node_name = mixed_code_node['name']
                
                conditions = [{
                    "condition_type": "variable",
                    "comparison_operator": "=",
                    "condition_value": "1",
                    "condition_variable": output_var
                }]
                logical_operator = "and"
            elif cond_info.get('and_conditions_list'):
                and_conditions = cond_info.get('and_conditions_list', [])
                is_or = cond_info.get('is_or_condition', False)
                conditions = []
                for cond in and_conditions:
                    conditions.append({
                        "condition_type": "variable",
                        "comparison_operator": cond.get('operator', '='),
                        "condition_value": str(cond.get('value', '')) if cond.get('value') is not None else "",
                        "condition_variable": (cond.get('variable', '') or '').replace('-', '_').lower()
                    })
                logical_operator = "or" if is_or else "and"
            elif cond_info.get('condition_variable'):
                cond_var = (cond_info.get('condition_variable', '') or '').replace('-', '_').lower()
                conditions = [{
                    "condition_type": "variable",
                    "comparison_operator": cond_info.get('condition_operator', '='),
                    "condition_value": str(cond_info.get('condition_value', '')),
                    "condition_variable": cond_var
                }]
                logical_operator = "and"
            else:
                conditions = []
                logical_operator = "other"
            
            cond_val = cond_info.get('condition_value', '')
            cond_val_short = str(cond_val)[:15].replace(' ', '_').replace('"', '').replace("'", '') if cond_val else ""
            condition_name = f"Param_{cond_val_short}" if cond_val_short else f"Param_{idx}"
            
            branch = {
                "condition_id": condition_id,
                "condition_name": condition_name,
                "logical_operator": logical_operator if conditions else "other",
                "conditions": conditions,
                "condition_action": [],
                "target_page_id": cond_info.get('target_page_id'),
                "target_flow_id": cond_info.get('target_flow_id'),
                "is_always_true": cond_info.get('is_always_true', False),
                "set_parameter_actions": cond_info.get('set_parameter_actions', []),
                "_mixed_code_node": mixed_code_node_name
            }
            param_branches.append(branch)
            condition_branches.append(branch)
        
        # 添加 fallback 分支
        fallback_branch = {
            "condition_id": "param_fallback",
            "condition_name": "Fallback",
            "logical_operator": "other",
            "conditions": [],
            "condition_action": [],
            "target_fallback_text": True
        }
        param_branches.append(fallback_branch)
        condition_branches.append(fallback_branch)
        
        condition_node = {
            "type": "condition",
            "name": param_condition_node_name,
            "title": "Parameter Routing",
            "if_else_conditions": param_branches
        }
        nodes.append(condition_node)
        
        return nodes, condition_branches
    
    def generate_chain_parameter_nodes(
        self,
        page_id: str,
        intent_name: str,
        variables_to_extract: Set[str],
        parameters: List[Dict[str, Any]],
        trans_info_list: List[Dict[str, Any]],
        gen_unique_node_name: Callable[[str, str], str],
        gen_variable_name: Callable[[], str],
        lang: str = 'en'
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        为链式意图检查生成参数提取节点（Pattern 2）
        
        流程: intent_check → LLM → CODE → param_condition
        
        Args:
            page_id: 页面ID
            intent_name: 意图名称
            variables_to_extract: 需要提取的变量集合
            parameters: 参数列表
            trans_info_list: transition 信息列表
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            lang: 语言代码
            
        Returns:
            (llm_node, code_node, extra_nodes, condition_branches)
        """
        from step2.page_processor import (
            generate_mixed_condition_code_node,
            generate_combined_mixed_condition_code_node
        )
        
        extra_nodes = []
        condition_branches = []
        
        # 1. 生成 LLM 节点
        llm_variable = gen_variable_name()
        llm_node_name = gen_unique_node_name(f'llm_extract_{intent_name}', page_id)
        
        # 构建 hint 文本
        hint_text = self._build_hint_text_for_chain(
            variables_to_extract=variables_to_extract,
            parameters=parameters,
            trans_info_list=trans_info_list,
            lang=lang
        )
        
        # 构建输出模板
        output_template = "{\n"
        for var_name in sorted(variables_to_extract):
            output_template += f'  "{var_name}": "",\n'
        output_template += "}"
        
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
        
        # 2. 生成 CODE 节点
        code_variable = gen_variable_name()
        code_node_name = gen_unique_node_name(f'parse_{intent_name}', page_id)
        
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
        
        code_node = {
            "type": "code",
            "name": code_node_name,
            "title": f"Parse Parameters for {intent_name}",
            "variable_assign": code_variable,
            "code": parse_code,
            "outputs": sorted_vars,
            "args": [llm_variable]
        }
        
        # 3. 生成条件分支（param_route）
        param_condition_name = gen_unique_node_name(f'param_route_{intent_name}', page_id)
        param_if_else = []
        
        # 处理混合条件
        mixed_conditions_with_index = []
        temp_branch_index = 0
        for t_info in trans_info_list:
            if t_info.get('intent_name') != intent_name:
                continue
            if not t_info.get('has_condition'):
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
            extra_nodes.append(combined_mixed_code_node)
        
        branch_index = 0
        for t_info in trans_info_list:
            if t_info.get('intent_name') != intent_name:
                continue
            if not t_info.get('has_condition'):
                continue
            
            branch_index += 1
            
            if t_info.get('is_mixed_and_or') and t_info.get('mixed_and_or_condition') and combined_output_var:
                expected_value = index_to_condition_value.get(branch_index, str(branch_index))
                conditions = [{
                    "condition_type": "variable",
                    "comparison_operator": "=",
                    "condition_value": expected_value,
                    "condition_variable": combined_output_var
                }]
                logical_operator = "and"
            elif t_info.get('and_conditions_list'):
                and_conditions = t_info.get('and_conditions_list', [])
                is_or = t_info.get('is_or_condition', False)
                conditions = []
                for cond in and_conditions:
                    conditions.append({
                        "condition_type": "variable",
                        "comparison_operator": cond.get('operator', '='),
                        "condition_value": str(cond.get('value', '')) if cond.get('value') is not None else "",
                        "condition_variable": (cond.get('variable', '') or '').replace('-', '_').lower()
                    })
                logical_operator = "or" if is_or else "and"
            elif t_info.get('condition_variable'):
                cond_var = (t_info.get('condition_variable', '') or '').replace('-', '_').lower()
                conditions = [{
                    "condition_type": "variable",
                    "comparison_operator": t_info.get('condition_operator', '='),
                    "condition_value": str(t_info.get('condition_value', '')),
                    "condition_variable": cond_var
                }]
                logical_operator = "and"
            else:
                conditions = []
                logical_operator = "other"
            
            cond_val = t_info.get('condition_value', '')
            cond_val_short = str(cond_val)[:15].replace(' ', '_').replace('"', '').replace("'", '') if cond_val else ""
            condition_name = f"Route_{intent_name}_{cond_val_short}" if cond_val_short else f"Route_{intent_name}_{branch_index}"
            
            branch = {
                "condition_id": f"route_{intent_name}_{branch_index}",
                "condition_name": condition_name,
                "logical_operator": logical_operator if conditions else "other",
                "conditions": conditions,
                "condition_action": [],
                "target_page_id": t_info.get('target_page_id'),
                "target_flow_id": t_info.get('target_flow_id'),
                "is_always_true": t_info.get('is_always_true', False),
                "set_parameter_actions": t_info.get('set_parameter_actions', [])
            }
            param_if_else.append(branch)
            condition_branches.append(branch)
        
        # 添加 else 分支
        else_branch = {
            "condition_id": f"else_{intent_name}",
            "condition_name": f"Else_{intent_name}",
            "logical_operator": "other",
            "conditions": [],
            "condition_action": []
        }
        param_if_else.append(else_branch)
        
        param_condition_node = {
            "type": "condition",
            "name": param_condition_name,
            "title": f"Parameter Routing for {intent_name}",
            "if_else_conditions": param_if_else
        }
        extra_nodes.append(param_condition_node)
        
        return llm_node, code_node, extra_nodes, condition_branches
    
    def generate_flow_start_nodes(
        self,
        intent_name: str,
        parameters: List[Dict[str, Any]],
        gen_variable_name: Callable[[], str],
        lang: str = 'en'
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """
        为 Flow 层级生成抽槽节点链（Start Page 抽槽）
        
        流程: capture → llm → code
        
        Args:
            intent_name: 意图名称
            parameters: 参数列表
            gen_variable_name: 生成变量名的函数
            lang: 语言代码
            
        Returns:
            (capture_node, llm_node, code_node)
        """
        safe_intent_name = intent_name.replace(' ', '_').replace('-', '_').replace('.', '_')
        
        # 1. Capture 节点
        capture_node = {
            "type": "captureUserReply",
            "name": f"capture_flow_start_{safe_intent_name}",
            "title": "Capture User Input (Flow Start)",
            "variable_assign": "last_user_response"
        }
        
        # 2. 构建参数名列表
        param_names = [p.get('id', '') or p.get('name', '') for p in parameters]
        
        # 3. 构建 hint 文本
        hint_text = self._build_hint_text_from_parameters(parameters, lang)
        
        # 4. 构建输出模板
        output_template = "{\n"
        for param in param_names:
            param_normalized = param.replace('-', '_')
            output_template += f'  "{param_normalized}": "",\n'
        output_template += "}"
        
        # 5. LLM 节点
        llm_output_variable = gen_variable_name()
        
        flow_prompt = f'''#Role
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
            "name": f"llm_extract_params_{safe_intent_name}",
            "title": f"Extract Parameters from User Input (Flow Start)",
            "variable_assign": llm_output_variable,
            "prompt_template": flow_prompt,
            "llm_name": self.global_config.get("llmcodemodel", "azure-gpt-4o"),
            "chat_history_flag": self.global_config.get("enable_short_memory", False),
            "chat_history_count": self.global_config.get("short_chat_count", 5),
            "intent_name": intent_name
        }
        
        # 6. Code 节点
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
        
        code_node = {
            "type": "code",
            "name": f"parse_params_{safe_intent_name}",
            "title": f"Parse Parameters from LLM (Flow Start)",
            "code": parse_code,
            "outputs": sorted_vars,
            "args": [llm_output_variable]
        }
        
        return capture_node, llm_node, code_node
    
    def build_llm_and_code_nodes(
        self,
        variables_to_extract: Set[str],
        capture_variable: str,
        llm_node_name: str,
        code_node_name: str,
        gen_variable_name: Callable[[], str],
        hint_text: str = '',
        llm_title: str = "Extract Parameters from User Input",
        code_title: str = "Parse Parameters from LLM"
    ) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        """
        构建 LLM 和 CODE 节点（不包含 condition 节点）
        
        这是一个底层方法，供复杂场景使用（如 KB 模式），调用方自己负责 condition 节点生成。
        
        Args:
            variables_to_extract: 需要提取的变量集合
            capture_variable: 用户输入变量名
            llm_node_name: LLM 节点名称
            code_node_name: CODE 节点名称
            gen_variable_name: 生成变量名的函数
            hint_text: LLM 提示词中的候选值提示
            llm_title: LLM 节点标题
            code_title: CODE 节点标题
            
        Returns:
            (llm_node, code_node, llm_output_variable)
        """
        # 生成 LLM 节点
        llm_output_variable = gen_variable_name()
        
        llm_node = self._build_llm_node(
            node_name=llm_node_name,
            variables_to_extract=variables_to_extract,
            llm_output_variable=llm_output_variable,
            capture_variable=capture_variable,
            hint_text=hint_text,
            intent_name="",
            condition_id="",
            title=llm_title
        )
        
        code_node = self._build_code_node(
            node_name=code_node_name,
            variables_to_extract=variables_to_extract,
            llm_output_variable=llm_output_variable,
            intent_name="",
            condition_id="",
            title=code_title
        )
        
        return llm_node, code_node, llm_output_variable
    
    def build_hint_text_for_kb(
        self,
        condition_routes: List[Dict[str, Any]],
        intent_parameters_map: Dict[str, List[Dict[str, Any]]],
        lang: str
    ) -> str:
        """
        为 KB 模式构建 hint 文本
        
        Args:
            condition_routes: 条件路由列表
            intent_parameters_map: 意图参数映射
            lang: 语言代码
            
        Returns:
            hint 文本
        """
        return self._build_hint_text_from_condition_routes(
            condition_routes=condition_routes,
            intent_parameters_map=intent_parameters_map,
            lang=lang
        )
    
    # ==================== 私有辅助方法 ====================
    
    def _collect_variables_to_extract(
        self,
        parameters: List[Dict[str, Any]],
        trans_info_list: List[Dict[str, Any]]
    ) -> Set[str]:
        """从 parameters 和 trans_info_list 收集需要提取的变量"""
        variables_to_extract = set()
        
        # 1. 从 parameters 中收集
        if parameters:
            for param in parameters:
                param_name = param.get('name', '') or param.get('id', '')
                if param_name:
                    variables_to_extract.add(param_name.replace('-', '_'))
        
        # 2. 从 trans_info 的 condition_variable 中收集
        for trans_info in trans_info_list:
            cond_var = trans_info.get('condition_variable')
            if cond_var:
                variables_to_extract.add(cond_var.replace('-', '_'))
        
        return variables_to_extract
    
    def _build_hint_text(
        self,
        parameters: List[Dict[str, Any]],
        trans_info_list: List[Dict[str, Any]],
        lang: str
    ) -> str:
        """构建 LLM 提示词中的候选值提示（综合版本）"""
        hint_lines = []
        
        # 1. 从 parameters 获取候选值
        if parameters:
            for param in parameters:
                param_name = param.get('name', '') or param.get('id', '')
                param_name_normalized = param_name.replace('-', '_')
                entity_type = param.get('entityType', '') or param.get('entityTypeDisplayName', '')
                
                if entity_type:
                    hint_line = self._get_entity_hint_line(
                        param_name_normalized, entity_type, lang
                    )
                    if hint_line:
                        hint_lines.append(hint_line)
        
        # 2. 从 condition_values 收集候选值
        condition_values_by_var = {}
        for trans_info in trans_info_list:
            cond_var = trans_info.get('condition_variable')
            cond_var_normalized = cond_var.replace('-', '_') if cond_var else None
            if cond_var_normalized:
                if cond_var_normalized not in condition_values_by_var:
                    condition_values_by_var[cond_var_normalized] = []
                cond_val = trans_info.get('condition_value')
                if cond_val:
                    condition_values_by_var[cond_var_normalized].append(str(cond_val))
                for cv in trans_info.get('condition_values', []):
                    if cv:
                        condition_values_by_var[cond_var_normalized].append(str(cv))
        
        # 添加条件变量的候选值到 hint
        existing_vars = {line.split(':')[0].strip('- ') for line in hint_lines if ':' in line}
        for var_name, values in condition_values_by_var.items():
            if var_name not in existing_vars and values:
                unique_values = list(set(values))
                hint_line = f'- {var_name}: allowed values ({lang}) = ' + ", ".join(unique_values)
                
                # 尝试添加 synonyms
                hint_line = self._append_synonyms_to_hint(hint_line, var_name, unique_values, lang)
                hint_lines.append(hint_line)
        
        if hint_lines:
            return '\n##Hints (Use one of the allowed values for each parameter)\n' + "\n".join(hint_lines) + '\n'
        return ''
    
    def _build_hint_text_from_parameters(
        self,
        parameters: List[Dict[str, Any]],
        lang: str
    ) -> str:
        """从 parameters 构建 hint 文本（简化版本）"""
        hint_lines = []
        
        for param in parameters:
            param_name = param.get('name', '') or param.get('id', '')
            param_name_normalized = param_name.replace('-', '_')
            entity_type = param.get('entityType', '') or param.get('entityTypeDisplayName', '')
            
            if entity_type:
                hint_line = self._get_entity_hint_line(
                    param_name_normalized, entity_type, lang
                )
                if hint_line:
                    hint_lines.append(hint_line)
        
        if hint_lines:
            return '\n##Hints (Use one of the allowed values for each parameter)\n' + "\n".join(hint_lines) + '\n'
        return ''
    
    def _build_hint_text_from_condition_routes(
        self,
        condition_routes: List[Dict[str, Any]],
        intent_parameters_map: Dict[str, List[Dict[str, Any]]],
        lang: str
    ) -> str:
        """从 condition_routes 构建 hint 文本（KB 模式）"""
        hint_lines = []
        
        # 1. 收集需要验证的变量
        condition_values_by_var = {}
        for cond_info in condition_routes:
            cond_var = cond_info.get('condition_variable')
            cond_val = cond_info.get('condition_value')
            if cond_var and cond_val:
                cond_var_normalized = cond_var.replace('-', '_')
                if cond_var_normalized not in condition_values_by_var:
                    condition_values_by_var[cond_var_normalized] = []
                condition_values_by_var[cond_var_normalized].append(str(cond_val))
        
        # 2. 为每个变量构建提示词
        for var_name, values in condition_values_by_var.items():
            unique_values = list(set(values))
            
            # 尝试从 intent_parameters_map 找到对应的实体类型
            entity_type = None
            if intent_parameters_map:
                for intent_name, parameters in intent_parameters_map.items():
                    if parameters:
                        for param in parameters:
                            param_name = param.get('name', '') or param.get('id', '')
                            param_name_normalized = param_name.replace('-', '_')
                            if param_name_normalized == var_name:
                                entity_type = param.get('entityType', '') or param.get('entityTypeDisplayName', '')
                                break
                        if entity_type:
                            break
            
            # 如果找到了实体类型，从 entity_candidates 获取完整候选值
            if entity_type and self.entity_candidates:
                entity_key = f"@{entity_type}" if not entity_type.startswith('@') else entity_type
                candidates = self.entity_candidates.get(entity_key, {}).get(lang, [])
                if not candidates:
                    candidates = self.entity_candidates.get(entity_type, {}).get(lang, [])
                
                if candidates:
                    hint_line = f'- {var_name}: allowed values ({lang}) = ' + ", ".join(candidates)
                else:
                    hint_line = f'- {var_name}: allowed values ({lang}) = {", ".join(unique_values)}'
            else:
                hint_line = f'- {var_name}: allowed values ({lang}) = {", ".join(unique_values)}'
            
            # 尝试添加 synonyms
            entity_data = None
            if entity_type:
                entity_display_name = entity_type.lstrip('@')
                entity_data = self.entities_with_synonyms.get(entity_display_name, {}).get(lang, [])
            
            if not entity_data:
                var_name_original = var_name.replace('_', '-')
                for try_name in [var_name, var_name_original]:
                    if try_name in self.entities_with_synonyms:
                        entity_data = self.entities_with_synonyms[try_name].get(lang, [])
                        break
            
            if entity_data:
                synonym_lines = []
                target_values = candidates if (entity_type and self.entity_candidates) else unique_values
                for entry in entity_data:
                    value = entry.get('value', '')
                    if value in target_values and entry.get('synonyms', []):
                        synonyms_str = "、".join(entry['synonyms'])
                        synonym_lines.append(f'   "{value}"(synonyms: {synonyms_str})')
                
                if synonym_lines:
                    hint_line += '\n' + '\n'.join(synonym_lines)
            
            hint_lines.append(hint_line)
        
        if hint_lines:
            return '\n##Hints (Use one of the allowed values for each parameter)\n' + "\n".join(hint_lines) + '\n'
        return ''
    
    def _build_hint_text_for_chain(
        self,
        variables_to_extract: Set[str],
        parameters: List[Dict[str, Any]],
        trans_info_list: List[Dict[str, Any]],
        lang: str
    ) -> str:
        """为链式模式构建 hint 文本"""
        hint_lines = []
        
        # 1. 从 parameters 定义中获取候选值
        if parameters:
            for param in parameters:
                param_id = param.get('id', '')
                ent_type = param.get('entityTypeDisplayName')
                if ent_type:
                    param_id_normalized = param_id.replace('-', '_')
                    entity_key = f"@{ent_type}" if not ent_type.startswith('@') else ent_type
                    lang_vals = self.entity_candidates.get(entity_key, {}).get(lang, [])
                    
                    if lang_vals:
                        hint_line = f'- {param_id_normalized}: allowed values ({lang}) = ' + ", ".join(lang_vals)
                        
                        # 尝试添加 synonyms
                        entity_display_name = ent_type.lstrip('@')
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
        
        # 2. 从 condition 中收集候选值
        condition_values_by_var = {}
        for t_info in trans_info_list:
            condition_var = t_info.get('condition_variable')
            condition_var_normalized = condition_var.replace('-', '_') if condition_var else None
            
            if condition_var_normalized:
                if condition_var_normalized not in condition_values_by_var:
                    condition_values_by_var[condition_var_normalized] = []
                
                condition_val = t_info.get('condition_value')
                if condition_val:
                    condition_values_by_var[condition_var_normalized].append(str(condition_val))
                
                # 收集 OR 条件的多个值
                condition_vals = t_info.get('condition_values', [])
                for cv in condition_vals:
                    if cv:
                        condition_values_by_var[condition_var_normalized].append(str(cv))
        
        # 将 condition 的候选值添加到 hint
        existing_param_ids = {line.split(':')[0].strip('- ') for line in hint_lines if ':' in line}
        for var_name, values in condition_values_by_var.items():
            if var_name not in existing_param_ids and values:
                unique_values = list(set(values))
                hint_line = f'- {var_name}: allowed values ({lang}) = ' + ", ".join(unique_values)
                
                # 尝试添加 synonyms
                hint_line = self._append_synonyms_to_hint(hint_line, var_name, unique_values, lang)
                hint_lines.append(hint_line)
        
        if hint_lines:
            return '\n##Hints (Use one of the allowed values for each parameter)\n' + "\n".join(hint_lines) + '\n'
        return ''
    
    def _get_entity_hint_line(
        self,
        param_name_normalized: str,
        entity_type: str,
        lang: str
    ) -> Optional[str]:
        """获取单个实体的 hint 行"""
        entity_key = f"@{entity_type}" if not entity_type.startswith('@') else entity_type
        candidates = self.entity_candidates.get(entity_key, {}).get(lang, [])
        if not candidates:
            candidates = self.entity_candidates.get(entity_type, {}).get(lang, [])
        
        if not candidates:
            return None
        
        hint_line = f'- {param_name_normalized}: allowed values ({lang}) = ' + ", ".join(candidates)
        
        # 尝试添加 synonyms
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
        
        return hint_line
    
    def _append_synonyms_to_hint(
        self,
        hint_line: str,
        var_name: str,
        unique_values: List[str],
        lang: str
    ) -> str:
        """为 hint 行添加 synonyms"""
        entity_data = None
        var_name_original = var_name.replace('_', '-')
        
        for try_name in [var_name, var_name_original]:
            if try_name in self.entities_with_synonyms:
                entity_data = self.entities_with_synonyms[try_name].get(lang, [])
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
        
        return hint_line
    
    def _build_llm_node(
        self,
        node_name: str,
        variables_to_extract: Set[str],
        llm_output_variable: str,
        capture_variable: str,
        hint_text: str,
        intent_name: str,
        condition_id: str,
        title: str
    ) -> Dict[str, Any]:
        """构建 LLM 节点"""
        # 构建输出模板
        output_template = "{\n"
        for var_name in sorted(variables_to_extract):
            output_template += f'  "{var_name}": "",\n'
        output_template += "}"
        
        prompt_template = f'''#Role
You are an information extraction specialist. Your task is to extract parameters from the user's reply.

##User Input
{{{{{capture_variable}}}}}

##Output Template
{output_template}

##Instructions
Extract the required parameters from user input and return in JSON format. If a parameter is not found, use empty string.
{hint_text}'''
        
        llm_node = {
            "type": "llmVariableAssignment",
            "name": node_name,
            "title": title,
            "variable_assign": llm_output_variable,
            "prompt_template": prompt_template,
            "llm_name": self.global_config.get("llmcodemodel", "azure-gpt-4o"),
            "chat_history_flag": self.global_config.get("enable_short_memory", False),
            "chat_history_count": self.global_config.get("short_chat_count", 5),
        }
        
        if intent_name:
            llm_node["intent_name"] = intent_name
        if condition_id:
            llm_node["from_semantic_condition_id"] = condition_id
        
        return llm_node
    
    def _build_code_node(
        self,
        node_name: str,
        variables_to_extract: Set[str],
        llm_output_variable: str,
        intent_name: str,
        condition_id: str,
        title: str
    ) -> Dict[str, Any]:
        """构建 CODE 解析节点"""
        sorted_vars = sorted(variables_to_extract)
        return_dict = ",\n".join([f'        "{v}": data["{v}"] if "{v}" in data else ""' for v in sorted_vars])
        
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
        
        code_node = {
            "type": "code",
            "name": node_name,
            "title": title,
            "code": parse_code,
            "outputs": sorted_vars,
            "args": [llm_output_variable],
        }
        
        if intent_name:
            code_node["intent_name"] = intent_name
        if condition_id:
            code_node["from_semantic_condition_id"] = condition_id
        
        return code_node


class SemanticNERNodeGenerator(NERNodeGeneratorBase):
    """
    Semantic 版本 NER 节点生成器
    
    使用 SemanticJudgment + Code 节点进行实体抽取：
    [Capture] → [SemanticJudgment (每个实体值作为一个分支)] → [Code (设置参数值)]
                         ↓
                   ┌─────┴─────┬─────────┬─────────┐
                   ↓           ↓         ↓         ↓
                [Value1]   [Value2]  [Value3]  [Other]
                   ↓           ↓         ↓         ↓
                [Code]      [Code]    [Code]    [Code]
               设置=v1     设置=v2   设置=v3   设置=""
    """
    
    def generate_parameter_nodes(
        self,
        page_id: str,
        intent_name: str,
        condition_id: str,
        trans_info_list: List[Dict[str, Any]],
        parameters: List[Dict[str, Any]],
        capture_variable: str,
        gen_unique_node_name: Callable[[str, str], str],
        gen_variable_name: Callable[[], str],
        lang: str = 'en',
        node_counter_ref: List[int] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        生成 SemanticJudgment + Code 参数提取节点链
        
        Args:
            page_id: 页面ID
            intent_name: 意图名称（如 Common_YesNo）
            condition_id: 语义判断节点中该意图的条件ID
            trans_info_list: 该意图的所有 transition 信息列表
            parameters: 参数列表，包含 entityType 等信息
            capture_variable: 用户输入变量名（如 last_user_response）
            gen_unique_node_name: 生成唯一节点名的函数
            gen_variable_name: 生成变量名的函数
            lang: 语言代码
            node_counter_ref: 节点计数器引用
            
        Returns:
            (节点列表, 条件分支列表)
        """
        nodes = []
        condition_branches = []
        
        safe_intent_name = intent_name.replace(' ', '_').replace('-', '_').replace('.', '_')
        
        # 收集需要提取的参数信息
        if not parameters:
            logger.warning(f"SemanticNERNodeGenerator: No parameters for intent {intent_name}")
            return nodes, condition_branches
        
        # 为每个参数生成 Semantic NER 节点
        for param in parameters:
            param_name = param.get('name', '') or param.get('id', '')
            if not param_name:
                continue
            
            param_name_normalized = param_name.replace('-', '_')
            entity_type = param.get('entityType', '') or param.get('entityTypeDisplayName', '')
            
            # 获取实体的标准值和同义词
            entity_display_name = entity_type.lstrip('@') if entity_type else param_name
            entity_data = self.entities_with_synonyms.get(entity_display_name, {}).get(lang, [])
            
            if not entity_data:
                # 如果没有找到实体数据，尝试从 entity_candidates 获取
                entity_key = f"@{entity_type}" if entity_type and not entity_type.startswith('@') else entity_type
                candidates = self.entity_candidates.get(entity_key, {}).get(lang, [])
                if not candidates:
                    candidates = self.entity_candidates.get(entity_type or param_name, {}).get(lang, [])
                
                if candidates:
                    # 将 candidates 转换为 entity_data 格式
                    entity_data = [{"value": v, "synonyms": [v]} for v in candidates]
            
            # writed by senlin.deng 2026-02-05
            # 新增：如果仍然没有实体数据，尝试从 trans_info_list 的 condition_value 中提取候选值
            # 这样可以处理 Intent 路由与条件路由混合的情况
            if not entity_data and trans_info_list:
                condition_values = set()
                for trans_info in trans_info_list:
                    # 获取 condition_variable 并规范化
                    cond_var = trans_info.get('condition_variable', '')
                    cond_var_normalized = cond_var.lower().replace('-', '_') if cond_var else ''
                    
                    # 检查 condition_variable 是否与当前参数名匹配
                    if cond_var_normalized == param_name_normalized.lower():
                        # 收集单个 condition_value
                        cond_val = trans_info.get('condition_value')
                        if cond_val and cond_val not in ('true', 'false', True, False):
                            condition_values.add(str(cond_val))
                        
                        # 收集 OR 条件的多个值（condition_values 列表）
                        cond_vals = trans_info.get('condition_values', [])
                        for cv in cond_vals:
                            if cv and cv not in ('true', 'false', True, False):
                                condition_values.add(str(cv))
                
                if condition_values:
                    logger.debug(f"SemanticNERNodeGenerator: Found condition values for {param_name}: {condition_values}")
                    entity_data = [{"value": v, "synonyms": [v]} for v in sorted(condition_values)]
            
            # writed by senlin.deng 2026-02-05
            # 即使没有候选值，在 ner_version=semantic 的情况下也要创建 semantic+code 节点
            # 只是此时只有 Other 分支，参数值将通过 LLM 兜底或设为用户输入原始值
            if not entity_data:
                logger.warning(f"SemanticNERNodeGenerator: No entity data for {entity_display_name} in lang {lang}, will create semantic node with only 'Other' branch")
                # 传入空的 entity_data，_generate_semantic_ner_for_parameter 会生成只有 Other 分支的节点
                entity_data = []
            
            # 生成 Semantic NER 节点组
            param_nodes, param_branches = self._generate_semantic_ner_for_parameter(
                page_id=page_id,
                intent_name=intent_name,
                param_name=param_name,
                param_name_normalized=param_name_normalized,
                entity_data=entity_data,
                capture_variable=capture_variable,
                condition_id=condition_id,
                gen_unique_node_name=gen_unique_node_name,
                lang=lang
            )
            
            nodes.extend(param_nodes)
            condition_branches.extend(param_branches)
        
        return nodes, condition_branches
    
    def _generate_semantic_ner_for_parameter(
        self,
        page_id: str,
        intent_name: str,
        param_name: str,
        param_name_normalized: str,
        entity_data: List[Dict[str, Any]],
        capture_variable: str,
        condition_id: str,
        gen_unique_node_name: Callable[[str, str], str],
        lang: str = 'en'
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        为单个参数生成 Semantic NER 节点组
        
        结构：
        [SemanticJudgment] → [Code (设置参数值)]
        
        Args:
            page_id: 页面ID
            intent_name: 意图名称
            param_name: 参数名称
            param_name_normalized: 规范化的参数名称
            entity_data: 实体数据列表 [{value, synonyms}]
            capture_variable: 用户输入变量名
            condition_id: 来源的语义条件ID
            gen_unique_node_name: 生成唯一节点名的函数
            lang: 语言代码
            
        Returns:
            (节点列表, 条件分支列表)
        """
        nodes = []
        condition_branches = []
        
        safe_param_name = param_name.replace(' ', '_').replace('-', '_').replace('.', '_')
        
        # 注意：不再单独生成 Capture 节点，复用调用方已有的 capture 节点
        # capture_variable 参数应该是调用方提供的变量名（如 last_user_response）
        
        # 1. 生成 SemanticJudgment 节点（用于实体值匹配）
        semantic_node_name = gen_unique_node_name(f'ner_semantic_{safe_param_name}', page_id)
        
        # 构建 semantic_conditions（每个实体标准值作为一个条件）
        semantic_conditions = []
        code_nodes_info = []  # 存储每个分支对应的 Code 节点信息
        
        for entry in entity_data:
            value = entry.get('value', '')
            if not value:
                continue
            
            synonyms = entry.get('synonyms', [])
            semantic_condition_id = str(uuid.uuid4())
            
            # 构建 positive_examples（使用同义词）
            positive_examples = []
            added_values = set()  # 用于去重
            
            for syn in synonyms:
                if syn and syn not in added_values:  # 确保同义词非空且不重复
                    positive_examples.append({
                        "id": str(uuid.uuid4())[:21],  # 生成类似 nanoid 的短 ID
                        "question": syn
                    })
                    added_values.add(syn)
            
            # 确保标准值也在 positive_examples 中（如果还没有添加）
            if value not in added_values:
                positive_examples.append({
                    "id": str(uuid.uuid4())[:21],
                    "question": value
                })
            
            semantic_condition = {
                "condition_id": semantic_condition_id,
                "intent_id": None,  # NER 不需要 intent_id
                "name": value,  # 使用实体标准值作为条件名称
                "desc": "",
                "refer_questions": [
                    {
                        "id": str(uuid.uuid4())[:21],
                        "question": ""
                    }
                ],
                "positive_examples": positive_examples,
                "negative_examples": [
                    {
                        "id": str(uuid.uuid4())[:21],
                        "value": ""
                    }
                ],
                "condition_config": {
                    "keyword_enable": False,
                    "keywords": [],
                    "keyword_type": 1,
                    "keyword_hit_variable_assign": "",
                    "regular_enable": False,
                    "regular_str": "",
                    "sft_model_enable": False,
                    "sft_model_name": "",
                    "sft_model_reponse_structure": {},
                    "llm_enable": False,
                    "embedding_enable": True
                }
            }
            semantic_conditions.append(semantic_condition)
            
            # 记录 Code 节点信息
            code_nodes_info.append({
                "condition_id": semantic_condition_id,
                "value": value,
                "param_name": param_name_normalized
            })
        
        # 构建 default_condition（Other 分支）
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
                "sft_model_reponse_structure": {
                    "label": "",
                    "value": ""
                },
                "llm_enable": True,  # Other 分支使用 LLM 兜底
                "embedding_enable": False
            }
        }
        
        # 记录 Other 分支的 Code 节点信息
        code_nodes_info.append({
            "condition_id": default_condition_id,
            "value": "",  # Other 分支设置空值
            "param_name": param_name_normalized,
            "is_default": True
        })
        
        # 设置 embedding_language
        embedding_language = lang if lang else "en"
        
        # 构建 global_config
        semantic_global_config = {
            "is_chatflow": True,
            "is_start_intent": 0,
            "embedding_model_name": self.global_config.get("embedding_model_name", "bge-m3"),
            "embedding_semantic_proportion": 1,
            "confidence": self.global_config.get("semantic_confidence", 70),
            "embedding_max_reference_knowledge_num": 3,
            "embedding_rerank_enable": True,
            "embedding_rerank_model_name": self.global_config.get("embedding_rerank_model_name", "bge-reranker-v2-m3"),
            "embedding_rerank_confidence": self.global_config.get("embedding_rerank_confidence", 50),
            "embedding_llm_enable": False,
            "embedding_llm_model_name": "",
            "embedding_llm_prompt": "",
            "embedding_llm_return_count": 1,
            "allow_update_embedding": True,
            "default_condition_name": "Other",
            "input_type": 1,
            "input_variable": capture_variable,
            "sft_model_name": "",
            "embedding_confidence": self.global_config.get("semantic_confidence", 70),
            "embedding_language": embedding_language
        }
        
        # 构建 SemanticJudgment 节点（中间格式）
        # step6 的 _create_semantic_judgment 会将其转换为最终格式
        semantic_node = {
            "type": "semanticJudgment",
            "name": semantic_node_name,
            "title": f"Extract Parameters for {intent_name}",
            # config 嵌套结构（step6 期望的格式）
            "config": {
                "semantic_conditions": semantic_conditions,
                "default_condition": default_condition,
                "global_config": semantic_global_config,
                "title": f"Extract Parameters for {intent_name}"
            },
            # 内部使用的属性（用于边生成和识别）
            "_is_ner_semantic": True,
            "_ner_param_name": param_name_normalized,
            "_code_nodes_info": code_nodes_info,
            "from_semantic_condition_id": condition_id
        }
        
        nodes.append(semantic_node)
        
        # 3. 为每个分支生成 Code 节点（设置参数值）
        # 使用简单的中间格式，step6 会负责转换为最终格式
        for code_info in code_nodes_info:
            branch_condition_id = code_info["condition_id"]
            value = code_info["value"]
            is_default = code_info.get("is_default", False)
            
            # 生成 Code 节点名称
            if is_default:
                code_node_name = gen_unique_node_name(f'set_params_other_{safe_param_name}', page_id)
                code_title = f"Set Parameters for Other"
            else:
                safe_value = value.replace(' ', '_').replace('-', '_').replace('.', '_')
                code_node_name = gen_unique_node_name(f'set_params_{safe_value}', page_id)
                code_title = f"Set Parameters for {value}"
            
            # 构建 Code 节点的代码（完整的 main 函数）
            code_content = f'''
def main() -> dict:
  return {{
    "{param_name}": "{value}"
  }}
'''
            
            # 使用简单的中间格式（与 converter.py 其他节点一致）
            # step6 的 _create_code_node 会将其转换为最终格式
            code_node = {
                "type": "code",
                "name": code_node_name,
                "title": code_title,
                "code": code_content,
                # outputs 使用格式化的字典列表，直接传递给 step6
                "outputs": [
                    {
                        "name": param_name,  # 保持原始大小写格式（如 Common_YesNo）
                        "type": "string",
                        "variable_assign": param_name_normalized.lower()  # 小写（如 common_yesno）
                    }
                ],
                "args": [],
                # 内部使用的属性（用于边生成）
                "_is_ner_code": True,
                "_ner_param_name": param_name_normalized,
                "_ner_value": value,
                "_from_semantic_condition_id": branch_condition_id,
                "from_semantic_condition_id": condition_id
            }
            
            nodes.append(code_node)
            
            # 记录条件分支信息（用于边生成）
            condition_branches.append({
                "condition_id": branch_condition_id,
                "condition_name": value if not is_default else "Other",
                "target_node": code_node_name,
                "is_default": is_default,
                "_ner_branch": True,
                "_semantic_node_name": semantic_node_name,
                "_code_node_name": code_node_name,
                "from_semantic_condition_id": condition_id
            })
        
        # 记录节点连接关系（供边生成使用）
        # Semantic → Code（通过 condition_branches 记录）
        semantic_node["_condition_branches"] = condition_branches
        
        return nodes, condition_branches


def create_ner_generator(
    ner_version: str,
    global_config: Dict[str, Any] = None,
    entities_with_synonyms: Dict[str, Dict[str, List[Dict]]] = None,
    entity_candidates: Dict[str, Dict[str, List[str]]] = None
) -> NERNodeGeneratorBase:
    """
    NER 节点生成器工厂函数
    
    Args:
        ner_version: NER 版本 ("llm" 或 "semantic")
        global_config: 全局配置
        entities_with_synonyms: 实体的同义词数据
        entity_candidates: 实体候选值
        
    Returns:
        对应版本的 NER 节点生成器实例
    """
    if ner_version == "semantic":
        logger.info("🔄 使用 Semantic NER 节点生成器")
        return SemanticNERNodeGenerator(
            global_config=global_config,
            entities_with_synonyms=entities_with_synonyms,
            entity_candidates=entity_candidates
        )
    else:
        logger.info("🔄 使用 LLM NER 节点生成器（默认）")
        return LLMNERNodeGenerator(
            global_config=global_config,
            entities_with_synonyms=entities_with_synonyms,
            entity_candidates=entity_candidates
        )


def generate_ner_edges(
    nodes: List[Dict[str, Any]],
    semantic_node_name: str = None
) -> List[Dict[str, Any]]:
    """
    为 Semantic NER 节点生成边（edges）
    
    Args:
        nodes: 节点列表
        semantic_node_name: 语义判断节点名称
        
    Returns:
        边列表
    """
    edges = []
    
    # 找到 NER 相关的节点
    capture_nodes = [n for n in nodes if n.get('_is_ner_capture')]
    semantic_nodes = [n for n in nodes if n.get('_is_ner_semantic')]
    code_nodes = [n for n in nodes if n.get('_is_ner_code')]
    
    for capture_node in capture_nodes:
        # Capture → Semantic
        next_node_name = capture_node.get('_next_node')
        if next_node_name:
            semantic_node = next((n for n in semantic_nodes if n.get('name') == next_node_name), None)
            if semantic_node:
                edge_id = f"vueflow__edge-{capture_node.get('blockId', '')}{capture_node['data'].get('sourceHandle', '')}-{semantic_node.get('blockId', '')}{semantic_node.get('id', '')}"
                edges.append({
                    "id": edge_id,
                    "type": "custom",
                    "source": capture_node.get('blockId', ''),
                    "target": semantic_node.get('blockId', ''),
                    "sourceHandle": capture_node['data'].get('sourceHandle', ''),
                    "targetHandle": semantic_node.get('id', ''),
                    "data": {"hovering": False},
                    "label": "",
                    "zIndex": 0,
                    "animated": False
                })
    
    for semantic_node in semantic_nodes:
        # Semantic → Code（根据条件分支）
        condition_branches = semantic_node.get('_condition_branches', [])
        for branch in condition_branches:
            code_node_name = branch.get('_code_node_name')
            if code_node_name:
                code_node = next((n for n in code_nodes if n.get('name') == code_node_name), None)
                if code_node:
                    branch_condition_id = branch.get('condition_id', '')
                    edge_id = f"vueflow__edge-{semantic_node.get('blockId', '')}{branch_condition_id}-{code_node.get('blockId', '')}{code_node.get('id', '')}"
                    edges.append({
                        "id": edge_id,
                        "type": "custom",
                        "source": semantic_node.get('blockId', ''),
                        "target": code_node.get('blockId', ''),
                        "sourceHandle": branch_condition_id,
                        "targetHandle": code_node.get('id', ''),
                        "data": {"hovering": False},
                        "label": "",
                        "zIndex": 0,
                        "animated": False
                    })
    
    return edges

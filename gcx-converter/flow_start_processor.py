"""
Flow Start Page Processor (legacy single-entry workflow)
-------------------------------------------------------
本文件用于处理 flow 层级的 start page（flow.flow.transitionEvents），
生成一条“单一入口路由”式的 workflow：capture → kb → code → condition → jump/page。

说明：
- 这是旧的单一入口方案：所有请求先在主 Flow 的 start page 统一识别/分发意图。
- 当前 step2 的主流程（convert_to_multiple_workflows）不再调用这里的逻辑，
  而是为每个 triggerIntent 生成独立的 page-level workflow，入口路由应在外层配置。
- 保留此文件仅作参考/兼容，如果未来不再需要单一入口路由，可以安全删除。
"""

import json
from typing import Dict, List, Any, Tuple


class FlowStartPageProcessor:
    """
    处理 Flow 层级的 start page 的类
    专门负责解析 flow.flow.transitionEvents 中的意图和条件逻辑
    """
    
    def __init__(self, 
                 intents_mapping: Dict[str, str],
                 intent_parameters_map: Dict[str, List[Dict[str, Any]]]):
        """
        初始化 Flow Start Page 处理器
        
        Args:
            intents_mapping: 意图ID到名称的映射
            intent_parameters_map: 意图ID到参数列表的映射
        """
        self.intents_mapping = intents_mapping
        self.intent_parameters_map = intent_parameters_map
        self.node_counter = 0
    
    def _generate_node_name(self, base_name: str) -> str:
        """生成唯一的节点名称"""
        name = f"{base_name}_{self.node_counter}"
        self.node_counter += 1
        return name
    
    def parse_flow_transition_events(self, transition_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        解析 flow 层级的 transitionEvents
        
        Args:
            transition_events: flow.flow.transitionEvents 列表
            
        Returns:
            transition_info_list: 标准化的转换信息列表
        """
        transition_info_list = []
        
        for event in transition_events:
            trigger_intent_id = event.get('triggerIntentId', '')
            handler = event.get('transitionEventHandler', {})
            target_page_id = handler.get('targetPageId')
            target_flow_id = handler.get('targetFlowId')
            
            # 获取 beforeTransition 中的 setParameterActions
            before_transition = handler.get('beforeTransition', {})
            set_parameter_actions = before_transition.get('setParameterActions', [])
            
            # 构造 transition_info
            transition_info = {
                "target_page_id": target_page_id,
                "target_flow_id": target_flow_id,
                "has_intent": bool(trigger_intent_id),
                "intent_id": trigger_intent_id if trigger_intent_id else None,
                "intent_name": self.intents_mapping.get(trigger_intent_id, trigger_intent_id) if trigger_intent_id else None,
                "has_parameters": False,
                "parameters": [],
                "set_parameter_actions": set_parameter_actions
            }
            
            # 检查该 intent 是否有 parameters 需要提取
            if trigger_intent_id and trigger_intent_id in self.intent_parameters_map:
                transition_info["has_parameters"] = True
                transition_info["parameters"] = self.intent_parameters_map[trigger_intent_id]
            
            transition_info_list.append(transition_info)
        
        return transition_info_list
    
    def generate_intent_recognition_nodes(self, transition_info_list: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str, str, str]:
        """
        生成意图识别相关节点：capture → kb → code
        
        Returns:
            (nodes, capture_name, kb_name, code_name)
        """
        nodes = []
        
        # 1. Capture 节点 - 使用 last_user_response 变量
        capture_name = self._generate_node_name('flow_capture')
        capture_variable = "last_user_response"
        nodes.append({
            "type": "captureUserReply",
            "name": capture_name,
            "title": "Capture User Input (Flow Start)",
            "variable_assign": capture_variable
        })
        
        # 2. Knowledge Base 节点 - RAG 使用 {{last_user_response}} 检索
        kb_name = self._generate_node_name('flow_kb')
        rag_output_variable = "rag_result"
        nodes.append({
            "type": "knowledgeAssignment",
            "name": kb_name,
            "title": "RAG Intent Matching (Flow)",
            "variable_assign": rag_output_variable,
            "knowledge_base_ids": [10212],
            "rag_question": f"{{{{{capture_variable}}}}}"  # 使用模板变量格式 {{last_user_response}}
        })
        
        # 3. Code 节点 - 提取 intent
        code_name = self._generate_node_name('flow_extract_intent')
        intent_variable = "intent"
        code_content = f"""import re
def main({rag_output_variable}) -> dict:
    match = re.search(r"A:(.*)", {rag_output_variable})
    if match:
        result = match.group(1).strip()
    else:
        result = "unknown"
    return {{
        "{intent_variable}": result
    }}"""
        
        nodes.append({
            "type": "code",
            "name": code_name,
            "title": "Extract Intent (Flow)",
            "code": code_content,
            "outputs": [intent_variable],
            "args": [rag_output_variable]
        })
        
        return nodes, capture_name, kb_name, code_name
    
    def generate_condition_node(self, transition_info_list: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
        """
        生成条件路由节点
        
        Returns:
            (condition_node, fallback_text_name)
        """
        condition_name = self._generate_node_name('flow_condition')
        fallback_text_name = self._generate_node_name('flow_fallback')
        
        if_else_conditions = []
        intent_variable = "intent"
        
        # 为每个 intent 创建条件分支
        for idx, trans_info in enumerate(transition_info_list, 1):
            if trans_info.get('has_intent'):
                intent_name = trans_info.get('intent_name', '')
                target_page_id = trans_info.get('target_page_id')
                target_flow_id = trans_info.get('target_flow_id')
                
                condition_branch = {
                    "condition_id": f"flow_intent_{idx}",
                    "condition_name": f"Intent_{intent_name}",
                    "logical_operator": "and",
                    "conditions": [{
                        "condition_type": "variable",
                        "comparison_operator": "=",
                        "condition_value": intent_name,
                        "condition_variable": intent_variable
                    }],
                    "condition_action": [],
                    "target_page_id": target_page_id,
                    "target_flow_id": target_flow_id
                }
                if_else_conditions.append(condition_branch)
        
        # Fallback 条件
        fallback_condition = {
            "condition_id": "flow_fallback_condition",
            "condition_name": "Fallback",
            "logical_operator": "other",
            "conditions": [],
            "condition_action": [],
            "target_node": fallback_text_name
        }
        if_else_conditions.append(fallback_condition)
        
        condition_node = {
            "type": "condition",
            "name": condition_name,
            "title": "Intent Routing (Flow)",
            "if_else_conditions": if_else_conditions
        }
        
        return condition_node, fallback_text_name
    
    def generate_parameter_setting_nodes(self, transition_info_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        为每个有 setParameterActions 的 transition 生成参数设置节点
        
        Returns:
            param_code_nodes: 参数设置代码节点列表（带 transition_info 关联）
        """
        param_code_nodes = []
        
        for trans_info in transition_info_list:
            set_param_actions = trans_info.get('set_parameter_actions', [])
            if not set_param_actions:
                continue
            
            # 生成变量赋值代码
            code_lines = []
            output_variables = []
            input_variables = []  # 收集输入变量（从$引用中提取）
            
            for action in set_param_actions:
                parameter = action.get('parameter', '')
                value = action.get('value', '')
                
                # 生成 Python 代码
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
                    
                    code_lines.append(f"{parameter} = {input_var_name}")
                elif isinstance(value, str):
                    # 是字符串字面值
                    code_lines.append(f'{parameter} = "{value}"')
                else:
                    # 是数字或其他类型
                    code_lines.append(f"{parameter} = {value}")
                
                output_variables.append(parameter)
            
            # 生成 code 节点
            code_node_name = self._generate_node_name('flow_set_params')
            intent_name = trans_info.get('intent_name', 'Flow')
            
            param_code_node = {
                "type": "code",
                "name": code_node_name,
                "title": f"Set Parameters ({intent_name})",
                "code": "\n".join(code_lines),
                "outputs": output_variables,
                "args": input_variables,  # 添加输入变量
                "transition_info": trans_info  # 保存关联信息
            }
            param_code_nodes.append(param_code_node)
        
        return param_code_nodes
    
    def generate_jump_to_flow_nodes(self, transition_info_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        为跳转到其他 flow 的 transition 生成 jump 节点（当有 targetFlowId 且无 targetPageId 时）
        
        Args:
            transition_info_list: transition事件信息列表
            
        Returns:
            jump_nodes: jump节点列表
        """
        jump_nodes = []
        
        for trans_info in transition_info_list:
            target_page_id = trans_info.get('target_page_id')
            target_flow_id = trans_info.get('target_flow_id')
            
            # 只有当有 targetFlowId 且无 targetPageId 时才生成 jump 节点
            if target_flow_id and not target_page_id:
                jump_node_name = self._generate_node_name('flow_jump')
                
                jump_node = {
                    "type": "jump",
                    "name": jump_node_name,
                    "title": f"jump_to_{target_flow_id[:8]}",  # 使用 jump_to_DisplayName 格式
                    "jump_type": "flow",
                    "jump_robot_id": "",
                    "jump_robot_name": "",
                    "jump_carry_history_number": 5,
                    "jump_flow_name": "",
                    "jump_flow_uuid": target_flow_id,  # 保存 target_flow_id
                    "jump_carry_userinput": True,
                    "transition_info": trans_info  # 保存关联信息
                }
                jump_nodes.append(jump_node)
        
        return jump_nodes
    
    def _find_jump_node_for_target(self, target_flow_id: str, target_page_id: str, jump_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        查找与目标 flow 对应的 jump 节点（仅当有 targetFlowId 且无 targetPageId 时）
        
        Args:
            target_flow_id: 目标flow ID
            target_page_id: 目标page ID
            jump_nodes: jump节点列表
            
        Returns:
            匹配的jump节点或None
        """
        if not target_flow_id or target_page_id:
            return None
        
        for jump_node in jump_nodes:
            trans_info = jump_node.get('transition_info', {})
            if trans_info.get('target_flow_id') == target_flow_id and not trans_info.get('target_page_id'):
                return jump_node
        return None
    
    def generate_edges(self, 
                      start_node_name: str,
                      capture_name: str,
                      kb_name: str,
                      code_name: str,
                      condition_node: Dict[str, Any],
                      fallback_text_name: str,
                      param_code_nodes: List[Dict[str, Any]],
                      jump_nodes: List[Dict[str, Any]] = None,
                      slot_llm_name: str = None,
                      slot_parse_name: str = None,
                      need_slot_extraction: bool = False) -> List[Dict[str, Any]]:
        """
        生成所有边配置
        """
        edges = []
        condition_name = condition_node['name']
        
        # 1. start → capture
        edges.append({
            "source_node": start_node_name,
            "target_node": capture_name,
            "connection_type": "default"
        })
        
        # 2. capture → (可选) slot LLM → slot parse → kb
        if need_slot_extraction and slot_llm_name and slot_parse_name:
            edges.append({
                "source_node": capture_name,
                "target_node": slot_llm_name,
                "connection_type": "default"
            })
            edges.append({
                "source_node": slot_llm_name,
                "target_node": slot_parse_name,
                "connection_type": "default"
            })
            edges.append({
                "source_node": slot_parse_name,
                "target_node": kb_name,
                "connection_type": "default"
            })
        else:
            edges.append({
                "source_node": capture_name,
                "target_node": kb_name,
                "connection_type": "default"
            })
        
        # 3. kb → code
        edges.append({
            "source_node": kb_name,
            "target_node": code_name,
            "connection_type": "default"
        })
        
        # 4. code → condition
        edges.append({
            "source_node": code_name,
            "target_node": condition_name,
            "connection_type": "default"
        })
        
        # 5. condition 的各个分支
        for branch in condition_node['if_else_conditions']:
            condition_id = branch.get('condition_id')
            
            if condition_id == 'flow_fallback_condition':
                # fallback → fallback_text
                edges.append({
                    "source_node": condition_name,
                    "target_node": fallback_text_name,
                    "connection_type": "condition",
                    "condition_id": condition_id
                })
            else:
                # 正常分支
                target_page_id = branch.get('target_page_id')
                target_flow_id = branch.get('target_flow_id')
                
                if target_page_id or target_flow_id:
                    # 检查是否需要跳转到另一个 flow
                    jump_node = None
                    if jump_nodes:
                        jump_node = self._find_jump_node_for_target(target_flow_id, target_page_id, jump_nodes)
                    
                    # 检查是否需要插入 param_code 节点
                    param_code = self._find_param_code_for_target(target_page_id, param_code_nodes)
                    
                    if jump_node:
                        # 跳转到另一个 flow
                        if param_code:
                            # condition → param_code → jump_node
                            edges.append({
                                "source_node": condition_name,
                                "target_node": param_code['name'],
                                "connection_type": "condition",
                                "condition_id": condition_id
                            })
                            edges.append({
                                "source_node": param_code['name'],
                                "target_node": jump_node['name'],
                                "connection_type": "default"
                            })
                        else:
                            # condition → jump_node
                            edges.append({
                                "source_node": condition_name,
                                "target_node": jump_node['name'],
                                "connection_type": "condition",
                                "condition_id": condition_id
                            })
                    else:
                        # 跳转到 page
                        target_page = target_page_id or target_flow_id
                        if param_code:
                            # condition → param_code → target_page
                            edges.append({
                                "source_node": condition_name,
                                "target_node": param_code['name'],
                                "connection_type": "condition",
                                "condition_id": condition_id
                            })
                            edges.append({
                                "source_node": param_code['name'],
                                "target_node": f"page_{target_page[:8]}",
                                "connection_type": "default"
                            })
                        else:
                            # condition → target_page
                            edges.append({
                                "source_node": condition_name,
                                "target_node": f"page_{target_page[:8]}",
                                "connection_type": "condition",
                                "condition_id": condition_id
                            })
        
        # 6. fallback_text → capture (循环)
        edges.append({
            "source_node": fallback_text_name,
            "target_node": capture_name,
            "connection_type": "default"
        })
        
        return edges
    
    def _find_param_code_for_target(self, target_page_id: str, param_code_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """查找与目标页面对应的参数设置节点"""
        for pcode in param_code_nodes:
            trans_info = pcode.get('transition_info', {})
            if trans_info.get('target_page_id') == target_page_id:
                return pcode
        return None
    
    def process_flow(self, flow_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        处理完整的 flow start page
        
        Args:
            flow_data: flow 配置数据（从 exported_flow_*.json 读取）
            
        Returns:
            (nodes, edges): 节点和边列表
        """
        flow_info = flow_data.get('flow', {}).get('flow', {})
        flow_name = flow_info.get('displayName', 'Flow')
        transition_events = flow_info.get('transitionEvents', [])
        
        print(f"\n=== Processing Flow Start Page: {flow_name} ===")
        
        if not transition_events:
            print("  ✗ No transition events found")
            return [], []
        
        # 1. 解析 transitionEvents
        transition_info_list = self.parse_flow_transition_events(transition_events)
        print(f"  ✓ Parsed {len(transition_info_list)} transition events")
        
        # 检查是否所有 transition 都有 intent
        has_any_intent = any(t.get('has_intent') for t in transition_info_list)
        
        if not has_any_intent:
            print("  ✗ No intents found in transition events (event-driven only)")
            return [], []

        # 检查是否需要在 start page 做“intent + 抽槽”模式：
        # 条件：所有 transition 指向同一个目标（page/flow），且存在需要参数的意图
        unique_targets = {
            (t.get('target_page_id'), t.get('target_flow_id')) for t in transition_info_list
        }
        need_slot_extraction = (
            len(unique_targets) == 1 and any(t.get('has_parameters') for t in transition_info_list)
        )
        
        nodes = []
        
        # 2. 生成意图识别节点
        intent_nodes, capture_name, kb_name, code_name = self.generate_intent_recognition_nodes(transition_info_list)
        nodes.extend(intent_nodes)
        print(f"  ✓ Generated {len(intent_nodes)} intent recognition nodes")

        # 2.1 如果需要抽槽，在 capture 后插入 LLM 抽槽 + code 解析节点
        slot_llm_name = None
        slot_parse_name = None
        if need_slot_extraction:
            slot_llm_name = self._generate_node_name('flow_llm_extract')
            slot_parse_name = self._generate_node_name('flow_parse_params')

            # LLM 抽槽节点
            slot_llm_node = {
                "type": "llmVariableAssignment",
                "name": slot_llm_name,
                "title": "Extract Parameters from User Input (Flow)",
                # 这里复用 capture 的变量 last_user_response 作为提示输入
                "rag_question": "{{last_user_response}}",
                "variable_assign": "llm_params"
            }
            # code 解析节点（把 llm_params 透传成字典，便于下游使用）
            slot_parse_node = {
                "type": "code",
                "name": slot_parse_name,
                "title": "Parse Parameters from LLM (Flow)",
                "code": """def main(llm_params=None) -> dict:
    if not llm_params:
        return {}
    if isinstance(llm_params, dict):
        return llm_params
    # 如果是字符串或其他格式，可按需解析，这里简单返回空
    return {}""",
                "outputs": [],
                "args": ["llm_params"]
            }
            nodes.append(slot_llm_node)
            nodes.append(slot_parse_node)
            print("  ✓ Enabled slot extraction on start page (capture → llm → parse)")
        
        # 3. 生成条件路由节点
        condition_node, fallback_text_name = self.generate_condition_node(transition_info_list)
        nodes.append(condition_node)
        print(f"  ✓ Generated condition routing node")
        
        # 4. 生成 fallback 文本节点
        fallback_node = {
            "type": "textReply",
            "name": fallback_text_name,
            "title": "Fallback Response (Flow)",
            "plain_text": [{
                "text": "I didn't understand that. Can you try again?",
                "id": fallback_text_name
            }]
        }
        nodes.append(fallback_node)
        
        # 5. 生成参数设置节点
        param_code_nodes = self.generate_parameter_setting_nodes(transition_info_list)
        nodes.extend(param_code_nodes)
        if param_code_nodes:
            print(f"  ✓ Generated {len(param_code_nodes)} parameter setting nodes")
        
        # 6. 生成jump节点（用于跳转到另一个flow）
        jump_nodes = self.generate_jump_to_flow_nodes(transition_info_list)
        nodes.extend(jump_nodes)
        if jump_nodes:
            print(f"  ✓ Generated {len(jump_nodes)} jump to flow nodes")
        
        # 7. 生成边（支持可选的抽槽链路）
        edges = self.generate_edges(
            "start_node",
            capture_name,
            kb_name,
            code_name,
            condition_node,
            fallback_text_name,
            param_code_nodes,
            jump_nodes,
            slot_llm_name=slot_llm_name,
            slot_parse_name=slot_parse_name,
            need_slot_extraction=need_slot_extraction
        )
        print(f"  ✓ Generated {len(edges)} edges")
        print(f"  Total: {len(nodes)} nodes, {len(edges)} edges\n")
        
        return nodes, edges


def test_flow_processor():
    """测试函数"""
    # 加载 intents 映射
    with open('intents_en.json', 'r', encoding='utf-8') as f:
        intents_data = json.load(f)
        intents_mapping = {i['id']: i['displayName'] for i in intents_data.get('intents', [])}
    
    # 加载 intent parameters
    intent_parameters_map = {}
    try:
        with open('intent_parameters.json', 'r', encoding='utf-8') as f:
            params_data = json.load(f)
            for intent in params_data.get('intentsWithParameters', []):
                intent_parameters_map[intent['id']] = intent.get('parameters', [])
    except:
        pass
    
    # 创建处理器
    processor = FlowStartPageProcessor(intents_mapping, intent_parameters_map)
    
    # 加载 flow 数据
    with open('exported_flow_TXNAndSTMT_Deeplink.json', 'r', encoding='utf-8') as f:
        flow_data = json.load(f)
    
    # 处理
    nodes, edges = processor.process_flow(flow_data)
    
    # 保存结果
    with open('flow_nodes.json', 'w', encoding='utf-8') as f:
        json.dump({"nodes": nodes}, f, ensure_ascii=False, indent=2)
    
    with open('flow_edges.json', 'w', encoding='utf-8') as f:
        json.dump({"edges": edges}, f, ensure_ascii=False, indent=2)
    
    print(f"✓ Saved to flow_nodes.json and flow_edges.json")


if __name__ == '__main__':
    test_flow_processor()


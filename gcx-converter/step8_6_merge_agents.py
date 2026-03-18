# -*- coding: utf-8 -*-
"""
Agent 智能合并脚本
用于将多个 agent merged JSON 文件合并为一个domain agent

直接运行即可自动合并 output 目录下所有 agent

使用方式:
    python step_8_6_merge_agents.py
"""

import json
import uuid
import os
import sys
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from copy import deepcopy

# 设置控制台编码
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

# ============================================
# 硬编码配置 - 根据需要修改这里
# ============================================

# 获取脚本所在目录作为项目根目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR

# 输入: output 目录路径
INPUT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# 输出: 合并后的文件路径
MERGED_OUTPUT_FILE = os.path.join(INPUT_OUTPUT_DIR, "HSBC_Combined_Agent.json")

# 合并后 agent 的名称
MERGED_AGENT_NAME = "HSBC_Combined_Agent"

# 合并后 agent 的描述
MERGED_AGENT_DESCRIPTION = "Combined agent from multiple sources"

# ============================================


@dataclass
class MergeReport:
    """合并报告数据类"""
    total_intentions: int = 0
    total_flows: int = 0
    total_variables: int = 0
    total_entities: int = 0
    
    duplicate_intentions: List[str] = field(default_factory=list)
    duplicate_flows: List[str] = field(default_factory=list)
    duplicate_variables: List[str] = field(default_factory=list)
    duplicate_entities: List[str] = field(default_factory=list)
    
    renamed_items: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    orphan_flows: List[str] = field(default_factory=list)
    
    def print_report(self):
        """打印合并报告"""
        print("\n" + "=" * 60)
        print("[REPORT] Merge Detection Report")
        print("=" * 60)
        
        print(f"\n[OK] Merge Statistics:")
        print(f"   - Total intentions: {self.total_intentions}")
        print(f"   - Total flows: {self.total_flows}")
        print(f"   - Total variables: {self.total_variables}")
        print(f"   - Total entities: {self.total_entities}")
        
        if self.duplicate_intentions:
            print(f"\n[DUP] Duplicate intentions (deduplicated): {len(self.duplicate_intentions)}")
            for item in self.duplicate_intentions[:5]:
                print(f"   - {item}")
            if len(self.duplicate_intentions) > 5:
                print(f"   ... and {len(self.duplicate_intentions) - 5} more")
        
        if self.duplicate_flows:
            print(f"\n[DUP] Duplicate flows (deduplicated): {len(self.duplicate_flows)}")
            for item in self.duplicate_flows[:5]:
                print(f"   - {item}")
            if len(self.duplicate_flows) > 5:
                print(f"   ... and {len(self.duplicate_flows) - 5} more")
        
        if self.duplicate_variables:
            print(f"\n[DUP] Duplicate variables (merged): {len(self.duplicate_variables)}")
            for item in self.duplicate_variables[:5]:
                print(f"   - {item}")
        
        if self.renamed_items:
            print(f"\n[RENAME] Renamed items: {len(self.renamed_items)}")
            for item in self.renamed_items[:5]:
                print(f"   - {item}")
        
        if self.orphan_flows:
            print(f"\n[WARN] Orphan flows (not referenced by intentions): {len(self.orphan_flows)}")
            for item in self.orphan_flows[:5]:
                print(f"   - {item}")
            if len(self.orphan_flows) > 5:
                print(f"   ... and {len(self.orphan_flows) - 5} more")
        
        if self.warnings:
            print(f"\n[WARN] Warnings: {len(self.warnings)}")
            for warning in self.warnings[:10]:
                print(f"   - {warning}")
            if len(self.warnings) > 10:
                print(f"   ... and {len(self.warnings) - 10} more warnings")
        
        if self.errors:
            print(f"\n[ERROR] Errors: {len(self.errors)}")
            for error in self.errors:
                print(f"   - {error}")
        
        print("\n" + "=" * 60)
        if not self.errors:
            print("[OK] Merge completed!")
        else:
            print("[ERROR] Merge has errors, please check")
        print("=" * 60 + "\n")


class AgentMerger:
    """Agent 智能合并器"""
    
    BUILTIN_VARIABLES = {
        "last_user_response",
        "LLM_response", 
        "DialogueHistory",
        "username",
        "cTime",
        "user_utterance",
        "llm_params"
    }
    
    def __init__(self, global_config: dict = {}):
        self.report = MergeReport()
        self.flow_uuid_mapping: Dict[str, str] = {}
        self.global_config = global_config
        
    def load_agent(self, file_path: str) -> Optional[Dict[str, Any]]:
        """加载单个 agent JSON 文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            self.report.errors.append(f"File not found: {file_path}")
            return None
        except json.JSONDecodeError as e:
            self.report.errors.append(f"JSON parse error ({file_path}): {str(e)}")
            return None
    
    def generate_uuid(self) -> str:
        """生成新的 UUID"""
        return str(uuid.uuid4())
    
    def merge_intention_list(
        self, 
        agents: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """
        合并意图列表
        
        去重规则：
        - 如果 intention_name 相同且 action_flow_uuid 也相同 → 去重（丢弃后面的）
        - 如果 intention_name 相同但 action_flow_uuid 不同 → 重命名（加后缀）
        """
        merged_intentions = []
        seen_intention_names: Set[str] = set()
        # 记录 intention_name -> flow_uuid 的映射，用于判断是否真正重复
        intention_flow_mapping: Dict[str, str] = {}
        intention_to_flow: Dict[str, str] = {}
        
        sort_num = 1
        
        for agent in agents:
            planning = agent.get("planning", {})
            resource = planning.get("resource", {})
            intention_list = resource.get("intention_list", [])
            
            for intention in intention_list:
                intention_name = intention.get("intention_name", "")
                flow_uuid = intention.get("action_flow_uuid", "")
                
                # 检查是否存在同名 intention
                if intention_name in seen_intention_names:
                    existing_flow_uuid = intention_flow_mapping.get(intention_name, "")
                    
                    # 如果绑定的 flow 也相同，则是真正的重复，跳过
                    if flow_uuid == existing_flow_uuid:
                        self.report.duplicate_intentions.append(intention_name)
                        continue
                    
                    # 如果绑定的 flow 不同，则重命名 intention
                    original_name = intention_name
                    counter = 1
                    while intention_name in seen_intention_names:
                        intention_name = f"{original_name}_{counter}"
                        counter += 1
                    self.report.renamed_items.append(f"Intent: {original_name} -> {intention_name} (different flow)")
                
                seen_intention_names.add(intention_name)
                intention_flow_mapping[intention_name] = flow_uuid
                
                new_intention = deepcopy(intention)
                new_intention["intention_name"] = intention_name  # 使用可能被重命名的名称
                new_intention["sort_num"] = sort_num
                sort_num += 1
                
                merged_intentions.append(new_intention)
                
                if flow_uuid:
                    intention_to_flow[intention_name] = flow_uuid
        
        self.report.total_intentions = len(merged_intentions)
        return merged_intentions, intention_to_flow
    
    def _get_flow_list(self, agent: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        获取 agent 中的 flow 列表，兼容两种格式：
        1. 顶层的 flow_list（Dyna.ai 导出格式）
        2. planning.resource.chatflow.chatflow_list（step8 生成格式）
        """
        # 优先尝试顶层的 flow_list
        flow_list = agent.get("flow_list", [])
        if flow_list:
            return flow_list
        
        # 如果顶层没有，尝试从 planning.resource.chatflow.chatflow_list 获取
        chatflow_list = agent.get("planning", {}).get("resource", {}).get(
            "chatflow", {}
        ).get("chatflow_list", [])
        
        return chatflow_list
    
    def merge_flow_list(
        self, 
        agents: List[Dict[str, Any]],
        intention_to_flow: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """合并工作流列表"""
        merged_flows = []
        seen_flow_uuids: Set[str] = set()
        seen_flow_names: Set[str] = set()
        
        referenced_flow_uuids = set(intention_to_flow.values())
        
        for agent in agents:
            flow_list = self._get_flow_list(agent)
            
            for flow in flow_list:
                flow_uuid = flow.get("flow_uuid", "")
                flow_name = flow.get("flow_name", "")
                
                if flow_uuid in seen_flow_uuids:
                    self.report.duplicate_flows.append(f"{flow_name} (UUID: {flow_uuid[:8]}...)")
                    continue
                
                seen_flow_uuids.add(flow_uuid)
                
                original_name = flow_name
                counter = 1
                while flow_name in seen_flow_names:
                    flow_name = f"{original_name}_{counter}"
                    counter += 1
                    self.report.renamed_items.append(f"Flow: {original_name} -> {flow_name}")
                
                seen_flow_names.add(flow_name)
                
                new_flow = deepcopy(flow)
                if flow_name != original_name:
                    new_flow["flow_name"] = flow_name
                
                merged_flows.append(new_flow)
                
                if flow_uuid not in referenced_flow_uuids:
                    self.report.orphan_flows.append(flow_name)
        
        self.report.total_flows = len(merged_flows)
        return merged_flows
    
    def merge_variables(self, agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """合并变量列表"""
        merged_variables = []
        seen_variable_names: Set[str] = set()
        
        for agent in agents:
            flow_list = self._get_flow_list(agent)
            
            for flow in flow_list:
                variables = flow.get("variables", [])
                
                for var in variables:
                    var_name = var.get("variable_name", "")
                    
                    if var_name in self.BUILTIN_VARIABLES:
                        if var_name not in seen_variable_names:
                            seen_variable_names.add(var_name)
                            merged_variables.append(deepcopy(var))
                        continue
                    
                    if var_name in seen_variable_names:
                        self.report.duplicate_variables.append(var_name)
                        continue
                    
                    seen_variable_names.add(var_name)
                    merged_variables.append(deepcopy(var))
        
        self.report.total_variables = len(merged_variables)
        return merged_variables
    
    def merge_entities(self, agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """合并实体列表"""
        merged_entities = []
        entity_map: Dict[str, Dict[str, Any]] = {}
        
        for agent in agents:
            flow_list = self._get_flow_list(agent)
            
            for flow in flow_list:
                entities = flow.get("entities", [])
                
                for entity in entities:
                    entity_name = entity.get("entity_name", "")
                    
                    if not entity_name:
                        continue
                    
                    if entity_name in entity_map:
                        existing = entity_map[entity_name]
                        existing_values = set(existing.get("values", []))
                        new_values = set(entity.get("values", []))
                        
                        if existing_values != new_values:
                            merged_values = list(existing_values | new_values)
                            existing["values"] = merged_values
                            self.report.duplicate_entities.append(
                                f"{entity_name} (values merged)"
                            )
                    else:
                        entity_map[entity_name] = deepcopy(entity)
        
        merged_entities = list(entity_map.values())
        self.report.total_entities = len(merged_entities)
        return merged_entities
    
    def merge_basic_config(
        self, 
        agents: List[Dict[str, Any]], 
        new_name: str,
        new_description: str = ""
    ) -> Dict[str, Any]:
        """合并基础配置"""
        if not agents:
            return {}
        
        base_config = deepcopy(
            agents[0].get("planning", {}).get("basic_config", {})
        )
        
        base_config["robot_name"] = new_name
        
        all_knowledge_bases = set()
        for agent in agents:
            kb_list = agent.get("planning", {}).get("resource", {}).get(
                "knowledge", {}
            ).get("knowledge_base_list", [])
            all_knowledge_bases.update(kb_list)
        
        max_context = max(
            agent.get("planning", {}).get("basic_config", {}).get("max_context_num", 5)
            for agent in agents
        )
        base_config["max_context_num"] = max_context
        
        return base_config
    
    def merge_knowledge_config(self, agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并知识库配置"""
        if not agents:
            return {}
        
        base_knowledge = deepcopy(
            agents[0].get("planning", {}).get("resource", {}).get("knowledge", {})
        )
        
        all_kb_list = []
        seen_kb_ids = set()
        
        for agent in agents:
            kb_list = agent.get("planning", {}).get("resource", {}).get(
                "knowledge", {}
            ).get("knowledge_base_list", [])
            
            for kb in kb_list:
                kb_id = kb if isinstance(kb, str) else kb.get("id", "")
                if kb_id and kb_id not in seen_kb_ids:
                    seen_kb_ids.add(kb_id)
                    all_kb_list.append(kb)
        
        base_knowledge["knowledge_base_list"] = all_kb_list
        
        return base_knowledge
    
    def _merge_chatflow_config(self, agents: List[Dict[str, Any]], merged_flows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        合并 chatflow 配置，将 merged_flows 放入 chatflow_list
        """
        # 以第一个 agent 的 chatflow 配置为基础
        base_chatflow = deepcopy(
            agents[0].get("planning", {}).get("resource", {}).get("chatflow", {})
        )
        
        # 如果没有 chatflow 配置，创建默认结构
        if not base_chatflow:
            # writed by senlin.deng 2026-01-27
            # 必须要与step8_merge_to_planning.py中的配置一致
            base_chatflow = {
                "is_chatflow": True,
                "is_start_intent": True,
                "prioritize_flow_uuid": "",
                "intent_confidence": 0.6,
                "intent_embedding_rerank_enable": False,
                "intent_embedding_rerank_model_name": "",
                "intent_rerank_confidence": 0.5,
                "intent_embedding_llm_enable": True,
                "intent_embedding_llm_model_name": self.global_config.get('llmcodemodel', 'qwen3-30b-a3b'),
                "intent_embedding_llm_return_count": 3,
                "intent_embedding_llm_prompt": "",
                "intent_final_llm_model_name": self.global_config.get('llmcodemodel', 'qwen3-30b-a3b'),
                "intent_final_llm_prompt": "",
                "chatflow_list": []
            }
        
        # 将合并后的 flows 放入 chatflow_list
        base_chatflow["chatflow_list"] = merged_flows
        
        return base_chatflow
    
    def _merge_plugin_config(self, agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并 plugin 配置"""
        base_plugin = deepcopy(
            agents[0].get("planning", {}).get("resource", {}).get("plugin", {})
        )
        return base_plugin if base_plugin else {}
    
    def _merge_database_config(self, agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并 database 配置"""
        base_database = deepcopy(
            agents[0].get("planning", {}).get("resource", {}).get("database", {})
        )
        return base_database if base_database else {}
    
    def _merge_advanced_config(self, agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并 advanced_config"""
        base_advanced = deepcopy(
            agents[0].get("planning", {}).get("advanced_config", {})
        )
        return base_advanced if base_advanced else {}
    
    def _merge_realtime_config(self, agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并 realtime_config"""
        base_realtime = deepcopy(
            agents[0].get("planning", {}).get("realtime_config", {})
        )
        return base_realtime if base_realtime else {}

    def merge(
        self, 
        input_files: List[str], 
        new_name: str,
        new_description: str = "",
        output_file: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """执行合并操作"""
        self.report = MergeReport()
        self.flow_uuid_mapping = {}
        
        agents = []
        for file_path in input_files:
            print(f"[LOAD] Loading: {os.path.basename(file_path)}")
            agent = self.load_agent(file_path)
            if agent:
                agents.append(agent)
        
        if not agents:
            self.report.errors.append("No agent files loaded successfully")
            self.report.print_report()
            return None
        
        print(f"\n[MERGE] Starting merge of {len(agents)} agents...")
        
        print("   - Merging intentions...")
        merged_intentions, intention_to_flow = self.merge_intention_list(agents)
        
        print("   - Merging flows...")
        merged_flows = self.merge_flow_list(agents, intention_to_flow)
        
        print("   - Counting variables and entities...")
        self.merge_variables(agents)
        self.merge_entities(agents)
        
        print("   - Merging configs...")
        merged_basic_config = self.merge_basic_config(agents, new_name, new_description)
        merged_knowledge = self.merge_knowledge_config(agents)
        merged_chatflow = self._merge_chatflow_config(agents, merged_flows)
        merged_plugin = self._merge_plugin_config(agents)
        merged_database = self._merge_database_config(agents)
        merged_advanced = self._merge_advanced_config(agents)
        merged_realtime = self._merge_realtime_config(agents)
        
        if not new_description:
            new_description = f"Merged agent from {len(agents)} sources"
        
        # 构建符合 Dyna.ai 格式的 merged_agent
        # 注意：flow 放在 planning.resource.chatflow.chatflow_list 中，而不是顶层 flow_list
        merged_agent = {
            "planning": {
                "agent_info": {
                    "description": new_description,
                    "avatar_name": agents[0].get("planning", {}).get("agent_info", {}).get("avatar_name", "5"),
                    "avatar_color": agents[0].get("planning", {}).get("agent_info", {}).get("avatar_color", "5")
                },
                "basic_config": merged_basic_config,
                "resource": {
                    "knowledge": merged_knowledge,
                    "intention_list": merged_intentions,
                    "chatflow": merged_chatflow,
                    "plugin": merged_plugin,
                    "database": merged_database
                },
                "advanced_config": merged_advanced,
                "realtime_config": merged_realtime
            }
        }
        
        self.report.print_report()
        
        if output_file:
            print(f"[SAVE] Saving to: {output_file}")
            try:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(merged_agent, f, ensure_ascii=False)
                print(f"[OK] File saved!")
            except Exception as e:
                self.report.errors.append(f"Failed to save file: {str(e)}")
                print(f"[ERROR] Save failed: {str(e)}")
        
        return merged_agent


def find_merged_files(output_dir: str) -> List[str]:
    """
    在 output 目录中查找所有 step8_final 下的 merged JSON 文件
    """
    merged_files = []
    
    if not os.path.exists(output_dir):
        print(f"[ERROR] Directory not found: {output_dir}")
        return merged_files
    
    for item in os.listdir(output_dir):
        item_path = os.path.join(output_dir, item)
        
        if os.path.isdir(item_path):
            step8_path = os.path.join(item_path, "step8_final")
            
            if os.path.exists(step8_path):
                for file_name in os.listdir(step8_path):
                    if file_name.endswith("_merged.json"):
                        merged_files.append(os.path.join(step8_path, file_name))
    
    return merged_files


def main():
    """主函数 - 直接运行合并"""
    print("\n" + "=" * 60)
    print("[START] Agent Smart Merge Tool")
    print("=" * 60)
    
    print(f"\n[CONFIG] Settings:")
    print(f"   - Input directory: {INPUT_OUTPUT_DIR}")
    print(f"   - Output file: {MERGED_OUTPUT_FILE}")
    print(f"   - Agent name: {MERGED_AGENT_NAME}")
    
    # 查找所有 merged 文件
    print(f"\n[FIND] Searching for merged files...")
    input_files = find_merged_files(INPUT_OUTPUT_DIR)
    
    if not input_files:
        print(f"[ERROR] No merged files found in {INPUT_OUTPUT_DIR}")
        print("Please make sure there are step8_final/*_merged.json files")
        return
    
    print(f"[FIND] Found {len(input_files)} files:")
    for f in input_files:
        print(f"   - {os.path.basename(f)}")
    
    # 执行合并
    merger = AgentMerger()
    result = merger.merge(
        input_files=input_files,
        new_name=MERGED_AGENT_NAME,
        new_description=MERGED_AGENT_DESCRIPTION,
        output_file=MERGED_OUTPUT_FILE
    )
    
    if result:
        print(f"\n[SUCCESS] Merge completed!")
        print(f"[OUTPUT] {MERGED_OUTPUT_FILE}")
    else:
        print(f"\n[ERROR] Merge failed!")


if __name__ == "__main__":
    main()

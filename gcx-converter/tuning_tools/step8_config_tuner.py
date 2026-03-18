# -*- coding: utf-8 -*-
"""
Step8 配置微调工具
==================
功能：一键修改 step8_final 目录下 JSON 文件中的：
1. 知识库阈值配置
2. 大模型节点中的模型选择

使用方法：
    python step8_config_tuner.py --input_dir output/step8_final
"""

import json
import os
import argparse
from pathlib import Path
from typing import Dict, List, Any, Optional


class Step8ConfigTuner:
    """Step8 JSON 配置微调工具"""
    
    def __init__(self, input_dir: str = "output/step8_final"):
        """
        初始化工具
        
        Args:
            input_dir: step8_final 目录路径
        """
        self.input_dir = input_dir
        self.modified_files = []
        
    def load_json_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        加载 JSON 文件
        
        Args:
            file_path: JSON 文件路径
            
        Returns:
            JSON 数据字典，失败返回 None
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data
        except Exception as e:
            print(f"  ❌ 加载文件失败 {file_path}: {e}")
            return None
    
    def save_json_file(self, file_path: str, data: Dict[str, Any]) -> bool:
        """
        保存 JSON 文件
        
        Args:
            file_path: JSON 文件路径
            data: JSON 数据字典
            
        Returns:
            是否保存成功
        """
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"  ❌ 保存文件失败 {file_path}: {e}")
            return False
    
    def update_knowledge_base_thresholds(
        self,
        data: Dict[str, Any],
        thresholds: Dict[str, Any]
    ) -> bool:
        """
        更新知识库阈值配置
        
        Args:
            data: JSON 数据字典
            thresholds: 阈值配置字典，例如：
                {
                    "qa_min_threshold": 0.95,
                    "correlation_threshold": 65,
                    "reference_questions_threshold": 50,
                    "diff_threshold": 0.4,
                    "ratio_threshold": 0.15,
                    "file_comparison_threshold": 95,
                    "qa_comparison_threshold": 95,
                    "text_comparison_threshold": 95
                }
        
        Returns:
            是否进行了修改
        """
        modified = False
        
        # 获取知识库配置路径
        knowledge_base_list = data.get("planning", {}).get("resource", {}).get("knowledge", {}).get("knowledge_base_list", [])
        
        if not knowledge_base_list:
            print("  ⚠️  未找到知识库配置")
            return False
        
        # 更新全局 qa_min_threshold
        if "qa_min_threshold" in thresholds:
            qa_min_threshold = thresholds["qa_min_threshold"]
            old_value = data.get("planning", {}).get("resource", {}).get("knowledge", {}).get("qa_min_threshold")
            if old_value != qa_min_threshold:
                data["planning"]["resource"]["knowledge"]["qa_min_threshold"] = qa_min_threshold
                print(f"    ✅ 更新 qa_min_threshold: {old_value} -> {qa_min_threshold}")
                modified = True
        
        # 更新每个知识库的阈值
        for kb in knowledge_base_list:
            kb_name = kb.get("knowledge_base_name", "未知")
            kb_modified = False
            
            # 可更新的阈值字段
            threshold_fields = [
                "correlation_threshold",
                "reference_questions_threshold",
                "diff_threshold",
                "ratio_threshold",
                "file_comparison_threshold",
                "qa_comparison_threshold",
                "text_comparison_threshold"
            ]
            
            for field in threshold_fields:
                if field in thresholds:
                    new_value = thresholds[field]
                    old_value = kb.get(field)
                    if old_value != new_value:
                        kb[field] = new_value
                        print(f"    ✅ 知识库 [{kb_name}] {field}: {old_value} -> {new_value}")
                        kb_modified = True
            
            if kb_modified:
                modified = True
        
        return modified
    
    def update_llm_models(
        self,
        data: Dict[str, Any],
        model_config: Dict[str, str]
    ) -> bool:
        """
        更新大模型节点中的模型选择
        
        Args:
            data: JSON 数据字典
            model_config: 模型配置字典，例如：
                {
                    "basic_chat_model": "aliyun-qwen-plus",  # 基础配置中的 chat_model
                    "intent_final_llm_model": "aliyun-qwen-plus",  # 意图识别最终LLM模型
                    "workflow_llm_nodes": "azure-gpt-4o",  # workflow 中所有 LLM 节点的模型
                    "intention_info_llm_model": "bge-large-zh"  # intention_info 中的 llm_model
                }
        
        Returns:
            是否进行了修改
        """
        modified = False
        
        # 1. 更新基础配置中的 chat_model
        if "basic_chat_model" in model_config:
            new_model = model_config["basic_chat_model"]
            old_model = data.get("planning", {}).get("basic_config", {}).get("chat_model")
            if old_model != new_model:
                data["planning"]["basic_config"]["chat_model"] = new_model
                print(f"    ✅ 更新 basic_config.chat_model: {old_model} -> {new_model}")
                modified = True
        
        # 2. 更新意图识别最终LLM模型
        if "intent_final_llm_model" in model_config:
            new_model = model_config["intent_final_llm_model"]
            old_model = data.get("planning", {}).get("resource", {}).get("chatflow", {}).get("intent_final_llm_model_name")
            if old_model != new_model:
                data["planning"]["resource"]["chatflow"]["intent_final_llm_model_name"] = new_model
                print(f"    ✅ 更新 intent_final_llm_model_name: {old_model} -> {new_model}")
                modified = True
        
        # 3. 更新 workflow 中所有 LLM 节点的模型
        if "workflow_llm_nodes" in model_config:
            new_model = model_config["workflow_llm_nodes"]
            chatflow_list = data.get("planning", {}).get("resource", {}).get("chatflow", {}).get("chatflow_list", [])
            
            updated_count = 0
            for workflow in chatflow_list:
                nodes = workflow.get("nodes", [])
                for node in nodes:
                    # 检查是否是 LLM 节点
                    node_type = node.get("type", "")
                    if node_type in ["llmVariableAssignment", "llmReply"]:
                        # 更新 llm_config 中的 llm_name
                        llm_config = node.get("config", {}).get("llm_config", {})
                        if llm_config:
                            old_model = llm_config.get("llm_name")
                            if old_model != new_model:
                                llm_config["llm_name"] = new_model
                                updated_count += 1
            
            if updated_count > 0:
                print(f"    ✅ 更新了 {updated_count} 个 workflow LLM 节点的模型: -> {new_model}")
                modified = True
        
        # 4. 更新 intention_info 中的 llm_model
        if "intention_info_llm_model" in model_config:
            new_model = model_config["intention_info_llm_model"]
            chatflow_list = data.get("planning", {}).get("resource", {}).get("chatflow", {}).get("chatflow_list", [])
            
            updated_count = 0
            for workflow in chatflow_list:
                intention_info = workflow.get("intention_info", {})
                if intention_info:
                    old_model = intention_info.get("llm_model")
                    if old_model != new_model:
                        intention_info["llm_model"] = new_model
                        updated_count += 1
            
            if updated_count > 0:
                print(f"    ✅ 更新了 {updated_count} 个 intention_info.llm_model: -> {new_model}")
                modified = True
        
        return modified
    
    def process_file(
        self,
        file_path: str,
        thresholds: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, str]] = None
    ) -> bool:
        """
        处理单个 JSON 文件
        
        Args:
            file_path: JSON 文件路径
            thresholds: 阈值配置字典
            model_config: 模型配置字典
        
        Returns:
            是否处理成功
        """
        print(f"\n📄 处理文件: {os.path.basename(file_path)}")
        
        # 加载文件
        data = self.load_json_file(file_path)
        if data is None:
            return False
        
        file_modified = False
        
        # 更新知识库阈值
        if thresholds:
            print("  🔧 更新知识库阈值...")
            if self.update_knowledge_base_thresholds(data, thresholds):
                file_modified = True
        
        # 更新大模型配置
        if model_config:
            print("  🤖 更新大模型配置...")
            if self.update_llm_models(data, model_config):
                file_modified = True
        
        # 保存文件
        if file_modified:
            if self.save_json_file(file_path, data):
                print(f"  ✅ 文件已更新并保存")
                self.modified_files.append(file_path)
                return True
            else:
                return False
        else:
            print(f"  ⏭️  文件无需更新")
            return True
    
    def process_all_files(
        self,
        thresholds: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, str]] = None
    ) -> Dict[str, int]:
        """
        处理目录下所有 JSON 文件
        
        Args:
            thresholds: 阈值配置字典
            model_config: 模型配置字典
        
        Returns:
            处理结果统计
        """
        print("=" * 70)
        print("🔧 Step8 配置微调工具")
        print("=" * 70)
        
        if not os.path.exists(self.input_dir):
            print(f"❌ 目录不存在: {self.input_dir}")
            return {"success": 0, "failed": 0, "skipped": 0}
        
        # 查找所有 JSON 文件
        json_files = [
            os.path.join(self.input_dir, f)
            for f in os.listdir(self.input_dir)
            if f.endswith('.json') and os.path.isfile(os.path.join(self.input_dir, f))
        ]
        
        if not json_files:
            print(f"⚠️  在 {self.input_dir} 中未找到 JSON 文件")
            return {"success": 0, "failed": 0, "skipped": 0}
        
        print(f"📂 找到 {len(json_files)} 个 JSON 文件\n")
        
        # 处理每个文件
        success_count = 0
        failed_count = 0
        skipped_count = 0
        
        for json_file in sorted(json_files):
            if self.process_file(json_file, thresholds, model_config):
                success_count += 1
            else:
                failed_count += 1
        
        # 输出统计
        print("\n" + "=" * 70)
        print("📊 处理完成")
        print("=" * 70)
        print(f"✅ 成功处理: {success_count} 个文件")
        print(f"❌ 处理失败: {failed_count} 个文件")
        print(f"📝 已修改文件: {len(self.modified_files)} 个")
        if self.modified_files:
            print("\n已修改的文件:")
            for f in self.modified_files:
                print(f"  - {os.path.basename(f)}")
        print("=" * 70)
        
        return {
            "success": success_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "modified": len(self.modified_files)
        }


def parse_thresholds(threshold_str: str) -> Dict[str, Any]:
    """
    解析阈值配置字符串
    
    格式: key1=value1,key2=value2,...
    例如: qa_min_threshold=0.9,correlation_threshold=70
    
    Args:
        threshold_str: 阈值配置字符串
    
    Returns:
        阈值配置字典
    """
    thresholds = {}
    if not threshold_str:
        return thresholds
    
    for item in threshold_str.split(','):
        item = item.strip()
        if '=' not in item:
            continue
        
        key, value = item.split('=', 1)
        key = key.strip()
        value = value.strip()
        
        # 尝试转换为数字
        try:
            if '.' in value:
                value = float(value)
            else:
                value = int(value)
        except ValueError:
            pass  # 保持字符串
        
        thresholds[key] = value
    
    return thresholds


def parse_model_config(model_str: str) -> Dict[str, str]:
    """
    解析模型配置字符串
    
    格式: key1=value1,key2=value2,...
    例如: basic_chat_model=aliyun-qwen-plus,workflow_llm_nodes=azure-gpt-4o
    
    Args:
        model_str: 模型配置字符串
    
    Returns:
        模型配置字典
    """
    model_config = {}
    if not model_str:
        return model_config
    
    for item in model_str.split(','):
        item = item.strip()
        if '=' not in item:
            continue
        
        key, value = item.split('=', 1)
        key = key.strip()
        value = value.strip()
        
        model_config[key] = value
    
    return model_config


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Step8 配置微调工具 - 一键修改知识库阈值和大模型节点配置",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 修改知识库阈值
  python step8_config_tuner.py --thresholds "qa_min_threshold=0.9,correlation_threshold=70"
  
  # 修改大模型配置
  python step8_config_tuner.py --models "basic_chat_model=aliyun-qwen-plus,workflow_llm_nodes=azure-gpt-4o"
  
  # 同时修改阈值和模型
  python step8_config_tuner.py --thresholds "qa_min_threshold=0.9" --models "workflow_llm_nodes=azure-gpt-4o"
  
  # 指定输入目录
  python step8_config_tuner.py --input_dir output/step8_final --thresholds "correlation_threshold=70"
        """
    )
    
    parser.add_argument(
        '--input_dir',
        type=str,
        default='output/step8_final',
        help='step8_final 目录路径 (默认: output/step8_final)'
    )
    
    parser.add_argument(
        '--thresholds',
        type=str,
        default='',
        help='知识库阈值配置，格式: key1=value1,key2=value2\n'
             '支持的字段: qa_min_threshold, correlation_threshold, reference_questions_threshold,\n'
             '          diff_threshold, ratio_threshold, file_comparison_threshold,\n'
             '          qa_comparison_threshold, text_comparison_threshold'
    )
    
    parser.add_argument(
        '--models',
        type=str,
        default='',
        help='大模型配置，格式: key1=value1,key2=value2\n'
             '支持的字段: basic_chat_model, intent_final_llm_model, workflow_llm_nodes,\n'
             '          intention_info_llm_model'
    )
    
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='交互式模式，逐步输入配置'
    )
    
    args = parser.parse_args()
    
    # 解析配置
    thresholds = parse_thresholds(args.thresholds) if args.thresholds else None
    model_config = parse_model_config(args.models) if args.models else None
    
    # 交互式模式
    if args.interactive or (not thresholds and not model_config):
        print("=" * 70)
        print("🔧 Step8 配置微调工具 - 交互式模式")
        print("=" * 70)
        print("\n请输入要修改的配置（留空跳过）:\n")
        
        # 知识库阈值配置
        print("📊 知识库阈值配置:")
        print("  支持的字段: qa_min_threshold, correlation_threshold, reference_questions_threshold,")
        print("            diff_threshold, ratio_threshold, file_comparison_threshold,")
        print("            qa_comparison_threshold, text_comparison_threshold")
        threshold_input = input("  阈值配置 (格式: key=value,key2=value2): ").strip()
        if threshold_input:
            thresholds = parse_thresholds(threshold_input)
        
        # 大模型配置
        print("\n🤖 大模型配置:")
        print("  支持的字段: basic_chat_model, intent_final_llm_model, workflow_llm_nodes,")
        print("            intention_info_llm_model")
        model_input = input("  模型配置 (格式: key=value,key2=value2): ").strip()
        if model_input:
            model_config = parse_model_config(model_input)
        
        if not thresholds and not model_config:
            print("\n⚠️  未输入任何配置，退出")
            return
    
    # 创建工具实例并处理
    tuner = Step8ConfigTuner(input_dir=args.input_dir)
    tuner.process_all_files(thresholds=thresholds, model_config=model_config)


if __name__ == "__main__":
    main()


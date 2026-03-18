"""
Step 8.5: 更新最终合并文件中所有 workflow 的 knowledge_base_ids

功能：
1. 读取知识库映射文件 (kb_per_intent_results_*.json)
2. 读取最终合并文件 (*_merged.json)
3. 更新所有 workflow 中 knowledgeAssignment 节点的 knowledge_base_ids
4. 保存更新后的文件

使用方法：
    python step8_5_update_final_kb_ids.py --final_file <merged_json_file> --kb_mapping <kb_results_json_file> --output_file <output_file>

或在 run_all_steps_server.py 中自动调用：
    from step8_5_update_final_kb_ids import update_final_kb_ids
    update_final_kb_ids(final_file, kb_mapping_file, output_file)
"""

import json
import os
import argparse
from typing import Dict, List, Any, Optional

from logger_config import get_logger
logger = get_logger(__name__)


def load_kb_mapping(kb_mapping_file: str) -> Dict[str, str]:
    """
    加载知识库映射文件

    Args:
        kb_mapping_file: kb_per_intent_results_*.json 文件路径

    Returns:
        intent_name -> kb_id 的映射字典
    """
    if not os.path.exists(kb_mapping_file):
        logger.error(f"❌ 知识库映射文件不存在: {kb_mapping_file}")
        return {}

    try:
        with open(kb_mapping_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 解析映射关系
        kb_mapping = {}
        results = data.get('results', {})

        for intent_name, info in results.items():
            kb_id = info.get('kb_id')
            if kb_id and isinstance(kb_id, (str, int)):
                kb_mapping[intent_name] = str(kb_id)

        logger.info(f"✅ 加载了 {len(kb_mapping)} 个知识库映射")
        if kb_mapping:
            sample = list(kb_mapping.items())[0]
            logger.debug(f"   示例: {sample[0]} -> {sample[1]}")

        return kb_mapping

    except Exception as e:
        logger.error(f"❌ 加载知识库映射文件失败: {e}")
        return {}


def build_workflow_to_intent_mapping(kb_mapping_file: str, intents_file: str = None) -> Dict[str, str]:
    """
    建立 workflow 名称到 intent 名称的映射关系

    Args:
        kb_mapping_file: 知识库映射文件路径
        intents_file: intents文件路径（可选，用于建立intent_id到displayName的映射）

    Returns:
        workflow_name -> intent_name 的映射字典
    """
    # 从 kb_mapping_file 中提取所有 intent_name
    intent_names = []
    if os.path.exists(kb_mapping_file):
        try:
            with open(kb_mapping_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                results = data.get('results', {})

                for intent_name in results.keys():
                    intent_names.append(intent_name)
        except Exception as e:
            logger.warning(f"⚠️ 读取知识库映射文件失败: {e}")

    # 建立 workflow_name 到 intent_name 的映射
    workflow_to_intent = {}

    for intent_name in intent_names:
        # 生成可能的 workflow 名称变体
        # 例如: "AccountServicing_AntiScamAlert" 可能对应 "accountservicing_antiscamalert"

        # 变体1: 全部小写
        variant1 = intent_name.lower()
        workflow_to_intent[variant1] = intent_name

        # 变体2: 移除下划线
        variant2 = intent_name.lower().replace('_', '')
        if variant2 != variant1:
            workflow_to_intent[variant2] = intent_name

        # 变体3: 移除下划线和连字符
        variant3 = intent_name.lower().replace('_', '').replace('-', '')
        if variant3 not in workflow_to_intent:
            workflow_to_intent[variant3] = intent_name

        # 变体4: 基于intent名称的模式匹配
        # AccountServicing_Xxx -> accountserviceing_xxx (假设有拼写错误)
        if intent_name.startswith('AccountServicing_'):
            suffix = intent_name[len('AccountServicing_'):].lower()
            variant4 = f'accountservicing_{suffix}'
            workflow_to_intent[variant4] = intent_name

        # 原始名称也保留
        workflow_to_intent[intent_name] = intent_name

    logger.debug(f"建立了 {len(workflow_to_intent)} 个 workflow 到 intent 的映射关系")
    if intent_names:
        logger.debug(f"示例映射: accountservicing_antiscamalert -> {workflow_to_intent.get('accountservicing_antiscamalert', 'N/A')}")
    return workflow_to_intent


def find_intent_from_workflow_name(workflow_name: str, kb_mapping: Dict[str, str]) -> Optional[str]:
    """
    从 workflow 名称中找到对应的 intent 名称

    Args:
        workflow_name: workflow 名称，如 "intent_1", "accountservicing_complywithfatca"
        kb_mapping: 知识库映射字典

    Returns:
        匹配的 intent 名称，如果找不到则返回 None
    """
    # 方法1: 直接匹配
    if workflow_name in kb_mapping:
        return workflow_name

    # 方法2: 从文件名提取 intent 名称
    # 例如: "generated_workflow_accountservicing_complywithfatca.json" -> "accountservicing_complywithfatca"
    if workflow_name.startswith('generated_workflow_'):
        intent_name = workflow_name[len('generated_workflow_'):]
        if intent_name.endswith('.json'):
            intent_name = intent_name[:-5]
        if intent_name in kb_mapping:
            return intent_name

    # 方法3: 移除前缀和后缀进行匹配
    if workflow_name.startswith('generated_workflow_'):
        intent_name = workflow_name[len('generated_workflow_'):]
        if intent_name.endswith('.json'):
            intent_name = intent_name[:-5]
    else:
        intent_name = workflow_name

    # 尝试匹配包含关系的intent名称
    for mapped_intent in kb_mapping.keys():
        if intent_name in mapped_intent or mapped_intent in intent_name:
            return mapped_intent

    return None


def update_workflow_kb_ids(workflow: Dict[str, Any], kb_mapping: Dict[str, str], workflow_name: str, workflow_to_intent: Dict[str, str] = None) -> int:
    """
    更新单个 workflow 中的 knowledge_base_ids

    Args:
        workflow: workflow 数据
        kb_mapping: 知识库映射字典 (intent_name -> kb_id)
        workflow_name: workflow 名称，用于日志
        workflow_to_intent: workflow_name -> intent_name 的映射字典

    Returns:
        更新了的节点数量
    """
    updated_count = 0

    # 查找 flow 层级的 intent
    flow_intent = None

    # 方法1: 通过 workflow_to_intent 映射查找
    if workflow_to_intent and workflow_name in workflow_to_intent:
        flow_intent = workflow_to_intent[workflow_name]
    else:
        # 方法2: 回退到原来的匹配逻辑
        flow_intent = find_intent_from_workflow_name(workflow_name, kb_mapping)

    flow_kb_id = None
    if flow_intent and flow_intent in kb_mapping:
        flow_kb_id = kb_mapping[flow_intent]
        logger.debug(f"   📌 Flow-level intent: {flow_intent} -> KB {flow_kb_id}")
    else:
        logger.debug(f"   ⚠️  无法为 workflow '{workflow_name}' 找到对应的 intent")

    # 遍历所有节点
    nodes = workflow.get('nodes', [])
    for node in nodes:
        if node.get('type') == 'knowledgeAssignment':
            node_name = node.get('name', 'unknown')

            # 获取当前的 knowledge_base_ids（在 config.rag_config 下）
            config = node.get('config', {})
            rag_config = config.get('rag_config', {})
            current_kb_ids = rag_config.get('knowledge_base_ids', [])
            if not isinstance(current_kb_ids, list):
                current_kb_ids = []

            # 检查是否有 page_intents
            page_intents = rag_config.get('page_intents', [])
            logger.debug(f"   🔍 节点 '{node_name}': page_intents={page_intents}, current_kb_ids={current_kb_ids}")
            if page_intents:
                # page-level 节点：根据 page_intents 匹配知识库
                new_kb_ids = []
                for intent_name in page_intents:
                    kb_id = kb_mapping.get(intent_name)
                    if kb_id:
                        kb_id_int = int(kb_id) if isinstance(kb_id, str) and kb_id.isdigit() else kb_id
                        if kb_id_int not in new_kb_ids:
                            new_kb_ids.append(kb_id_int)
                    else:
                        logger.debug(f"   ⚠️  Page intent '{intent_name}' 找不到对应的知识库")

                if new_kb_ids:
                    # 按升序排序
                    new_kb_ids.sort()
                    if current_kb_ids != new_kb_ids:
                        logger.debug(f"   🔧 更新 page-level 节点 '{node_name}': {current_kb_ids} -> {new_kb_ids}")
                        rag_config['knowledge_base_ids'] = new_kb_ids
                        updated_count += 1
                    else:
                        logger.debug(f"   ✅ Page-level 节点 '{node_name}' 已是最新的")
                else:
                    logger.debug(f"   ⚠️  Page-level 节点 '{node_name}' 无有效知识库ID")

            elif flow_kb_id:
                # flow-level 节点：使用 flow 层级的知识库
                flow_kb_id_int = int(flow_kb_id) if isinstance(flow_kb_id, str) and flow_kb_id.isdigit() else flow_kb_id
                new_kb_ids = [flow_kb_id_int]

                if current_kb_ids != new_kb_ids:
                    logger.debug(f"   🔧 更新 flow-level 节点 '{node_name}': {current_kb_ids} -> {new_kb_ids}")
                    rag_config['knowledge_base_ids'] = new_kb_ids
                    updated_count += 1
                else:
                    logger.debug(f"   ✅ Flow-level 节点 '{node_name}' 已是最新的")

            else:
                logger.debug(f"   ⚠️  节点 '{node_name}' 无有效的知识库配置")

    return updated_count


def update_final_kb_ids(final_file: str, kb_mapping_file: str, intents_file: str = None, output_file: Optional[str] = None) -> bool:
    """
    更新最终合并文件中的 knowledge_base_ids

    Args:
        final_file: 最终合并的 JSON 文件路径 (*_merged.json)
        kb_mapping_file: 知识库映射文件路径 (kb_per_intent_results_*.json)
        intents_file: intents文件路径（可选，用于建立更好的映射关系）
        output_file: 输出文件路径，如果为 None 则覆盖原文件

    Returns:
        是否成功
    """
    logger.info("=" * 80)
    logger.info("Step 8.5: 更新最终文件中的知识库IDs")
    logger.info("=" * 80)

    # 检查输入文件
    if not os.path.exists(final_file):
        logger.error(f"❌ 最终文件不存在: {final_file}")
        return False

    if not os.path.exists(kb_mapping_file):
        logger.error(f"❌ 知识库映射文件不存在: {kb_mapping_file}")
        return False

    # 加载知识库映射
    kb_mapping = load_kb_mapping(kb_mapping_file)
    if not kb_mapping:
        logger.error("❌ 无法加载知识库映射，终止更新")
        return False

    # 建立 workflow 到 intent 的映射关系
    workflow_to_intent = {}
    if intents_file:
        workflow_to_intent = build_workflow_to_intent_mapping(kb_mapping_file, intents_file)

    # 读取最终文件
    try:
        with open(final_file, 'r', encoding='utf-8') as f:
            final_data = json.load(f)
        logger.info(f"✅ 成功加载最终文件: {final_file}")
    except Exception as e:
        logger.error(f"❌ 读取最终文件失败: {e}")
        return False

    # 确定输出文件
    if output_file is None:
        output_file = final_file
        logger.info("📝 将覆盖原文件")
    else:
        logger.info(f"📝 输出到新文件: {output_file}")

    # 更新所有 workflow 的 knowledge_base_ids
    chatflow_list = final_data.get('planning', {}).get('resource', {}).get('chatflow', {}).get('chatflow_list', [])

    total_updated = 0
    total_workflows = len(chatflow_list)

    logger.info(f"🔍 开始处理 {total_workflows} 个 workflow...")

    for idx, workflow in enumerate(chatflow_list, 1):
        flow_uuid = workflow.get('flow_uuid', '')
        flow_name = workflow.get('flow_name', f'workflow_{idx}')

        logger.info(f"   [{idx}/{total_workflows}] 处理 workflow: {flow_name}")

        # 更新这个 workflow 中的 knowledge_base_ids
        updated = update_workflow_kb_ids(workflow, kb_mapping, flow_name, workflow_to_intent)
        total_updated += updated

        if updated > 0:
            logger.info(f"   ✅ 更新了 {updated} 个节点")
        else:
            logger.debug(f"   ℹ️  无需更新")

    # 保存更新后的文件
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
        logger.info(f"💾 文件已保存: {output_file}")
    except Exception as e:
        logger.error(f"❌ 保存文件失败: {e}")
        return False

    logger.info("")
    logger.info("=" * 80)
    logger.info("🎉 Step 8.5 完成！")
    logger.info(f"   📊 处理了 {total_workflows} 个 workflow")
    logger.info(f"   🔧 更新了 {total_updated} 个节点的知识库IDs")
    logger.info("=" * 80)

    return True


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="更新最终合并文件中的知识库IDs")
    parser.add_argument('--final_file', required=True, help='最终合并的JSON文件路径 (*_merged.json)')
    parser.add_argument('--kb_mapping', required=True, help='知识库映射文件路径 (kb_per_intent_results_*.json)')
    parser.add_argument('--intents_file', help='intents文件路径（可选，用于建立更好的映射关系）')
    parser.add_argument('--output_file', help='输出文件路径（可选，默认覆盖原文件）')

    args = parser.parse_args()

    success = update_final_kb_ids(args.final_file, args.kb_mapping, args.intents_file, args.output_file)

    if success:
        logger.info("✅ 更新完成！")
    else:
        logger.error("❌ 更新失败！")
        exit(1)


if __name__ == "__main__":
    main()
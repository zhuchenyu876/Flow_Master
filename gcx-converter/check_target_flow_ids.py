"""
检查 exported_flow 中所有 targetFlowId 的情况
查看它们是否有 targetPageId，以及是否被转换到 nodes_config.json 中
"""

import json
import re
from typing import Dict, List, Set, Tuple


def check_target_flow_ids(exported_flow_path: str, nodes_config_path: str):
    """
    检查 exported_flow 中所有 targetFlowId 的情况
    """
    # 1. 从 exported_flow 文件中提取所有 targetFlowId 及其上下文
    print("=" * 70)
    print("检查 exported_flow 中所有 targetFlowId 的情况")
    print("=" * 70)
    
    with open(exported_flow_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 使用正则表达式提取所有 targetFlowId 及其上下文
    # 匹配包含 targetFlowId 的 JSON 对象
    pattern = r'\{[^}]*"targetFlowId":\s*"([a-f0-9-]+)"[^}]*\}'
    matches = re.finditer(pattern, content)
    
    flow_id_info: Dict[str, List[Dict]] = {}
    
    for match in matches:
        context = match.group(0)
        flow_id = match.group(1)
        
        # 检查是否有 targetPageId
        has_target_page_id = '"targetPageId"' in context
        target_page_id = None
        if has_target_page_id:
            page_id_match = re.search(r'"targetPageId":\s*"([a-f0-9-]+)"', context)
            if page_id_match:
                target_page_id = page_id_match.group(1)
        
        # 检查是否有 triggerIntentId
        has_trigger_intent = '"triggerIntentId"' in context
        trigger_intent_id = None
        if has_trigger_intent:
            intent_match = re.search(r'"triggerIntentId":\s*"([a-f0-9-]+)"', context)
            if intent_match:
                trigger_intent_id = intent_match.group(1)
        
        if flow_id not in flow_id_info:
            flow_id_info[flow_id] = []
        
        flow_id_info[flow_id].append({
            "has_target_page_id": has_target_page_id,
            "target_page_id": target_page_id,
            "has_trigger_intent": has_trigger_intent,
            "trigger_intent_id": trigger_intent_id,
            "context": context[:200]  # 只保存前200个字符
        })
    
    print(f"\n找到 {len(flow_id_info)} 个唯一的 targetFlowId:\n")
    
    # 2. 检查 nodes_config.json 中的情况
    with open(nodes_config_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)
    
    nodes = config_data.get("nodes", [])
    
    # 收集 nodes_config.json 中所有需要 jump 节点的 target_flow_id
    config_target_flow_ids: Set[str] = set()
    for node in nodes:
        if node.get("type") == "condition":
            if_else_conditions = node.get("if_else_conditions", [])
            for branch in if_else_conditions:
                target_flow_id = branch.get("target_flow_id")
                target_page_id = branch.get("target_page_id")
                
                # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
                if target_flow_id and not target_page_id:
                    config_target_flow_ids.add(target_flow_id)
    
    # 3. 分析每个 targetFlowId
    for flow_id in sorted(flow_id_info.keys()):
        occurrences = flow_id_info[flow_id]
        print(f"\n{'='*70}")
        print(f"targetFlowId: {flow_id}")
        print(f"{'='*70}")
        print(f"出现次数: {len(occurrences)}")
        
        # 统计有多少有 targetPageId，多少没有
        with_page_id = sum(1 for occ in occurrences if occ["has_target_page_id"])
        without_page_id = len(occurrences) - with_page_id
        
        print(f"  有 targetPageId: {with_page_id} 次")
        print(f"  无 targetPageId: {without_page_id} 次")
        
        # 检查是否在 nodes_config.json 中
        in_config = flow_id in config_target_flow_ids
        print(f"  在 nodes_config.json 中: {'✅ 是' if in_config else '❌ 否'}")
        
        if not in_config and without_page_id > 0:
            print(f"  ⚠️  警告: 这个 targetFlowId 没有 targetPageId，但不在 nodes_config.json 中！")
            print(f"     可能需要添加 jump 节点")
        
        # 显示一些示例上下文
        if without_page_id > 0:
            print(f"\n  示例（无 targetPageId）:")
            for occ in occurrences[:2]:  # 只显示前2个
                if not occ["has_target_page_id"]:
                    print(f"    - {occ['context']}")
    
    # 4. 总结
    print(f"\n\n{'='*70}")
    print("总结")
    print(f"{'='*70}")
    print(f"exported_flow 中的 targetFlowId 总数: {len(flow_id_info)}")
    print(f"nodes_config.json 中需要的 targetFlowId 数: {len(config_target_flow_ids)}")
    
    # 找出需要添加 jump 节点的 targetFlowId
    missing_flow_ids = []
    for flow_id, occurrences in flow_id_info.items():
        # 如果有任何一次出现没有 targetPageId，且不在 nodes_config.json 中
        has_without_page_id = any(not occ["has_target_page_id"] for occ in occurrences)
        if has_without_page_id and flow_id not in config_target_flow_ids:
            missing_flow_ids.append(flow_id)
    
    if missing_flow_ids:
        print(f"\n⚠️  发现 {len(missing_flow_ids)} 个 targetFlowId 可能需要添加 jump 节点:")
        for flow_id in missing_flow_ids:
            print(f"  - {flow_id}")
    else:
        print(f"\n✅ 所有需要的 jump 节点都已存在")


if __name__ == "__main__":
    exported_flow_path = "exported_flow_TXNAndSTMT_Deeplink.json"
    nodes_config_path = "output/step2_workflow_config/nodes_config.json"
    
    try:
        check_target_flow_ids(exported_flow_path, nodes_config_path)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


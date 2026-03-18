"""
检查 exported_flow 中所有 targetFlowId 的情况
"""

import json
from typing import Dict, List, Set


def check_target_flow_ids(exported_flow_path: str, nodes_config_path: str):
    """
    检查 exported_flow 中所有 targetFlowId 的情况
    """
    print("=" * 70)
    print("检查 exported_flow 中所有 targetFlowId 的情况")
    print("=" * 70)
    
    # 1. 读取 exported_flow 文件
    with open(exported_flow_path, 'r', encoding='utf-8') as f:
        flow_data = json.load(f)
    
    # 2. 递归查找所有 targetFlowId
    def find_target_flow_ids(obj, path="", results=None):
        if results is None:
            results = []
        
        if isinstance(obj, dict):
            # 检查是否有 targetFlowId
            if "targetFlowId" in obj:
                target_flow_id = obj.get("targetFlowId")
                target_page_id = obj.get("targetPageId")
                trigger_intent_id = obj.get("triggerIntentId")
                
                results.append({
                    "target_flow_id": target_flow_id,
                    "target_page_id": target_page_id,
                    "trigger_intent_id": trigger_intent_id,
                    "has_target_page_id": target_page_id is not None,
                    "path": path
                })
            
            # 递归处理所有值
            for key, value in obj.items():
                find_target_flow_ids(value, f"{path}.{key}" if path else key, results)
        
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                find_target_flow_ids(item, f"{path}[{idx}]", results)
        
        return results
    
    all_flow_ids_info = find_target_flow_ids(flow_data)
    
    # 3. 按 targetFlowId 分组
    flow_id_groups: Dict[str, List] = {}
    for info in all_flow_ids_info:
        flow_id = info["target_flow_id"]
        if flow_id not in flow_id_groups:
            flow_id_groups[flow_id] = []
        flow_id_groups[flow_id].append(info)
    
    print(f"\n找到 {len(flow_id_groups)} 个唯一的 targetFlowId:\n")
    
    # 4. 检查 nodes_config.json 中的情况
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
    
    # 5. 分析每个 targetFlowId
    for flow_id in sorted(flow_id_groups.keys()):
        occurrences = flow_id_groups[flow_id]
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
        
        # 显示一些示例
        if without_page_id > 0:
            print(f"\n  示例（无 targetPageId）:")
            for occ in occurrences[:2]:  # 只显示前2个
                if not occ["has_target_page_id"]:
                    print(f"    - triggerIntentId: {occ.get('trigger_intent_id', 'None')}")
                    print(f"      path: {occ.get('path', '')[:100]}")
    
    # 6. 总结
    print(f"\n\n{'='*70}")
    print("总结")
    print(f"{'='*70}")
    print(f"exported_flow 中的 targetFlowId 总数: {len(flow_id_groups)}")
    print(f"nodes_config.json 中需要的 targetFlowId 数: {len(config_target_flow_ids)}")
    
    # 找出需要添加 jump 节点的 targetFlowId
    missing_flow_ids = []
    for flow_id, occurrences in flow_id_groups.items():
        # 如果有任何一次出现没有 targetPageId，且不在 nodes_config.json 中
        has_without_page_id = any(not occ["has_target_page_id"] for occ in occurrences)
        if has_without_page_id and flow_id not in config_target_flow_ids:
            missing_flow_ids.append(flow_id)
    
    if missing_flow_ids:
        print(f"\n⚠️  发现 {len(missing_flow_ids)} 个 targetFlowId 可能需要添加 jump 节点:")
        for flow_id in missing_flow_ids:
            print(f"  - {flow_id}")
        return missing_flow_ids
    else:
        print(f"\n✅ 所有需要的 jump 节点都已存在")
        return []


if __name__ == "__main__":
    exported_flow_path = "exported_flow_TXNAndSTMT_Deeplink.json"
    nodes_config_path = "output/step2_workflow_config/nodes_config.json"
    
    try:
        missing_flow_ids = check_target_flow_ids(exported_flow_path, nodes_config_path)
        if missing_flow_ids:
            print(f"\n需要为这些 targetFlowId 添加 jump 节点")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


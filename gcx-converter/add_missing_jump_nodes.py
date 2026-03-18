"""
为 nodes_config.json 添加所有缺失的 jump 节点
从 exported_flow 文件中提取所有没有 targetPageId 的 targetFlowId，
然后为 nodes_config.json 添加对应的 jump 节点
"""

import json
import os
from typing import Dict, List, Set


def find_all_target_flow_ids_without_page_id(flow_data, path="", results=None):
    """
    递归查找所有没有 targetPageId 的 targetFlowId
    """
    if results is None:
        results = []
    
    if isinstance(obj, dict):
        # 检查是否有 targetFlowId
        if "targetFlowId" in obj:
            target_flow_id = obj.get("targetFlowId")
            target_page_id = obj.get("targetPageId")
            
            # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
            if target_flow_id and not target_page_id:
                results.append({
                    "target_flow_id": target_flow_id,
                    "path": path
                })
        
        # 递归处理所有值
        for key, value in obj.items():
            find_all_target_flow_ids_without_page_id(value, f"{path}.{key}" if path else key, results)
    
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            find_all_target_flow_ids_without_page_id(item, f"{path}[{idx}]", results)
    
    return results


def add_missing_jump_nodes(exported_flow_path: str, nodes_config_path: str):
    """
    从 exported_flow 文件中提取所有没有 targetPageId 的 targetFlowId，
    然后为 nodes_config.json 添加对应的 jump 节点
    """
    print("=" * 70)
    print("为 nodes_config.json 添加所有缺失的 jump 节点")
    print("=" * 70)
    
    # 1. 从 exported_flow 文件中提取所有没有 targetPageId 的 targetFlowId
    print("\n从 exported_flow 文件中提取所有没有 targetPageId 的 targetFlowId...")
    
    with open(exported_flow_path, 'r', encoding='utf-8') as f:
        flow_data = json.load(f)
    
    def find_target_flow_ids(obj, path="", results=None):
        if results is None:
            results = []
        
        if isinstance(obj, dict):
            # 检查是否有 targetFlowId
            if "targetFlowId" in obj:
                target_flow_id = obj.get("targetFlowId")
                target_page_id = obj.get("targetPageId")
                
                # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
                if target_flow_id and not target_page_id:
                    results.append({
                        "target_flow_id": target_flow_id,
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
    
    # 按 targetFlowId 分组，去重
    all_target_flow_ids: Set[str] = set()
    for info in all_flow_ids_info:
        all_target_flow_ids.add(info["target_flow_id"])
    
    print(f"找到 {len(all_target_flow_ids)} 个唯一的 targetFlowId（无 targetPageId）:")
    for flow_id in sorted(all_target_flow_ids):
        print(f"  - {flow_id}")
    
    # 2. 读取 nodes_config.json
    with open(nodes_config_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)
    
    nodes = config_data.get("nodes", [])
    
    # 3. 检查是否已经有 jump 节点
    existing_jump_nodes = {}
    for node in nodes:
        if node.get("type") == "jump":
            jump_flow_uuid = node.get("jump_flow_uuid", "")
            if jump_flow_uuid:
                existing_jump_nodes[jump_flow_uuid] = node
    
    print(f"\n已存在的 jump 节点: {len(existing_jump_nodes)}")
    for flow_id, jump_node in existing_jump_nodes.items():
        print(f"  - {jump_node.get('name')} -> {flow_id}")
    
    # 4. 为所有缺失的 targetFlowId 生成 jump 节点
    new_jump_nodes = []
    for flow_id in sorted(all_target_flow_ids):
        if flow_id not in existing_jump_nodes:
            # 生成唯一的节点名称
            jump_node_name = f"jump_to_flow_{flow_id[:8]}"
            
            # 确保节点名称唯一
            existing_names = {n.get("name") for n in nodes}
            counter = 0
            original_name = jump_node_name
            while jump_node_name in existing_names:
                counter += 1
                jump_node_name = f"{original_name}_{counter}"
            
            jump_node = {
                "type": "jump",
                "name": jump_node_name,
                "title": f"Jump to Flow {flow_id[:8]}...",
                "jump_type": "flow",
                "jump_robot_id": "",
                "jump_robot_name": "",
                "jump_carry_history_number": 5,
                "jump_flow_name": "",
                "jump_flow_uuid": flow_id,  # 使用 targetFlowId
                "jump_carry_userinput": True
            }
            
            new_jump_nodes.append(jump_node)
            print(f"  ✅ 生成新的 jump 节点: {jump_node_name} -> {flow_id}")
        else:
            print(f"  ⏭️  jump 节点已存在: {existing_jump_nodes[flow_id].get('name')} -> {flow_id}")
    
    # 5. 将新的 jump 节点添加到 nodes 列表
    if new_jump_nodes:
        # 将 jump 节点添加到 nodes 列表的末尾
        nodes.extend(new_jump_nodes)
        print(f"\n✅ 添加了 {len(new_jump_nodes)} 个新的 jump 节点")
    else:
        print(f"\n✅ 所有需要的 jump 节点都已存在")
    
    # 6. 保存更新后的配置
    config_data["nodes"] = nodes
    
    # 备份原文件
    backup_path = nodes_config_path + ".backup3"
    if not os.path.exists(backup_path):
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        print(f"📦 已创建备份文件: {backup_path}")
    
    # 保存更新后的文件
    with open(nodes_config_path, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
    
    print(f"💾 已更新文件: {nodes_config_path}")
    print(f"📊 总节点数: {len(nodes)} (其中 jump 节点: {len([n for n in nodes if n.get('type') == 'jump'])})")
    
    return len(new_jump_nodes)


if __name__ == "__main__":
    exported_flow_path = "exported_flow_TXNAndSTMT_Deeplink.json"
    nodes_config_path = "output/step2_workflow_config/nodes_config.json"
    
    if not os.path.exists(exported_flow_path):
        print(f"❌ 错误: 找不到文件 {exported_flow_path}")
        exit(1)
    
    if not os.path.exists(nodes_config_path):
        print(f"❌ 错误: 找不到文件 {nodes_config_path}")
        exit(1)
    
    try:
        count = add_missing_jump_nodes(exported_flow_path, nodes_config_path)
        print("\n" + "=" * 70)
        print(f"✅ 完成！添加了 {count} 个 jump 节点")
        print("=" * 70)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


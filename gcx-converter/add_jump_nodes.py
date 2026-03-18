"""
为 nodes_config.json 添加跳转节点
扫描所有条件节点，找到有 target_flow_id 且 target_page_id 为 null 的分支，
为每个唯一的 target_flow_id 生成一个 jump 节点
"""

import json
import os
from typing import Dict, List, Set


def add_jump_nodes_to_config(nodes_config_path: str):
    """
    为 nodes_config.json 添加跳转节点
    
    Args:
        nodes_config_path: nodes_config.json 文件路径
    """
    # 读取 nodes_config.json
    with open(nodes_config_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)
    
    nodes = config_data.get("nodes", [])
    
    # 1. 收集所有需要 jump 节点的 target_flow_id
    target_flow_ids: Set[str] = set()
    flow_id_to_info: Dict[str, Dict] = {}
    
    for node in nodes:
        if node.get("type") == "condition":
            if_else_conditions = node.get("if_else_conditions", [])
            for branch in if_else_conditions:
                target_flow_id = branch.get("target_flow_id")
                target_page_id = branch.get("target_page_id")
                
                # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
                if target_flow_id and not target_page_id:
                    if target_flow_id not in target_flow_ids:
                        target_flow_ids.add(target_flow_id)
                        # 保存一些信息用于生成节点名称
                        flow_id_to_info[target_flow_id] = {
                            "target_flow_id": target_flow_id,
                            "condition_id": branch.get("condition_id", ""),
                            "condition_name": branch.get("condition_name", "")
                        }
    
    print(f"找到 {len(target_flow_ids)} 个需要 jump 节点的 target_flow_id:")
    for flow_id in target_flow_ids:
        print(f"  - {flow_id[:8]}... ({flow_id})")
    
    # 2. 检查是否已经有 jump 节点
    existing_jump_nodes = {}
    for node in nodes:
        if node.get("type") == "jump":
            jump_flow_uuid = node.get("jump_flow_uuid", "")
            if jump_flow_uuid:
                existing_jump_nodes[jump_flow_uuid] = node
    
    # 3. 为每个 target_flow_id 生成 jump 节点（如果不存在）
    new_jump_nodes = []
    for flow_id in target_flow_ids:
        if flow_id not in existing_jump_nodes:
            # 生成唯一的节点名称
            flow_info = flow_id_to_info[flow_id]
            condition_id = flow_info.get("condition_id", "")
            
            # 使用 flow_id 的前8位和 condition_id 的一部分生成节点名
            if condition_id:
                # 从 condition_id 中提取一些字符
                name_suffix = condition_id.replace("_", "").replace("-", "")[:8] if condition_id else ""
                jump_node_name = f"jump_to_flow_{flow_id[:8]}_{name_suffix}"
            else:
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
    
    # 4. 将新的 jump 节点添加到 nodes 列表
    if new_jump_nodes:
        # 将 jump 节点添加到 nodes 列表的末尾（在条件节点之后）
        nodes.extend(new_jump_nodes)
        print(f"\n✅ 添加了 {len(new_jump_nodes)} 个新的 jump 节点")
    else:
        print(f"\n✅ 所有需要的 jump 节点都已存在")
    
    # 5. 保存更新后的配置
    config_data["nodes"] = nodes
    
    # 备份原文件
    backup_path = nodes_config_path + ".backup"
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
    # 默认路径
    nodes_config_path = "output/step2_workflow_config/nodes_config.json"
    
    if not os.path.exists(nodes_config_path):
        print(f"❌ 错误: 找不到文件 {nodes_config_path}")
        print("请确保文件路径正确")
        exit(1)
    
    print("=" * 70)
    print("为 nodes_config.json 添加跳转节点")
    print("=" * 70)
    print(f"文件路径: {nodes_config_path}\n")
    
    try:
        count = add_jump_nodes_to_config(nodes_config_path)
        print("\n" + "=" * 70)
        print(f"✅ 完成！添加了 {count} 个 jump 节点")
        print("=" * 70)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


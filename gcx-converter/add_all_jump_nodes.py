"""
为 nodes_config.json 添加所有跳转节点
从 exported_flow_TXNAndSTMT_Deeplink.json 中提取所有唯一的 targetFlowId，
然后检查 nodes_config.json 中是否有对应的 jump 节点，为缺失的添加
"""

import json
import os
import re
from typing import Dict, List, Set

from logger_config import get_logger, is_verbose
logger = get_logger(__name__)

VERBOSE = is_verbose()

if not VERBOSE:
    # 将脚本中的 print 降级为 DEBUG 日志，避免默认模式输出太多
    def print(*args, **kwargs):  # type: ignore[override]
        try:
            message = " ".join(str(a) for a in args)
        except Exception:
            message = " ".join(map(str, args))
        logger.debug(message)


def extract_all_target_flow_ids(exported_flow_path: str) -> Set[str]:
    """
    从 exported_flow 文件中提取所有唯一的 targetFlowId
    
    Args:
        exported_flow_path: exported_flow 文件路径
        
    Returns:
        所有唯一的 targetFlowId 集合
    """
    target_flow_ids = set()
    
    # 使用正则表达式提取所有 targetFlowId
    with open(exported_flow_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 匹配 "targetFlowId": "uuid" 格式
    pattern = r'"targetFlowId":\s*"([a-f0-9-]+)"'
    matches = re.findall(pattern, content)
    target_flow_ids.update(matches)
    
    return target_flow_ids


def add_jump_nodes_from_exported_flow(
    exported_flow_path: str,
    nodes_config_path: str
):
    """
    从 exported_flow 文件中提取所有 targetFlowId，并为 nodes_config.json 添加缺失的 jump 节点
    
    Args:
        exported_flow_path: exported_flow 文件路径
        nodes_config_path: nodes_config.json 文件路径
    """
    # 1. 从 exported_flow 文件中提取所有唯一的 targetFlowId
    print("=" * 70)
    print("从 exported_flow 文件中提取所有 targetFlowId")
    print("=" * 70)
    
    all_target_flow_ids = extract_all_target_flow_ids(exported_flow_path)
    print(f"\n找到 {len(all_target_flow_ids)} 个唯一的 targetFlowId:")
    for flow_id in sorted(all_target_flow_ids):
        print(f"  - {flow_id}")
    
    # 2. 读取 nodes_config.json
    with open(nodes_config_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)
    
    nodes = config_data.get("nodes", [])
    
    # 3. 收集 nodes_config.json 中所有需要 jump 节点的 target_flow_id
    #    包括条件节点中的 target_flow_id
    config_target_flow_ids: Set[str] = set()
    flow_id_to_info: Dict[str, Dict] = {}
    
    for node in nodes:
        if node.get("type") == "condition":
            if_else_conditions = node.get("if_else_conditions", [])
            for branch in if_else_conditions:
                target_flow_id = branch.get("target_flow_id")
                target_page_id = branch.get("target_page_id")
                
                # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
                if target_flow_id and not target_page_id:
                    if target_flow_id not in config_target_flow_ids:
                        config_target_flow_ids.add(target_flow_id)
                        flow_id_to_info[target_flow_id] = {
                            "target_flow_id": target_flow_id,
                            "condition_id": branch.get("condition_id", ""),
                            "condition_name": branch.get("condition_name", "")
                        }
    
    print(f"\n在 nodes_config.json 中找到 {len(config_target_flow_ids)} 个需要 jump 节点的 target_flow_id:")
    for flow_id in sorted(config_target_flow_ids):
        print(f"  - {flow_id}")
    
    # 4. 检查哪些 targetFlowId 在 exported_flow 中但不在 nodes_config 中
    missing_in_config = all_target_flow_ids - config_target_flow_ids
    if missing_in_config:
        print(f"\n⚠️  发现 {len(missing_in_config)} 个 targetFlowId 在 exported_flow 中但不在 nodes_config 中:")
        for flow_id in sorted(missing_in_config):
            print(f"  - {flow_id}")
        print("\n   这些 targetFlowId 可能在其他 workflow 文件中，或者没有被转换到 nodes_config.json")
    
    # 5. 检查是否已经有 jump 节点
    existing_jump_nodes = {}
    for node in nodes:
        if node.get("type") == "jump":
            jump_flow_uuid = node.get("jump_flow_uuid", "")
            if jump_flow_uuid:
                existing_jump_nodes[jump_flow_uuid] = node
    
    print(f"\n已存在的 jump 节点: {len(existing_jump_nodes)}")
    for flow_id, jump_node in existing_jump_nodes.items():
        print(f"  - {jump_node.get('name')} -> {flow_id}")
    
    # 6. 为 nodes_config.json 中需要的 target_flow_id 生成 jump 节点（如果不存在）
    new_jump_nodes = []
    for flow_id in config_target_flow_ids:
        if flow_id not in existing_jump_nodes:
            # 生成唯一的节点名称
            if flow_id in flow_id_to_info:
                flow_info = flow_id_to_info[flow_id]
                condition_id = flow_info.get("condition_id", "")
                
                # 使用 flow_id 的前8位和 condition_id 的一部分生成节点名
                if condition_id:
                    name_suffix = condition_id.replace("_", "").replace("-", "")[:8] if condition_id else ""
                    jump_node_name = f"jump_to_flow_{flow_id[:8]}_{name_suffix}"
                else:
                    jump_node_name = f"jump_to_flow_{flow_id[:8]}"
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
    
    # 7. 将新的 jump 节点添加到 nodes 列表
    if new_jump_nodes:
        # 将 jump 节点添加到 nodes 列表的末尾（在条件节点之后）
        nodes.extend(new_jump_nodes)
        print(f"\n✅ 添加了 {len(new_jump_nodes)} 个新的 jump 节点")
    else:
        print(f"\n✅ 所有需要的 jump 节点都已存在")
    
    # 8. 保存更新后的配置
    config_data["nodes"] = nodes
    
    # 备份原文件
    backup_path = nodes_config_path + ".backup2"
    if not os.path.exists(backup_path):
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        print(f"📦 已创建备份文件: {backup_path}")
    
    # 保存更新后的文件
    with open(nodes_config_path, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
    
    print(f"💾 已更新文件: {nodes_config_path}")
    print(f"📊 总节点数: {len(nodes)} (其中 jump 节点: {len([n for n in nodes if n.get('type') == 'jump'])})")
    
    # 9. 统计信息
    print("\n" + "=" * 70)
    print("统计信息")
    print("=" * 70)
    print(f"exported_flow 中的 targetFlowId 总数: {len(all_target_flow_ids)}")
    print(f"nodes_config 中需要的 targetFlowId 数: {len(config_target_flow_ids)}")
    print(f"已存在的 jump 节点数: {len(existing_jump_nodes)}")
    print(f"新添加的 jump 节点数: {len(new_jump_nodes)}")
    print(f"最终 jump 节点总数: {len([n for n in nodes if n.get('type') == 'jump'])})")
    
    return len(new_jump_nodes)


if __name__ == "__main__":
    # 默认路径
    exported_flow_path = "exported_flow_TXNAndSTMT_Deeplink.json"
    nodes_config_path = "output/step2_workflow_config/nodes_config.json"
    
    if not os.path.exists(exported_flow_path):
        print(f"❌ 错误: 找不到文件 {exported_flow_path}")
        print("请确保文件路径正确")
        exit(1)
    
    if not os.path.exists(nodes_config_path):
        print(f"❌ 错误: 找不到文件 {nodes_config_path}")
        print("请确保文件路径正确")
        exit(1)
    
    print("=" * 70)
    print("为 nodes_config.json 添加所有跳转节点")
    print("=" * 70)
    print(f"exported_flow 文件: {exported_flow_path}")
    print(f"nodes_config 文件: {nodes_config_path}\n")
    
    try:
        count = add_jump_nodes_from_exported_flow(exported_flow_path, nodes_config_path)
        print("\n" + "=" * 70)
        print(f"✅ 完成！添加了 {count} 个 jump 节点")
        print("=" * 70)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


"""
为 jump 节点添加边连接
扫描 nodes_config.json，找到所有条件分支中有 target_flow_id 且无 target_page_id 的情况，
然后在 edge_config.json 中添加从条件节点到 jump 节点的边
"""

import json
import os

from logger_config import get_logger, is_verbose
logger = get_logger(__name__)

VERBOSE = is_verbose()

if not VERBOSE:
    # 将脚本中的 print 统一视为 DEBUG 日志，正常运行时不刷屏
    def print(*args, **kwargs):  # type: ignore[override]
        try:
            message = " ".join(str(a) for a in args)
        except Exception:
            message = " ".join(map(str, args))
        logger.debug(message)

def add_jump_edges(nodes_config_path: str, edge_config_path: str):
    """
    为 jump 节点添加边连接
    
    Args:
        nodes_config_path: nodes_config.json 文件路径
        edge_config_path: edge_config.json 文件路径
    """
    # 读取 nodes_config.json
    with open(nodes_config_path, 'r', encoding='utf-8') as f:
        nodes_config = json.load(f)
    
    # 读取 edge_config.json
    with open(edge_config_path, 'r', encoding='utf-8') as f:
        edge_config = json.load(f)
    
    nodes = nodes_config.get("nodes", [])
    edges = edge_config.get("edges", [])
    
    # 1. 收集所有 jump 节点，按 jump_flow_uuid 索引
    jump_nodes_map = {}
    for node in nodes:
        if node.get("type") == "jump":
            jump_flow_uuid = node.get("jump_flow_uuid", "")
            if jump_flow_uuid:
                jump_nodes_map[jump_flow_uuid] = node
    
    print(f"找到 {len(jump_nodes_map)} 个 jump 节点")
    for flow_uuid, jump_node in jump_nodes_map.items():
        print(f"  - {jump_node.get('name')} -> {flow_uuid[:8]}...")
    
    # 2. 扫描所有条件节点，找到需要连接到 jump 节点的分支
    new_edges = []
    existing_edges = {(e.get("source_node"), e.get("target_node"), e.get("condition_id")) for e in edges}
    
    for node in nodes:
        if node.get("type") == "condition":
            condition_name = node.get("name")
            if_else_conditions = node.get("if_else_conditions", [])
            
            for branch in if_else_conditions:
                target_flow_id = branch.get("target_flow_id")
                target_page_id = branch.get("target_page_id")
                condition_id = branch.get("condition_id")
                
                # 只有当有 targetFlowId 且无 targetPageId 时才需要连接到 jump 节点
                if target_flow_id and not target_page_id:
                    # 查找对应的 jump 节点
                    jump_node = jump_nodes_map.get(target_flow_id)
                    if jump_node:
                        jump_node_name = jump_node.get("name")
                        
                        # 检查是否已经有这条边
                        edge_key = (condition_name, jump_node_name, condition_id)
                        if edge_key not in existing_edges:
                            # 检查是否需要经过 param_code 节点
                            transition_code_node = branch.get("transition_code_node")
                            
                            if transition_code_node:
                                # condition → transition_code → jump_node
                                # 先检查 transition_code → jump_node 的边是否存在
                                code_to_jump_key = (transition_code_node, jump_node_name, None)
                                if code_to_jump_key not in existing_edges:
                                    new_edges.append({
                                        "source_node": transition_code_node,
                                        "target_node": jump_node_name,
                                        "connection_type": "default"
                                    })
                                    print(f"  ✅ 添加边: {transition_code_node} → {jump_node_name}")
                            else:
                                # condition → jump_node
                                new_edges.append({
                                    "source_node": condition_name,
                                    "target_node": jump_node_name,
                                    "connection_type": "condition",
                                    "condition_id": condition_id
                                })
                                print(f"  ✅ 添加边: {condition_name} → {jump_node_name} (condition: {condition_id})")
                    else:
                        print(f"  ⚠️  警告: 找不到 jump 节点 (target_flow_id: {target_flow_id[:8]}...)")
    
    # 3. 添加新边到 edge_config.json
    if new_edges:
        edges.extend(new_edges)
        edge_config["edges"] = edges
        
        # 保存更新后的文件
        with open(edge_config_path, 'w', encoding='utf-8') as f:
            json.dump(edge_config, f, ensure_ascii=False, indent=2)
        
        print(f"\n✅ 成功添加 {len(new_edges)} 条边到 {edge_config_path}")
    else:
        print(f"\n✅ 没有需要添加的边")

if __name__ == "__main__":
    nodes_config_path = "output/step2_workflow_config/nodes_config_transactionservicing_downloadestatement.json"
    edge_config_path = "output/step2_workflow_config/edge_config_transactionservicing_downloadestatement.json"
    
    add_jump_edges(nodes_config_path, edge_config_path)


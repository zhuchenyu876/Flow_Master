"""
为 jump 节点添加边连接（从 page 到 jump 节点）
根据 exported_flow 中的信息：
1. 找到所有有 targetFlowId 的 page（如 JumpTo_Common_EAT_CATCHALL）
2. 找到这些 page 的最后一个节点
3. 在 edge_config.json 中添加从这些 page 的最后一个节点到 jump 节点的边
"""

import json
import os
from typing import Dict, List, Set, Tuple

def find_page_last_node(
    page_id: str,
    nodes: List[Dict],
    edges: List[Dict]
) -> str:
    """
    找到 page 的最后一个节点
    
    Args:
        page_id: page 的 ID（完整 UUID 或前8位）
        nodes: 所有节点列表
        edges: 所有边列表
        
    Returns:
        page 的最后一个节点名称，如果找不到则返回 None
    """
    page_prefix = page_id[:8] if len(page_id) > 8 else page_id
    
    # 方法1: 找到所有指向 page_xxx 的边，这些边的 source_node 就是指向这个 page 的节点
    # 但我们需要找到 page 内部的最后一个节点
    
    # 方法2: 找到所有包含 page_id 的节点（通过 transition_info 或其他方式）
    page_nodes = []
    for node in nodes:
        # 检查节点的 transition_info 中是否有 target_page_id
        transition_info = node.get("transition_info", {})
        target_page_id = transition_info.get("target_page_id")
        if target_page_id and target_page_id.startswith(page_prefix):
            page_nodes.append(node)
    
    if not page_nodes:
        # 如果找不到，尝试通过节点名称查找
        # page 的节点名称可能包含 page_id 的前8位
        for node in nodes:
            node_name = node.get("name", "")
            if page_prefix in node_name:
                page_nodes.append(node)
    
    if not page_nodes:
        # 如果还是找不到，返回 page_xxx 格式的占位符
        return f"page_{page_prefix}"
    
    # 找到 page 的最后一个节点
    # 最后一个节点应该是没有出边（除了指向其他 page 或 jump 节点）的节点
    # 或者是在 edges 中作为 source_node 出现次数最多的节点
    
    # 统计每个节点作为 source_node 的次数
    source_counts = {}
    for edge in edges:
        source = edge.get("source_node", "")
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
    
    # 找到 page 节点中作为 source_node 次数最多的节点（通常是最后一个节点）
    last_node = None
    max_count = -1
    for node in page_nodes:
        node_name = node.get("name", "")
        count = source_counts.get(node_name, 0)
        if count > max_count:
            max_count = count
            last_node = node_name
    
    if last_node:
        return last_node
    
    # 如果找不到，返回 page 的第一个节点（entry node）
    if page_nodes:
        return page_nodes[0].get("name", f"page_{page_prefix}")
    
    return f"page_{page_prefix}"

def add_jump_edges_from_pages(
    exported_flow_path: str,
    nodes_config_path: str,
    edge_config_path: str
):
    """
    为 jump 节点添加边连接（从 page 到 jump 节点）
    
    Args:
        exported_flow_path: exported_flow 文件路径
        nodes_config_path: nodes_config.json 文件路径
        edge_config_path: edge_config.json 文件路径
    """
    print("=" * 70)
    print("为 jump 节点添加边连接（从 page 到 jump 节点）")
    print("=" * 70)
    
    # 读取 exported_flow
    with open(exported_flow_path, 'r', encoding='utf-8') as f:
        flow_data = json.load(f)
    
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
    
    print(f"\n找到 {len(jump_nodes_map)} 个 jump 节点")
    for flow_uuid, jump_node in jump_nodes_map.items():
        print(f"  - {jump_node.get('name')} -> {flow_uuid[:8]}...")
    
    # 2. 从 exported_flow 中找到所有有 targetFlowId 的 page
    # 这些 page 的 transitionEvents 中有 targetFlowId
    page_to_flow_id = {}  # page_id -> targetFlowId
    
    # pages 在 flow.pages 中
    flow_obj = flow_data.get("flow", {})
    pages = flow_obj.get("pages", [])
    
    for page_item in pages:
        page_key = page_item.get("key", "")
        page_value = page_item.get("value", {})
        transition_events = page_value.get("transitionEvents", [])
        
        for event in transition_events:
            handler = event.get("transitionEventHandler", {})
            target_flow_id = handler.get("targetFlowId")
            target_page_id = handler.get("targetPageId")
            
            # 只有当有 targetFlowId 且无 targetPageId 时才需要 jump 节点
            if target_flow_id and not target_page_id:
                page_to_flow_id[page_key] = target_flow_id
                page_name = page_value.get("displayName", page_key[:8])
                print(f"  找到 page {page_name} ({page_key[:8]}...) 有 targetFlowId: {target_flow_id[:8]}...")
    
    print(f"\n找到 {len(page_to_flow_id)} 个有 targetFlowId 的 page")
    
    # 3. 在 edge_config.json 中添加从这些 page 的最后一个节点到 jump 节点的边
    new_edges = []
    existing_edges = {(e.get("source_node"), e.get("target_node"), e.get("condition_id")) for e in edges}
    
    for page_id, target_flow_id in page_to_flow_id.items():
        jump_node = jump_nodes_map.get(target_flow_id)
        if not jump_node:
            print(f"  ⚠️  警告: 找不到 jump 节点 (target_flow_id: {target_flow_id[:8]}...)")
            continue
        
        jump_node_name = jump_node.get("name")
        page_prefix = page_id[:8]
        
        # 找到 page 的最后一个节点
        # 方法1: 查找所有指向 page_xxx 的边，找到这些边的 source_node
        # 然后找到这些 source_node 中最后一个节点（没有出边的节点）
        
        # 方法2: 直接查找 page_xxx 在 edges 中作为 target_node 的情况
        # 然后找到这个 page 的最后一个节点
        
        # 查找所有指向 page_xxx 的边
        page_entry_nodes = set()
        for edge in edges:
            target = edge.get("target_node", "")
            if target == f"page_{page_prefix}" or page_prefix in target:
                source = edge.get("source_node", "")
                if source:
                    page_entry_nodes.add(source)
        
        if not page_entry_nodes:
            # 如果找不到，尝试使用 page_xxx 作为 entry node
            page_entry_nodes.add(f"page_{page_prefix}")
        
        # 找到 page 的最后一个节点
        # 从 entry nodes 开始，找到没有出边（除了指向其他 page 或 jump 节点）的节点
        page_last_node = None
        
        # 方法：找到所有从 entry nodes 可达的节点，然后找到最后一个节点
        # 简单方法：找到所有包含 page_prefix 的节点，然后找到最后一个节点
        page_nodes = []
        for node in nodes:
            node_name = node.get("name", "")
            if page_prefix in node_name:
                page_nodes.append(node_name)
        
        if page_nodes:
            # 找到 page 节点中作为 source_node 出现次数最多的节点（通常是最后一个节点）
            source_counts = {}
            for edge in edges:
                source = edge.get("source_node", "")
                if source in page_nodes:
                    source_counts[source] = source_counts.get(source, 0) + 1
            
            if source_counts:
                # 找到作为 source_node 次数最多的节点
                page_last_node = max(source_counts.items(), key=lambda x: x[1])[0]
            else:
                # 如果找不到，使用最后一个 page 节点
                page_last_node = page_nodes[-1]
        else:
            # 如果找不到 page 节点，使用 page_xxx 作为占位符
            page_last_node = f"page_{page_prefix}"
        
        # 添加从 page 的最后一个节点到 jump 节点的边
        edge_key = (page_last_node, jump_node_name, None)
        if edge_key not in existing_edges:
            new_edges.append({
                "source_node": page_last_node,
                "target_node": jump_node_name,
                "connection_type": "default"
            })
            print(f"  ✅ 添加边: {page_last_node} → {jump_node_name}")
        else:
            print(f"  ⏭️  边已存在: {page_last_node} → {jump_node_name}")
    
    # 4. 添加新边到 edge_config.json
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
    exported_flow_path = "exported_flow_TXNAndSTMT_Deeplink.json"
    nodes_config_path = "output/step2_workflow_config/nodes_config_transactionservicing_downloadestatement.json"
    edge_config_path = "output/step2_workflow_config/edge_config_transactionservicing_downloadestatement.json"
    
    add_jump_edges_from_pages(exported_flow_path, nodes_config_path, edge_config_path)

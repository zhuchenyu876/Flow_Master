"""
Step 7: 清理孤立节点 + 布局优化
================================
功能：
1. 检查并删除 workflow JSON 文件中没有被任何边连接的孤立节点
2. 优化工作流布局，使节点位置更加美观

孤立节点定义：
- 既不是任何 edge 的 source
- 也不是任何 edge 的 target

注意：
- start 节点如果没有出边也算孤立（除非它是唯一的节点）
- 会同时删除相关的 block 节点（如果 block 节点对应的功能节点被删除）

布局优化：
- 使用 dagre 库（通过 Node.js）来优化节点位置
- 支持 LR (从左到右) 和 TB (从上到下) 两种布局方向

作者：chenyu.zhu
日期：2025-12-17
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Set

from logger_config import get_logger, is_verbose
logger = get_logger(__name__)

# 控制详细输出（大量 per-file / per-node 文本）的开关
VERBOSE = is_verbose()

# 在非 VERBOSE 模式下，将所有裸露的 print 降级为 DEBUG 日志，避免刷屏
if not VERBOSE:
    def print(*args, **kwargs):  # type: ignore[override]
        try:
            message = " ".join(str(a) for a in args)
        except Exception:
            message = " ".join(map(str, args))
        logger.debug(message)


def remove_edges_to_jump_nodes_without_incoming(workflow_data: Dict, dry_run: bool = False) -> int:
    """
    删除没有入边且连接到 jump 节点的边
    
    规则：
    - 如果一个节点的下一个节点连到一个 jump 节点
    - 并且该节点没有入边（即没有任何一个节点连接到该节点）
    - 需要删除该节点与连接 jump 节点的边
    
    特殊规则：如果 block 节点只连接到 jump 节点，且没有入边，删除该 block 和它包含的所有节点
    
    Args:
        workflow_data: workflow JSON 数据
        dry_run: 如果为 True，只检查不删除
        
    Returns:
        删除的边数量
    """
    nodes = workflow_data.get("nodes", [])
    edges = workflow_data.get("edges", [])
    
    # 构建节点 ID 到节点类型的映射
    node_id_to_type = {}
    node_id_to_node = {}
    for node in nodes:
        node_id = node.get("id")
        if node_id:
            node_id_to_type[node_id] = node.get("type", "unknown")
            node_id_to_node[node_id] = node
    
    # 收集所有 jump 节点的 ID
    jump_node_ids = set()
    for node_id, node_type in node_id_to_type.items():
        if node_type == "jump":
            jump_node_ids.add(node_id)
    
    if not jump_node_ids:
        return 0
    
    # 构建入边映射：target -> [source1, source2, ...]
    incoming_edges = {}
    # 构建出边映射：source -> [target1, target2, ...]
    outgoing_edges = {}
    for edge in edges:
        target = edge.get("target")
        source = edge.get("source")
        if target and source:
            if target not in incoming_edges:
                incoming_edges[target] = []
            incoming_edges[target].append(source)
            
            if source not in outgoing_edges:
                outgoing_edges[source] = []
            outgoing_edges[source].append(target)
    
    # 找出需要删除的边
    edges_to_remove = []
    blocks_to_remove = set()  # 需要删除的 block 节点 ID
    
    # 1. 检查普通节点：source 节点没有入边，且 target 是 jump 节点
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        
        if source and target:
            # 检查 target 是否是 jump 节点
            if target in jump_node_ids:
                # 检查 source 节点是否有入边
                if source not in incoming_edges or len(incoming_edges[source]) == 0:
                    edges_to_remove.append(edge)
    
    # 2. 检查 block 节点：如果 block 只连接到 jump 节点，且没有入边
    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        
        if node_type == "block":
            # 检查这个 block 是否有入边
            has_incoming = node_id in incoming_edges and len(incoming_edges[node_id]) > 0
            
            # 检查这个 block 的出边是否都连接到 jump 节点
            # 注意：边的 targetHandle 可能指向 jump 节点
            if node_id in outgoing_edges:
                all_targets_are_jump = True
                block_edges = [e for e in edges if e.get("source") == node_id]
                
                if len(block_edges) > 0:
                    for edge in block_edges:
                        target = edge.get("target")
                        target_handle = edge.get("targetHandle")
                        
                        # 检查 targetHandle 是否指向 jump 节点
                        if target_handle and target_handle in jump_node_ids:
                            continue
                        # 检查 target 是否是 jump 节点
                        elif target and target in jump_node_ids:
                            continue
                        # 检查 target 节点的类型是否是 jump
                        elif target and node_id_to_type.get(target) == "jump":
                            continue
                        else:
                            all_targets_are_jump = False
                            break
                else:
                    all_targets_are_jump = False
                
                # 如果 block 没有入边，且所有出边都连接到 jump 节点，标记删除
                if not has_incoming and all_targets_are_jump and len(block_edges) > 0:
                    blocks_to_remove.add(node_id)
                    # 同时删除这个 block 的所有出边
                    for edge in block_edges:
                        if edge not in edges_to_remove:
                            edges_to_remove.append(edge)
    
    # 删除边（仅在非 dry_run 模式下实际删除）
    removed_count = len(edges_to_remove)
    
    if removed_count > 0 or len(blocks_to_remove) > 0:
        action = "发现" if dry_run else "删除"
        if removed_count > 0:
            print(f"   🗑️  {action} {removed_count} 条连接到 jump 节点的边（源节点无入边）")
            for edge in edges_to_remove:
                source_id = edge.get("source", "")[:8] + "..."
                target_id = edge.get("target", "")[:8] + "..."
                print(f"      - {source_id} -> {target_id}")
        
        if len(blocks_to_remove) > 0:
            print(f"   🗑️  {action} {len(blocks_to_remove)} 个只连接到 jump 节点的 block（无入边）")
            for block_id in blocks_to_remove:
                block_node = node_id_to_node.get(block_id, {})
                block_label = block_node.get("data", {}).get("label", "无标签")
                include_ids = block_node.get("data", {}).get("include_node_ids", [])
                print(f"      - Block: {block_id[:8]}... ({block_label}), 包含 {len(include_ids)} 个节点")
        
        # 仅在非 dry_run 模式下实际删除边和 block
        if not dry_run:
            workflow_data["edges"] = [e for e in edges if e not in edges_to_remove]
            # 删除 block 节点及其包含的节点
            if blocks_to_remove:
                nodes_to_remove = set(blocks_to_remove)
                # 添加 block 包含的所有节点
                for block_id in blocks_to_remove:
                    block_node = node_id_to_node.get(block_id, {})
                    include_ids = block_node.get("data", {}).get("include_node_ids", [])
                    nodes_to_remove.update(include_ids)
                
                workflow_data["nodes"] = [
                    n for n in nodes 
                    if n.get("id") not in nodes_to_remove
                ]
        
        return removed_count
    
    return 0

# write by senlin.deng 2026-01-07
# 为解决替换了semantic judgement version后，意图判断条件节点还存在的问题
def remove_edges_from_condition_nodes_without_incoming(workflow_data: Dict, dry_run: bool = False) -> int:
    """
    删除没有入边且title以"Check if Intent"开头的条件节点的所有出边
    
    规则：
    - 找到所有 type 为 "condition" 的节点
    - 检查节点的 title 是否以 "Check if Intent" 开头
    - 检查该节点及其 block 节点（如果有）是否有入边
    - 如果条件节点和 block 节点都没有入边，删除 block 节点的所有出边
    - 注意：条件节点的出边实际上是从 block 节点发出的（source 是 block 节点ID）
    
    Args:
        workflow_data: workflow JSON 数据
        dry_run: 如果为 True，只检查不删除
        
    Returns:
        删除的边数量
    """
    nodes = workflow_data.get("nodes", [])
    edges = workflow_data.get("edges", [])
    
    # 构建节点ID到节点的映射（用于查找block节点）
    node_id_to_node = {}
    for node in nodes:
        node_id = node.get("id")
        if node_id:
            node_id_to_node[node_id] = node
    
    # 构建入边映射：target -> [source1, source2, ...]
    incoming_edges = {}
    for edge in edges:
        target = edge.get("target")
        source = edge.get("source")
        if target and source:
            if target not in incoming_edges:
                incoming_edges[target] = []
            incoming_edges[target].append(source)
    
    # 找出需要删除的边
    edges_to_remove = []
    condition_nodes_to_process = []
    block_nodes_to_process = []  # 新增：记录需要处理的 block 节点
    
    # 1. 找到所有符合条件的条件节点和 block 节点
    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        
        # 1.1 处理 condition 类型节点（检查 config.title）
        if node_type == "condition":
            # 检查 title 是否以 "Check if Intent" 或 "Intent Routing" 开头
            config = node.get("config", {})
            title = config.get("title", "")
            
            if title.startswith("Check if Intent") or title.startswith("Intent Routing"):
                # write by senlin.deng 2026-01-15
                # 解决意图判断条件节点还存在的问题
                # 强制删除所有 "Check if Intent" 或 "Intent Routing" 开头的条件节点
                condition_has_incoming = False
                block_has_incoming = False
                
                # 获取 block_id（条件节点可能关联 block 节点）
                block_id = node.get("blockId")
                
                # 如果条件节点和 block 节点都没有入边，则需要删除出边
                if not condition_has_incoming and not block_has_incoming:
                    condition_nodes_to_process.append({
                        "id": node_id,
                        "block_id": block_id,
                        "title": title,
                        "label": node.get("data", {}).get("label", "")
                    })
        
        # 1.2 处理 block 类型节点（检查 data.label）
        elif node_type == "block":
            # 检查 label 是否以 "Check if Intent" 或 "Intent Routing" 开头
            data = node.get("data", {})
            label = data.get("label", "")
            
            if label.startswith("Check if Intent") or label.startswith("Intent Routing"):
                # 强制标记为需要删除出边
                block_nodes_to_process.append({
                    "id": node_id,
                    "label": label
                })
    
    # 2. 找到这些条件节点对应的 block 节点的所有出边并标记删除
    # 注意：出边是从 block 节点发出的，不是从条件节点本身发出的
    # 如果条件节点没有 blockId，则删除条件节点本身的出边
    block_ids_to_remove_edges = set()
    condition_ids_to_remove_edges = set()
    
    for condition_node in condition_nodes_to_process:
        block_id = condition_node.get("block_id")
        if block_id:
            block_ids_to_remove_edges.add(block_id)
        else:
            # 如果没有 blockId，则删除条件节点本身的出边
            condition_ids_to_remove_edges.add(condition_node["id"])
    
    # 2.2 新增：将 block 类型节点的 ID 也加入删除列表
    for block_node in block_nodes_to_process:
        block_ids_to_remove_edges.add(block_node["id"])
    
    for edge in edges:
        source = edge.get("source")
        if source:
            if source in block_ids_to_remove_edges:
                edges_to_remove.append(edge)
            elif source in condition_ids_to_remove_edges:
                edges_to_remove.append(edge)
    
    # 删除边（仅在非 dry_run 模式下实际删除）
    removed_count = len(edges_to_remove)
    total_nodes_count = len(condition_nodes_to_process) + len(block_nodes_to_process)
    
    if removed_count > 0 or total_nodes_count > 0:
        action = "发现" if dry_run else "删除"
        print(f"   🗑️  {action} {removed_count} 条来自条件/block节点的边（title/label以'Check if Intent'开头）")
        
        # 打印 condition 节点信息
        for condition_node in condition_nodes_to_process:
            block_id = condition_node.get("block_id")
            if block_id:
                node_edges_count = sum(1 for e in edges_to_remove if e.get("source") == block_id)
                print(f"      - 条件节点 {condition_node['id'][:8]}... (block: {block_id[:8]}..., title: {condition_node['title']}): {node_edges_count} 条出边")
            else:
                node_edges_count = sum(1 for e in edges_to_remove if e.get("source") == condition_node["id"])
                print(f"      - 条件节点 {condition_node['id'][:8]}... (title: {condition_node['title']}): {node_edges_count} 条出边")
        
        # 打印 block 节点信息
        for block_node in block_nodes_to_process:
            node_edges_count = sum(1 for e in edges_to_remove if e.get("source") == block_node["id"])
            print(f"      - Block节点 {block_node['id'][:8]}... (label: {block_node['label']}): {node_edges_count} 条出边")
        
        # 仅在非 dry_run 模式下实际删除边
        if not dry_run:
            workflow_data["edges"] = [e for e in edges if e not in edges_to_remove]
        
        return removed_count
    
    return 0


# write by senlin.deng 2026-01-18
# 删除没有入边且title为"Fallback Message"的textReply节点
def remove_isolated_fallback_message_nodes(workflow_data: Dict, dry_run: bool = False) -> int:
    """
    删除没有入边且 title 为 "Fallback Message" 的 textReply 节点
    
    规则：
    - 找到所有 type 为 "textReply" 的节点
    - 检查节点的 config.title 是否为 "Fallback Message"
    - 检查该节点是否没有入边（既没有直接入边，也没有通过 block 节点的入边）
    - 如果满足条件，删除该节点以及相关的 block 节点（如果 block 节点的所有包含节点都被删除）
    
    Args:
        workflow_data: workflow JSON 数据
        dry_run: 如果为 True，只检查不删除
        
    Returns:
        删除的节点数量
    """
    nodes = workflow_data.get("nodes", [])
    edges = workflow_data.get("edges", [])
    
    # 构建节点ID到节点的映射
    node_id_to_node = {}
    for node in nodes:
        node_id = node.get("id")
        if node_id:
            node_id_to_node[node_id] = node
    
    # 构建入边映射：target -> [source1, source2, ...]
    incoming_edges = {}
    for edge in edges:
        target = edge.get("target")
        source = edge.get("source")
        if target and source:
            if target not in incoming_edges:
                incoming_edges[target] = []
            incoming_edges[target].append(source)
    
    # 找出需要删除的节点
    nodes_to_remove = set()
    fallback_nodes_info = []  # 用于日志输出
    
    # 1. 找到所有符合条件的 textReply 节点（title 为 "Fallback Message" 且没有入边）
    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        
        if node_type == "textReply":
            # 检查 config.title 是否为 "Fallback Message"
            config = node.get("config", {})
            title = config.get("title", "")
            
            if title == "Fallback Message":
                # 检查节点是否没有入边
                node_has_incoming = node_id in incoming_edges and len(incoming_edges[node_id]) > 0
                
                # 也检查对应的 block 节点是否有入边
                block_id = node.get("blockId")
                block_has_incoming = False
                if block_id:
                    block_has_incoming = block_id in incoming_edges and len(incoming_edges[block_id]) > 0
                
                # 如果节点和 block 节点都没有入边，标记删除
                if not node_has_incoming and not block_has_incoming:
                    nodes_to_remove.add(node_id)
                    fallback_nodes_info.append({
                        "id": node_id,
                        "block_id": block_id,
                        "title": title
                    })
    
    # 2. 找出需要删除的相关 block 节点
    blocks_to_remove = set()
    for node_info in fallback_nodes_info:
        block_id = node_info.get("block_id")
        if block_id:
            block_node = node_id_to_node.get(block_id)
            if block_node:
                # 检查这个 block 节点的所有 include_node_ids 是否都被删除
                include_node_ids = block_node.get("data", {}).get("include_node_ids", [])
                if include_node_ids:
                    # 如果所有包含的节点都被删除，则删除这个 block
                    all_included_removed = all(nid in nodes_to_remove for nid in include_node_ids)
                    if all_included_removed:
                        blocks_to_remove.add(block_id)
    
    # 3. 同时删除从这些节点发出的边
    edges_to_remove = []
    source_ids = nodes_to_remove | blocks_to_remove
    for edge in edges:
        source = edge.get("source")
        if source and source in source_ids:
            edges_to_remove.append(edge)
    
    # 删除节点（仅在非 dry_run 模式下实际删除）
    removed_count = len(nodes_to_remove) + len(blocks_to_remove)
    
    if removed_count > 0:
        action = "发现" if dry_run else "删除"
        print(f"   🗑️  {action} {len(nodes_to_remove)} 个孤立的 Fallback Message 节点（无入边）")
        
        for node_info in fallback_nodes_info:
            block_id = node_info.get("block_id")
            if block_id:
                print(f"      - textReply节点 {node_info['id'][:8]}... (block: {block_id[:8]}..., title: {node_info['title']})")
            else:
                print(f"      - textReply节点 {node_info['id'][:8]}... (title: {node_info['title']})")
        
        if blocks_to_remove:
            print(f"   🗑️  {action} {len(blocks_to_remove)} 个相关的 block 节点")
            for block_id in blocks_to_remove:
                block_node = node_id_to_node.get(block_id, {})
                block_label = block_node.get("data", {}).get("label", "无标签")
                print(f"      - Block: {block_id[:8]}... ({block_label})")
        
        if edges_to_remove:
            print(f"   🗑️  {action} {len(edges_to_remove)} 条相关的边")
        
        # 仅在非 dry_run 模式下实际删除
        if not dry_run:
            # 删除节点
            all_nodes_to_remove = nodes_to_remove | blocks_to_remove
            workflow_data["nodes"] = [
                n for n in nodes 
                if n.get("id") not in all_nodes_to_remove
            ]
            # 删除边
            workflow_data["edges"] = [e for e in edges if e not in edges_to_remove]
        
        return removed_count
    
    return 0


def find_isolated_nodes(workflow_data: Dict) -> Tuple[Set[str], Dict[str, Dict]]:
    """
    找出没有被任何 edge 连接的节点
    
    Args:
        workflow_data: workflow JSON 数据
        
    Returns:
        (isolated_node_ids, node_info): 孤立节点的 ID 集合和节点信息字典
    """
    nodes = workflow_data.get("nodes", [])
    edges = workflow_data.get("edges", [])
    
    # 收集所有在 edges 中被引用的节点 ID
    source_nodes = set()
    target_nodes = set()
    
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source:
            source_nodes.add(source)
        if target:
            target_nodes.add(target)
    
    # 收集所有连接的节点（在 edges 中的节点）
    connected_node_ids = source_nodes | target_nodes
    
    # 收集所有节点的 ID 和相关信息
    all_node_ids = set()
    node_info = {}
    block_id_to_node_id = {}  # blockId -> 功能节点 ID
    node_id_to_block_id = {}  # 节点 ID -> blockId
    block_include_nodes = {}  # block 节点 ID -> [包含的功能节点 ID]
    
    for node in nodes:
        node_id = node.get("id")
        if node_id:
            all_node_ids.add(node_id)
            node_info[node_id] = {
                'type': node.get('type', 'unknown'),
                'label': node.get('data', {}).get('label', ''),
                'blockId': node.get('blockId'),
                'node': node  # 保存完整节点信息
            }
            
            # 记录 blockId 关系
            block_id = node.get('blockId')
            if block_id:
                node_id_to_block_id[node_id] = block_id
                if block_id not in block_id_to_node_id:
                    block_id_to_node_id[block_id] = []
                block_id_to_node_id[block_id].append(node_id)
            
            # 记录 block 节点的 include_node_ids
            if node.get('type') == 'block':
                include_node_ids = node.get('data', {}).get('include_node_ids', [])
                if include_node_ids:
                    block_include_nodes[node_id] = include_node_ids
    
    # 找出真正孤立的节点
    # 规则：
    # 1. 如果节点在 edges 中（作为 source 或 target），不是孤立的
    # 2. 如果节点有 blockId，且这个 blockId 对应的 block 节点在 edges 中，不是孤立的
    # 3. 如果节点在某个 block 节点的 include_node_ids 中，且这个 block 节点在 edges 中，不是孤立的
    # 4. start 节点应该始终保留（即使没有连接）
    
    # 收集所有应该保留的节点
    nodes_to_keep = set()
    
    # 1. 直接连接的节点
    nodes_to_keep.update(connected_node_ids)
    
    # 2. 通过 blockId 关联的节点（功能节点 -> block 节点 -> edges）
    for node_id, block_id in node_id_to_block_id.items():
        if block_id in connected_node_ids:
            nodes_to_keep.add(node_id)
    
    # 3. 通过 include_node_ids 关联的节点（功能节点 -> block 节点 -> edges）
    for block_node_id, include_ids in block_include_nodes.items():
        if block_node_id in connected_node_ids:
            nodes_to_keep.update(include_ids)
    
    # 4. start 节点始终保留
    for node_id in all_node_ids:
        node = node_info.get(node_id, {}).get('node', {})
        if node.get('type') == 'start':
            nodes_to_keep.add(node_id)
    
    # 找出真正孤立的节点
    isolated_node_ids = all_node_ids - nodes_to_keep
    
    return isolated_node_ids, node_info


def clean_isolated_nodes(file_path: str, dry_run: bool = False, output_path: str = None, remove_condition_edges: bool = False) -> Tuple[int, List[Dict]]:
    """
    清理 JSON 文件中的孤立节点
    
    Args:
        file_path: JSON 文件路径
        dry_run: 如果为 True，只检查不删除
        
    Returns:
        (removed_count, removed_nodes): 删除的节点数量和节点信息
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            workflow_data = json.load(f)
        
        # 在清除孤立节点之前，先删除没有入边且连接到 jump 节点的边
        removed_edges_count = remove_edges_to_jump_nodes_without_incoming(workflow_data, dry_run=dry_run)
        
        # write by senlin.deng 2026-01-07
        # 删除没有入边且title以"Check if Intent"开头的条件节点的所有出边
        # 为解决替换了semantic judgement version后，意图判断条件节点还存在的问题
        removed_condition_edges_count = 0
        if remove_condition_edges:
            removed_condition_edges_count = remove_edges_from_condition_nodes_without_incoming(workflow_data, dry_run=dry_run)
        
        # write by senlin.deng 2026-01-18
        # 删除没有入边且title为"Fallback Message"的textReply节点
        # 作为兜底删除，清理孤立的 Fallback Message 节点
        removed_fallback_nodes_count = 0
        removed_fallback_nodes_count = remove_isolated_fallback_message_nodes(workflow_data, dry_run=dry_run)
        
        isolated_node_ids, node_info = find_isolated_nodes(workflow_data)
        
        # 如果没有孤立节点，但删除了边或节点，也需要保存文件
        if not isolated_node_ids:
            # 如果删除了边或 fallback 节点，需要保存文件
            if (removed_edges_count > 0 or removed_condition_edges_count > 0 or removed_fallback_nodes_count > 0) and not dry_run:
                # 确定保存路径
                if output_path:
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    save_path = output_path
                else:
                    save_path = file_path
                
                # 保存文件
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump(workflow_data, f, indent=2, ensure_ascii=False)
            return 0, []
        
        # 记录要删除的节点信息
        removed_nodes = []
        for node_id in isolated_node_ids:
            info = node_info.get(node_id, {})
            removed_nodes.append({
                "id": node_id,
                "type": info.get('type', 'unknown'),
                "label": info.get('label', ''),
                "blockId": info.get('blockId')
            })
        
        if dry_run:
            return len(removed_nodes), removed_nodes
        
        # 删除孤立节点
        nodes = workflow_data.get("nodes", [])
        nodes_to_remove = set(isolated_node_ids)
        
        # 同时需要检查并删除相关的 block 节点
        # 如果节点有 blockId，需要检查对应的 block 节点是否也被孤立
        block_ids_to_remove = set()
        for node in nodes:
            if node.get("id") in isolated_node_ids:
                block_id = node.get("blockId")
                if block_id:
                    block_ids_to_remove.add(block_id)
        
        # 如果 block 节点对应的功能节点被删除，也删除 block 节点
        for node in nodes:
            node_id = node.get("id")
            if node_id in block_ids_to_remove:
                # 检查这个 block 节点是否包含被删除的节点
                include_node_ids = node.get("data", {}).get("include_node_ids", [])
                if include_node_ids:
                    # 如果 include_node_ids 中的所有节点都被删除，则删除这个 block
                    if all(nid in nodes_to_remove for nid in include_node_ids):
                        nodes_to_remove.add(node_id)
                        removed_nodes.append({
                            "id": node_id,
                            "type": "block",
                            "label": node.get("data", {}).get("label", ""),
                            "blockId": None,
                            "reason": "related block node"
                        })
        
        # 删除所有标记的节点
        workflow_data["nodes"] = [
            node for node in nodes 
            if node.get("id") not in nodes_to_remove
        ]
        
        # 保存清理后的文件
        if output_path:
            # 如果指定了输出路径，保存到新位置
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            save_path = output_path
        else:
            # 否则覆盖原文件
            save_path = file_path
        
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(workflow_data, f, indent=2, ensure_ascii=False)
        
        return len(removed_nodes), removed_nodes
        
    except Exception as e:
        logger.error(f"❌ 处理文件 {file_path} 时出错: {str(e)}", exc_info=True)
        return 0, []


def optimize_layout(input_dir: str, output_dir: str = None, direction: str = 'LR'):
    """
    优化工作流布局（调用布局工具）
    
    Args:
        input_dir: 输入目录路径
        output_dir: 输出目录路径（如果为 None，则覆盖原文件）
        direction: 布局方向，'LR' (从左到右) 或 'TB' (从上到下)
    """
    # 导入布局工具（从布局工具文件夹）
    layout_tool_path = os.path.join(os.path.dirname(__file__), 'layout_tools', 'step7_layout_optimizer.py')
    
    if not os.path.exists(layout_tool_path):
        logger.warning(f"  ⚠️  布局工具未找到: {layout_tool_path}")
        logger.debug(f"  💡 跳过布局优化步骤")
        return
    
    try:
        # 动态导入布局工具模块
        import importlib.util
        spec = importlib.util.spec_from_file_location("layout_tools.step7_layout_optimizer", layout_tool_path)
        layout_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(layout_module)
        
        logger.info(f"\n{'='*60}")
        logger.info("🎨 优化工作流布局")
        logger.info(f"{'='*60}")
        logger.info(f"布局方向: {direction}")
        logger.info(f"输入目录: {input_dir}")
        if output_dir:
            logger.info(f"输出目录: {output_dir}")
        else:
            logger.info(f"输出目录: {input_dir} (覆盖原文件)")
        
        if hasattr(layout_module, 'process_all_workflows'):
            layout_module.process_all_workflows(input_dir, output_dir, direction)
            logger.info(f"✅ 布局优化完成")
        else:
            logger.warning(f"  ⚠️  布局工具模块缺少 process_all_workflows 函数")
            
    except Exception as e:
        logger.warning(f"  ⚠️  布局优化失败: {str(e)}")
        logger.debug(f"  💡 跳过布局优化步骤，继续执行")
        import traceback
        logger.error(traceback.format_exc())


def main(dry_run: bool = False, input_dir: str = None, output_dir: str = None, optimize: bool = True, layout_direction: str = 'LR', remove_condition_edges: bool = False):
    """
    主函数：清理所有 workflow JSON 文件中的孤立节点，并优化布局
    
    Args:
        dry_run: 如果为 True，只检查不删除
        input_dir: 输入目录，如果为 None 则使用默认目录
        output_dir: 输出目录，如果为 None 则覆盖原文件
        optimize: 是否在清理后优化布局（默认 True）
        layout_direction: 布局方向，'LR' (从左到右) 或 'TB' (从上到下)，默认 'LR'
    """
    if input_dir is None:
        # 默认检查 step6_final 目录
        input_path = Path("output/step6_final")
    else:
        input_path = Path(input_dir)
    
    if not input_path.exists():
        logger.error(f"Step 7: 目录不存在: {input_path}")
        return
    
    # 如果指定了输出目录，创建它
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
    else:
        output_path = None
    
    # 获取所有 JSON 文件
    json_files = list(input_path.glob("generated_workflow_*.json"))
    
    if not json_files:
        logger.warning(f"Step 7: 在 {input_path} 中未找到 generated_workflow_*.json 文件，跳过清理")
        return
    
    logger.info(f'Step 7: 清理孤立节点 - 找到 {len(json_files)} 个 workflow 文件')
    
    total_removed = 0
    files_with_isolated = []
    
    # 第一阶段：清除孤立节点，输出到 step7_final
    for json_file in sorted(json_files):
        logger.info(f"📄 处理: {json_file.name}")
        
        # 确定输出路径（必须输出到指定目录）
        if output_path:
            file_output_path = str(output_path / json_file.name)
        else:
            # 如果没有指定输出路径，使用输入目录
            file_output_path = str(json_file)
        
        removed_count, removed_nodes = clean_isolated_nodes(
            str(json_file), 
            dry_run=dry_run,
            output_path=file_output_path,
            remove_condition_edges=remove_condition_edges
        )
        
        if removed_count > 0:
            total_removed += removed_count
            files_with_isolated.append({
                "file": json_file.name,
                "count": removed_count,
                "nodes": removed_nodes
            })
            logger.info(f"   {'发现' if dry_run else '删除'} {removed_count} 个孤立节点")
        else:
            logger.info(f"   ✓ 没有孤立节点")
            if file_output_path and not dry_run:
                # 即使没有孤立节点，也复制文件到输出目录
                import shutil
                shutil.copy2(str(json_file), file_output_path)
        
    # 总结（对外只保留 1-2 行 INFO 日志，其余细节仍在 DEBUG 中）
    logger.info(
        f"Step 7: 清理完成 - 处理 {len(json_files)} 个 workflow，"
        f"{'发现' if dry_run else '删除'} {total_removed} 个孤立节点，"
        f"涉及 {len(files_with_isolated)} 个文件"
    )
    
    if total_removed > 0:
        logger.debug("📋 详细列表:")
        for item in files_with_isolated:
            logger.debug(f"  - {item['file']}: {item['count']} 个节点")
    else:
        logger.info("✅ 所有文件都没有孤立节点！")
    
    if dry_run and total_removed > 0:
        logger.info("\n💡 提示: 这是检查模式，没有实际删除节点。")
        logger.info("   要实际删除节点，请运行: python step7_clean_isolated_nodes.py")
    
    # 第二阶段：布局优化（仅在非 dry_run 模式下执行，且必须指定输出目录）
    if not dry_run and optimize and output_path:
        logger.info("="*60)
        logger.info("🎨 布局优化阶段")
        logger.info("="*60)
        logger.debug(f"优化目录: {output_path}")
        logger.debug(f"布局方向: {layout_direction}\n")
        
        # 获取输出目录中的所有 workflow 文件
        final_json_files = list(output_path.glob("generated_workflow_*.json"))
        
        if not final_json_files:
            logger.warning(f"⚠️  在输出目录中未找到 workflow 文件，跳过布局优化")
        else:
            # 加载布局工具模块
            try:
                layout_tool_path = os.path.join(os.path.dirname(__file__), 'layout_tools', 'step7_layout_optimizer.py')
                if not os.path.exists(layout_tool_path):
                    logger.warning(f"  ⚠️  布局工具未找到: {layout_tool_path}")
                    logger.debug(f"  💡 跳过布局优化步骤")
                else:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("layout_tools.step7_layout_optimizer", layout_tool_path)
                    layout_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(layout_module)
                    
                    if hasattr(layout_module, 'optimize_workflow_layout'):
                        # 对每个文件进行布局优化，原地替换
                        for final_file in sorted(final_json_files):
                            logger.debug(f"🎨 优化布局: {final_file.name}")
                            try:
                                layout_module.optimize_workflow_layout(
                                    str(final_file),
                                    output_file=str(final_file),  # 原地替换
                                    direction=layout_direction
                                )
                                logger.debug(f"   ✅ 布局优化完成\n")
                            except Exception as e:
                                logger.warning(f"   ⚠️  布局优化失败: {str(e)}\n")
                                # 继续处理下一个文件，不中断流程
                    else:
                        logger.warning(f"  ⚠️  布局工具缺少 optimize_workflow_layout 函数")
            except Exception as e:
                logger.warning(f"  ⚠️  布局优化失败: {str(e)}")
                logger.debug(f"  💡 跳过布局优化步骤，继续执行")
                import traceback
                logger.error(traceback.format_exc())
        
        logger.info("="*60)
        logger.info("✅ 所有处理完成")
        logger.info("="*60)


if __name__ == "__main__":
    import sys
    
    # 检查命令行参数
    dry_run = "--dry-run" in sys.argv or "-d" in sys.argv
    no_optimize = "--no-optimize" in sys.argv or "--no-layout" in sys.argv
    input_dir = None
    layout_direction = 'LR'
    
    # 检查是否有指定输入目录
    for i, arg in enumerate(sys.argv):
        if arg in ["--input", "-i"] and i + 1 < len(sys.argv):
            input_dir = sys.argv[i + 1]
        elif arg in ["--direction", "-dir"] and i + 1 < len(sys.argv):
            layout_direction = sys.argv[i + 1]
    
    main(dry_run=dry_run, input_dir=input_dir, optimize=not no_optimize, layout_direction=layout_direction)

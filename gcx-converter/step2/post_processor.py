"""
Post Processor - 后处理逻辑

职责：
- 处理 jump edges：为 jump 节点添加边连接
- 过滤无效边：jump 到 jump、jump 节点作为 source、自环等
- 去重：去除重复的边
- 解析 page_* 引用：将 page_xxx 占位符解析为实际的节点名称
- 兜底清理：确保所有节点都有正确的连接

输入：
- all_nodes: List[Dict] - 所有节点列表
- all_edges: List[Dict] - 所有边列表
- page_id_to_entry: Dict[str, str] - page_id 前缀到入口节点名称的映射
- jump_node_names: Set[str] - jump 节点名称集合（可选）

输出：
- filtered_edges: List[Dict] - 过滤后的边列表
- processed_edges: List[Dict] - 处理后的边列表（已解析 page_* 引用）

注意：
- 此模块负责所有后处理逻辑，确保生成的 workflow 结构正确
- 应该在所有节点和边生成完成后调用
"""

from typing import List, Dict, Any, Set, Optional


# write by senlin.deng 2026-01-18
# 删除“空条件”的 condition 节点，并将其上游边直接重连到下游节点。(兜底的删除)
# 适用场景：
# - 某些版本的路由会生成一个 condition 节点，但其 if_else_conditions 的所有分支 conditions 都为空，
#   导致它变成“无条件条件节点”（纯中转/冗余）。
# - 对于这类节点，如果它的所有出边最终都指向同一个 target（或只有一条出边），可以安全删除并重连。
# 重连策略：
# - 对每条进入该 condition 节点的边（incoming），复制该边并把 target_node 改为该节点的唯一下一跳。
# - 保留 incoming 的 connection_type / condition_id（尤其用于 semanticJudgment 的 condition_id 连边）。
def remove_empty_condition_nodes(
    all_nodes: List[Dict[str, Any]],
    all_edges: List[Dict[str, Any]],
    verbose: bool = True
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    删除“空条件”的 condition 节点，并将其上游边直接重连到下游节点。

    适用场景：
    - 某些版本的路由会生成一个 condition 节点，但其 if_else_conditions 的所有分支 conditions 都为空，
      导致它变成“无条件条件节点”（纯中转/冗余）。
    - 对于这类节点，如果它的所有出边最终都指向同一个 target（或只有一条出边），可以安全删除并重连。

    重连策略：
    - 对每条进入该 condition 节点的边（incoming），复制该边并把 target_node 改为该节点的唯一下一跳。
    - 保留 incoming 的 connection_type / condition_id（尤其用于 semanticJudgment 的 condition_id 连边）。

    注意：
    - 如果一个空条件 condition 节点存在多个不同的出边目标，为避免语义变化，不做删除。
    """
    node_by_name = {n.get("name"): n for n in all_nodes if n.get("name")}
    condition_nodes = [n for n in all_nodes if n.get("type") == "condition" and n.get("name")]

    edges = list(all_edges)
    removed_names: Set[str] = set()

    def _is_empty_conditions_branch(branch: Dict[str, Any]) -> bool:
        conds = branch.get("conditions")
        return not conds  # None or []

    for node in condition_nodes:
        name = node.get("name")
        if not name or name in removed_names:
            continue

        branches = node.get("if_else_conditions", [])
        if not branches:
            continue

        # 全部分支都没有任何 conditions => “空条件 condition 节点”
        if not all(_is_empty_conditions_branch(b) for b in branches):
            continue

        outgoing = [e for e in edges if e.get("source_node") == name]
        if not outgoing:
            continue

        unique_targets = {e.get("target_node") for e in outgoing if e.get("target_node")}
        if len(unique_targets) != 1:
            # 多个不同目标：不删除，避免改变路由语义
            continue

        next_target = next(iter(unique_targets))
        if not next_target or next_target == name:
            continue

        incoming = [e for e in edges if e.get("target_node") == name]
        if not incoming:
            # 没有上游连接，删掉也没意义；保留
            continue

        # 移除所有指向/来自该节点的边
        edges = [e for e in edges if e.get("source_node") != name and e.get("target_node") != name]

        # 重连上游到下一跳
        new_edges = []
        for inc in incoming:
            source = inc.get("source_node")
            if not source or source == next_target:
                continue
            new_edge = {
                "source_node": source,
                "target_node": next_target,
                "connection_type": inc.get("connection_type", "default")
            }
            if inc.get("condition_id"):
                new_edge["condition_id"] = inc.get("condition_id")
            new_edges.append(new_edge)

        edges.extend(new_edges)

        # 删除节点
        removed_names.add(name)
        if verbose:
            print(f"  ✅ Removed empty condition node: {name} (rewired {len(incoming)} incoming edges → {next_target})")

    if not removed_names:
        return all_nodes, edges

    new_nodes = [n for n in all_nodes if n.get("name") not in removed_names]
    return new_nodes, edges


def filter_invalid_edges(all_edges: List[Dict[str, Any]], all_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    过滤掉所有无效的边（jump 到 jump、jump 节点作为 source、自环）
    
    Args:
        all_edges: 所有边的列表
        all_nodes: 所有节点的列表
        
    Returns:
        过滤后的边列表
    """
    # 收集所有 jump 节点名称
    jump_node_names = {node.get('name') for node in all_nodes if node.get('type') == 'jump'}
    
    filtered_edges = []
    removed_count = 0
    jump_to_jump_count = 0
    jump_as_source_count = 0
    self_loop_count = 0
    
    for edge in all_edges:
        source = edge.get('source_node', '')
        target = edge.get('target_node', '')
        
        # 过滤掉自环
        if source == target:
            removed_count += 1
            self_loop_count += 1
            continue
        
        # 过滤掉 jump 节点作为 source 的边（jump 节点不能有出边）
        # 这包括所有 jump 到 jump 的边
        if source in jump_node_names:
            removed_count += 1
            jump_as_source_count += 1
            # 如果 target 也是 jump 节点，记录为 jump-to-jump
            if target in jump_node_names:
                jump_to_jump_count += 1
            continue
        
        # 如果 source 不是 jump 节点，但 target 是 jump 节点，这是允许的（jump 节点可以作为 target）
        # 所以不需要过滤
        
        filtered_edges.append(edge)
    
    if removed_count > 0:
        print(f'   ⚠️  Removed {removed_count} invalid edges:')
        if jump_to_jump_count > 0:
            print(f'      - {jump_to_jump_count} jump-to-jump edges')
        if jump_as_source_count > 0:
            print(f'      - {jump_as_source_count} edges with jump node as source')
        if self_loop_count > 0:
            print(f'      - {self_loop_count} self-loops')
    
    return filtered_edges


def can_add_edge(source: str, target: str, all_nodes: List[Dict[str, Any]] = None, jump_node_names: Set[str] = None) -> bool:
    """
    检查是否可以添加边
    
    Args:
        source: 源节点名称
        target: 目标节点名称
        all_nodes: 所有节点列表（可选，用于检查节点类型）
        jump_node_names: jump 节点名称集合（可选，如果提供则直接使用）
        
    Returns:
        True 如果可以添加，False 如果不可以
    """
    # 如果 source 和 target 相同，不能添加（自环）
    if source == target:
        return False
    
    # 如果没有提供 jump_node_names，从 all_nodes 中收集
    if jump_node_names is None and all_nodes:
        jump_node_names = {node.get('name') for node in all_nodes if node.get('type') == 'jump'}
    elif jump_node_names is None:
        jump_node_names = set()
    
    # 如果 source 是 jump 节点，不能添加（jump 节点不能有出边）
    if source in jump_node_names:
        return False
    
    # 如果 source 和 target 都是 jump 节点，不能添加（jump 到 jump）
    if source in jump_node_names and target in jump_node_names:
        return False
    
    return True


def safe_append_edge(
    edges: List[Dict[str, Any]], 
    source: str, 
    target: str, 
    connection_type: str = "default", 
    condition_id: str = None, 
    all_nodes: List[Dict[str, Any]] = None, 
    jump_node_names: Set[str] = None, 
    verbose: bool = True
) -> bool:
    """
    安全地添加边，在添加之前检查是否允许
    
    Args:
        edges: 边列表
        source: 源节点名称
        target: 目标节点名称
        connection_type: 连接类型（default 或 condition）
        condition_id: 条件ID（可选）
        all_nodes: 所有节点列表（可选，用于检查节点类型）
        jump_node_names: jump 节点名称集合（可选，如果提供则直接使用）
        verbose: 是否打印警告信息
        
    Returns:
        True 如果成功添加，False 如果不允许添加
    """
    # 检查是否可以添加边
    if not can_add_edge(source, target, all_nodes, jump_node_names):
        if verbose:
            print(f'  ⚠️  Warning: Skipping invalid edge (jump to jump or self-loop): {source} -> {target}')
        return False
    
    # 创建边对象
    edge = {
        "source_node": source,
        "target_node": target,
        "connection_type": connection_type
    }
    if condition_id:
        edge["condition_id"] = condition_id
    
    edges.append(edge)
    return True


def resolve_page_references(
    edges: List[Dict[str, Any]], 
    page_id_to_entry: Dict[str, str],
    all_nodes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    解析 page_xxx 引用为实际的节点名称
    
    Args:
        edges: 边列表（可能包含 page_xxx 占位符）
        page_id_to_entry: page_id 前缀到入口节点名称的映射
        all_nodes: 所有节点列表（用于验证解析后的节点是否存在）
        
    Returns:
        解析后的边列表
    """
    resolved_edges = []
    jump_node_names = {node.get('name') for node in all_nodes if node.get('type') == 'jump'}
    
    for edge in edges:
        target = edge.get('target_node', '')
        source = edge.get('source_node', '')
        
        # 如果 source 是 jump 节点，跳过（jump 节点不能有出边）
        if source in jump_node_names:
            continue
        
        # 解析 page_xxx 引用
        if target.startswith('page_'):
            page_prefix = target.replace('page_', '')
            if page_prefix in page_id_to_entry:
                resolved_target = page_id_to_entry[page_prefix]
                edge['target_node'] = resolved_target
            else:
                # 如果 page_xxx 没有被解析，保留原样（可能在后续处理中解析）
                print(f'  ⚠️  Warning: page_{page_prefix} not found in page_id_to_entry')
        
        resolved_edges.append(edge)
    
    return resolved_edges


def deduplicate_edges(edges: List[Dict[str, Any]], condition_node_names: Set[str] = None) -> List[Dict[str, Any]]:
    """
    去重边（对于非 condition 节点，只保留一条 default 出边）
    
    Args:
        edges: 边列表
        condition_node_names: condition 节点名称集合（可选）
        
    Returns:
        去重后的边列表
    """
    if condition_node_names is None:
        condition_node_names = set()
    
    # 记录每个 source_node 的 default 出边（用于去重，condition 节点除外）
    source_default_edges = {}  # source_node -> edge (只保留一条 default 出边)
    source_condition_targets = {}  # source_node -> set of target_nodes (用于 condition 连接去重)
    fixed_edges = []
    
    for edge in edges:
        target = edge.get('target_node', '')
        source = edge.get('source_node', '')
        connection_type = edge.get('connection_type', 'default')
        
        # 对于非 condition 节点，检查是否有重复的出边
        if source not in condition_node_names:
            # 对于 default 连接，非 condition 节点应该只有一条出边
            if connection_type == 'default':
                if source not in source_default_edges:
                    source_default_edges[source] = edge
                else:
                    # 已经有 default 出边，跳过重复的
                    existing_edge = source_default_edges[source]
                    existing_target = existing_edge.get('target_node', '')
                    print(f'  ⚠️  Warning: Skipping duplicate default edge (non-condition node should have only one default outgoing edge): {source} -> {target} (keeping: {source} -> {existing_target})')
                    continue
            else:
                # 对于 condition 连接，检查是否有相同的 target_node
                if source not in source_condition_targets:
                    source_condition_targets[source] = set()
                if target in source_condition_targets[source]:
                    print(f'  ⚠️  Warning: Skipping duplicate condition edge (non-condition node should not have multiple condition edges to same target): {source} -> {target}')
                    continue
                source_condition_targets[source].add(target)
                fixed_edges.append(edge)
        else:
            # condition 节点可以有多个出边
            fixed_edges.append(edge)
    
    # 添加所有保留的 default 出边
    for edge in source_default_edges.values():
        fixed_edges.append(edge)
    
    return fixed_edges


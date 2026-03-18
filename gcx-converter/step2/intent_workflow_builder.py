"""
Intent Workflow Builder - 组装单个 Intent 的完整 Workflow

职责：
- 针对单 intent，组装 start 节点
- 组装 flow 开头链路（可选）：flow slots 抽取链、flow conditions 条件节点
- 组装 intent 参数抽槽链路（可选）：capture → llm → parse
- 组装 param_code 节点（beforeTransition 的 setParameterActions）
- 组装 page 节点：调用 generate_workflow_from_page 处理各个 page
- 组装 jump 节点：处理页面层级的跳转
- 组装条件路由：处理 intent routing、parameter routing 等
- 负责将各片段合并成完整 nodes/edges，并返回 entry/exit 信息

输入：
- intent_id: str - 意图ID
- intent_name: str - 意图名称
- safe_intent_name: str - 安全的意图名称（用于文件名）
- event: Dict[str, Any] - transitionEvent 字典
- target_page_id: str - 目标 page ID
- target_flow_id: str - 目标 flow ID
- page_id_map: Dict[str, Dict] - page_id 到 page 数据的映射
- lang: str - 语言代码
- flow_slot_infos: List[Dict] - flow 层级的槽位信息（可选）
- flow_conditions: List[Dict] - flow 层级的条件（可选）

输出：
- workflow_name: str - 生成的 workflow 名称
- nodes: List[Dict] - 所有节点列表
- edges: List[Dict] - 所有边列表
- entry_node: str - 入口节点名称

注意：
- 此模块负责组装单个 intent 的完整 workflow
- 实际的 page 节点生成由 generate_workflow_from_page 方法处理（该方法仍在 converter.py 中，后续可迁移）
- 应该在所有节点和边生成完成后，调用 post_processor 进行后处理
"""

from typing import Dict, List, Any, Tuple, Callable, Optional
from step2.intent_chain_builder import build_flow_slot_chain, build_flow_condition_chain
from step2.post_processor import safe_append_edge, can_add_edge


def build_single_intent_workflow(
    intent_id: str,
    intent_name: str,
    safe_intent_name: str,
    event: Dict[str, Any],
    target_page_id: str,
    target_flow_id: str,
    page_id_map: Dict[str, Any],
    lang: str,
    flow_slot_infos: Optional[List[Dict[str, Any]]] = None,
    flow_conditions: Optional[List[Dict[str, Any]]] = None,
    intent_parameters_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    entity_candidates: Optional[Dict[str, Dict[str, List[str]]]] = None,
    gen_unique_node_name: Optional[Callable[[str, str], str]] = None,
    gen_variable_name: Optional[Callable[[], str]] = None,
    generate_workflow_from_page: Optional[Callable] = None,
    collect_related_pages: Optional[Callable] = None
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    为单个 intent 生成独立的 workflow
    
    注意：此函数是框架，实际的实现逻辑仍在 converter.py 的 _generate_single_intent_workflow 方法中
    后续需要将 converter.py 中的逻辑迁移到此模块
    
    Args:
        intent_id: 意图ID
        intent_name: 意图名称
        safe_intent_name: 安全的意图名称（用于文件名）
        event: transitionEvent 字典
        target_page_id: 目标 page ID
        target_flow_id: 目标 flow ID
        page_id_map: page_id 到 page 数据的映射
        lang: 语言代码
        flow_slot_infos: flow 层级的槽位信息（可选）
        flow_conditions: flow 层级的条件（可选）
        intent_parameters_map: intent 参数映射（可选）
        entity_candidates: 实体候选值映射（可选）
        gen_unique_node_name: 生成唯一节点名的函数（可选）
        gen_variable_name: 生成变量名的函数（可选）
        generate_workflow_from_page: 生成 page workflow 的函数（可选）
        collect_related_pages: 收集相关 pages 的函数（可选）
        
    Returns:
        (workflow_name, nodes, edges, entry_node)
    """
    # TODO: 将 converter.py 中的 _generate_single_intent_workflow 方法逻辑迁移到此函数
    # 当前此函数仅作为占位符，实际逻辑仍在 converter.py 中
    
    raise NotImplementedError(
        "此函数需要从 converter.py 的 _generate_single_intent_workflow 方法迁移逻辑。"
        "由于代码量较大，建议分步迁移："
        "1. 先迁移 start 节点和 flow 开头链路的组装逻辑"
        "2. 再迁移 intent 参数抽槽链路的组装逻辑"
        "3. 最后迁移 page 节点和 jump 节点的组装逻辑"
    )


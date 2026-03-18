"""
Flow Start Handler - 解析 Flow 层级逻辑

职责：
- 解析 flow 层级的 transitionEvents、slots、startpage 条件
- 生成开头的抽槽链、条件节点等共用"入口"结构
- 输出结构化数据（而不是直接 nodes/edges），供后续组装

输入：
- flow_data: exported_flow_*.json 的完整数据
- event: 单个 transitionEvent（可选，用于按 intent 提取条件）

输出：
- flow_slot_infos: List[Dict] - flow 层级的槽位信息列表
- flow_conditions: List[Dict] - flow 层级的条件列表（针对单个 event 或全部）
- transition_events: List[Dict] - flow 层级的 transitionEvents 列表

注意：
- 此模块不直接生成 nodes/edges，而是返回结构化数据
- 实际的节点生成由 intent_chain_builder.py 负责
"""

from typing import Dict, List, Any
from step2.flow_utils import (
    extract_flow_slots,
    extract_flow_conditions,
    extract_flow_conditions_for_event,
)


def parse_flow_start_data(flow_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    解析 flow 层级的起始数据（slots、conditions、transitionEvents）
    
    Args:
        flow_data: exported_flow_*.json 的完整数据
        
    Returns:
        {
            'flow_slot_infos': List[Dict],  # flow 层级的槽位信息
            'transition_events': List[Dict],  # flow 层级的 transitionEvents
            'flow_obj': Dict  # flow.flow 对象（用于后续处理）
        }
    """
    flow_obj = flow_data.get('flow', {}).get('flow', {})
    transition_events = flow_obj.get('transitionEvents', [])
    
    # 收集 flow 层级的 slots（用于 start page 抽槽）
    flow_slot_infos = extract_flow_slots(flow_data)
    
    return {
        'flow_slot_infos': flow_slot_infos,
        'transition_events': transition_events,
        'flow_obj': flow_obj
    }


def get_flow_conditions_for_event(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    获取单个 transitionEvent 对应的 flow 层级条件
    
    Args:
        event: 单个 transitionEvent 字典
        
    Returns:
        List[Dict] - 该 event 对应的条件列表（已过滤无效条件）
    """
    return extract_flow_conditions_for_event(event)


def get_all_flow_conditions(flow_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    获取所有 flow 层级的条件（用于全局分析）
    
    Args:
        flow_data: exported_flow_*.json 的完整数据
        
    Returns:
        List[Dict] - 所有 flow 层级的条件列表（已过滤无效条件和去重）
    """
    return extract_flow_conditions(flow_data)


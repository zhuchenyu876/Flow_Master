"""
Step 0: 从exported_flow文件中提取entities、intents、fulfillments
=============================================================
功能：从完整的exported_flow_*.json文件中提取并生成独立的JSON文件
输出：entities.json, intents.json, fulfillments.json

这是整个工作流转换的第一步，为后续步骤准备输入文件。

作者：chenyu.zhu
日期：2025-12-17
"""

from copy import deepcopy
import json
import os
import sys
import uuid
from logger_config import get_logger
logger = get_logger(__name__)


def validate_dialogflow_cx_file(file_path: str) -> tuple:
    """
    验证文件是否为有效的 Dialogflow CX 导出文件
    
    Args:
        file_path: 文件路径
        
    Returns:
        (is_valid, error_message)
    """
    # 检查文件是否存在
    if not os.path.exists(file_path):
        return False, f"文件不存在: {file_path}"
    
    # 检查文件大小
    file_size = os.path.getsize(file_path)
    if file_size == 0:
        return False, "文件为空"
    
    if file_size > 500 * 1024 * 1024:  # 500MB
        return False, f"文件过大 ({file_size / 1024 / 1024:.1f}MB)，可能不是有效的 Dialogflow CX 文件"
    
    # 验证 JSON 格式
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, f"文件不是有效的 JSON 格式: {str(e)}"
    except UnicodeDecodeError:
        return False, "文件编码错误，无法读取（请确保文件为 UTF-8 编码）"
    except Exception as e:
        return False, f"无法读取文件: {str(e)}"
    
    # 验证根节点结构
    if not isinstance(data, dict):
        return False, "文件格式错误：根节点必须是对象（JSON Object）"
    
    # 验证是否包含 Dialogflow CX 必需的 'flow' 字段
    if 'flow' not in data:
        return False, "文件格式错误：缺少 'flow' 字段。这不是有效的 Dialogflow CX 导出文件，请确保从 Dialogflow CX 控制台正确导出 Flow"
    
    flow_data = data.get('flow', {})
    
    if not isinstance(flow_data, dict):
        return False, "文件格式错误：'flow' 字段必须是对象"
    
    # 验证关键字段
    if 'intents' not in flow_data:
        return False, "文件格式错误：flow 中缺少 'intents' 字段"
    
    if 'pages' not in flow_data:
        return False, "文件格式错误：flow 中缺少 'pages' 字段"
    
    # 验证数据类型
    intents = flow_data.get('intents', [])
    if not isinstance(intents, list):
        return False, "文件格式错误：'intents' 必须是数组"
    
    pages = flow_data.get('pages', [])
    if not isinstance(pages, list):
        return False, "文件格式错误：'pages' 必须是数组"
    
    # 验证数据不为空
    if len(intents) == 0:
        return False, "文件内容错误：'intents' 为空，无法进行迁移。请确保导出的 Flow 包含意图（Intents）"
    
    if len(pages) == 0:
        return False, "文件内容警告：'pages' 为空，Flow 可能没有页面定义"
    
    logger.info(f"✅ 文件验证通过: {len(intents)} 个 intents, {len(pages)} 个 pages")
    return True, ""


def extract_from_exported_flow(
    exported_flow_file: str = 'exported_flow_TXNAndSTMT_Deeplink.json',
    output_entities: str = 'entities.json',
    output_intents: str = 'intents.json',
    output_fulfillments: str = 'fulfillments.json'
):
    """
    从exported_flow文件中提取entities、intents、fulfillments
    
    注意：
    - 会自动从 exported_flow_file 同目录下查找对应的 router 文件（文件名包含 "(router)"）
    - 如果找到 router 文件，会合并 transitionRouteGroups 和 agentTransitionRouteGroups 中的 transitionEvents
    - page 原有的 transitionEvents 会保持在前面，路由组中的 transitionEvents 会追加在后面
    
    Args:
        exported_flow_file: 输入的exported_flow文件路径
        output_entities: 输出的entities文件路径
        output_intents: 输出的intents文件路径
        output_fulfillments: 输出的fulfillments文件路径
    """
    logger.info(f'Step 0: 从 {exported_flow_file} 提取数据')
    
    # 先验证文件格式
    is_valid, error_msg = validate_dialogflow_cx_file(exported_flow_file)
    if not is_valid:
        logger.error(f'❌ 文件验证失败: {error_msg}')
        return False
    
    try:
        with open(exported_flow_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 提取flow数据
        flow_data = data.get('flow', {})
        
        # # write by senlin.deng 2026-01-15
        # # 解决路由组问题，需要合并transitionEvents
        # # 从同一个文件中加载路由组映射
        route_groups_map = {}  # key -> transitionEvents list (用于 transitionRouteGroups)
        agent_route_groups_map = {}  # key -> transitionEvents list (用于 agentTransitionRouteGroups)

        usedflowid = set()
        # 从 transitionRouteGroups 中提取当前所有的路由组
        transition_route_groups = flow_data.get('transitionRouteGroups', [])
        if transition_route_groups:
            logger.info(f'📂 从文件中加载 {len(transition_route_groups)} 个路由组')
            for idx, route_group in enumerate(transition_route_groups):
                # 检查 route_group 是否是字典类型（对象数组格式）
                if isinstance(route_group, dict):
                    route_group_key = route_group.get('key')
                    route_group_value = route_group.get('value', {})
                    if isinstance(route_group_value, dict):
                        transition_events = route_group_value.get('transitionEvents', [])
                        for jdx, event in enumerate(transition_events):
                            handler = event.get('transitionEventHandler', {})
                            # write by senlin.deng 2026-01-18
                            # 清理特殊 targetPageId 值（PREVIOUS_PAGE）设置为 None
                            # 设置pageid为flowid，使得生成跳转节点
                            if handler and handler.get('targetPageId') in ["PREVIOUS_PAGE"]:
                                transition_route_groups[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'] = str(uuid.uuid4())
                                transition_route_groups[idx]['value']['transitionEvents'][jdx]['transitionEventHandler'].pop('targetPageId')
                    if route_group_key and transition_events:
                            route_groups_map[route_group_key] = transition_events
                            logger.debug(f'  加载路由组: {route_group_key} ({len(transition_events)} 个 transitionEvents)')
                # 如果是字符串（ID 列表格式），跳过（这些只是引用，实际定义在其他地方）
                elif isinstance(route_group, str):
                    logger.debug(f'  跳过路由组引用（字符串格式）: {route_group}')
        
        # 从 agentTransitionRouteGroups 中提取路由组
        agent_transition_route_groups = flow_data.get('agentTransitionRouteGroups', [])
        if agent_transition_route_groups:
            logger.info(f'📂 从文件中加载 {len(agent_transition_route_groups)} 个 Agent 路由组')
            for idx, route_group in enumerate(agent_transition_route_groups):
                # 检查 route_group 是否是字典类型（对象数组格式）
                if isinstance(route_group, dict):
                    route_group_key = route_group.get('key')
                    route_group_value = route_group.get('value', {})
                    if isinstance(route_group_value, dict):
                        transition_events = route_group_value.get('transitionEvents', [])
                        # write by senlin.deng 2026-01-18
                        # 清理特殊 targetFlowId 值（None）设置为 uuid
                        # 设置flowid为uuid，使得生成跳转节点
                        for jdx, event in enumerate(transition_events):
                            handler = event.get('transitionEventHandler', {})
                            if handler and handler.get('targetFlowId') == None:
                                agent_transition_route_groups[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'] = str(uuid.uuid4())
                            # write by senlin.deng 2026-01-22
                            # 如果targetFlowId已经存在，则设置为新的uuid。避免重复生成跳转节点，导致跳转节点被去重缺失
                            if agent_transition_route_groups[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'] in usedflowid:
                                agent_transition_route_groups[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'] = str(uuid.uuid4())
                            usedflowid.add(agent_transition_route_groups[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'])
                        if route_group_key and transition_events:
                            agent_route_groups_map[route_group_key] = transition_events
                            logger.debug(f'  加载 Agent 路由组: {route_group_key} ({len(transition_events)} 个 transitionEvents)')
                # 如果是字符串（ID 列表格式），跳过（这些只是引用，实际定义在其他地方）
                elif isinstance(route_group, str):
                    logger.debug(f'  跳过 Agent 路由组引用（字符串格式）: {route_group}')
        
        if route_groups_map or agent_route_groups_map:
            logger.info(f'✅ 成功加载 {len(route_groups_map)} 个路由组和 {len(agent_route_groups_map)} 个 Agent 路由组')
        
        # 1. 提取entities
        entities = flow_data.get('entities', [])
        entities_output = {'entities': entities}
        with open(output_entities, 'w', encoding='utf-8') as f:
            json.dump(entities_output, f, ensure_ascii=False, indent=2)
        
        # 2. 提取intents
        intents = flow_data.get('intents', [])
        intents_output = {'intents': intents}
        with open(output_intents, 'w', encoding='utf-8') as f:
            json.dump(intents_output, f, ensure_ascii=False, indent=2)
        
        # 3. 提取fulfillments (pages) 并合并路由组中的 transitionEvents
        pages = flow_data.get('pages', [])
        
        # write by senlin.deng 2026-01-15
        # 解决路由组问题，需要将原始的 transitionEvents 和路由组的 transitionEvents 分开存储
        # 处理每个 page，将路由组中的 transitionEvents 存储到单独的字段
        separated_pages_count = 0
        for idx, page in enumerate(pages):
            page_value = page.get('value', {})
            
            # 检查是否有 transitionRouteGroups 或 agentTransitionRouteGroups
            transition_route_groups = page_value.get('transitionRouteGroups', [])
            agent_transition_route_groups = page_value.get('agentTransitionRouteGroups', [])
            
            # 只有当 page 存在 transitionRouteGroups 或 agentTransitionRouteGroups 时才进行处理
            if transition_route_groups or agent_transition_route_groups:
                # 保存原有的 transitionEvents（保持不变）
                original_transition_events = list(page_value.get('transitionEvents', []))
                
                # 收集路由组中的 transitionEvents
                route_groups_transition_events = []
                
                # 处理 transitionRouteGroups
                if transition_route_groups and route_groups_map:
                    display_name = page_value.get('displayName', '')
                    page_key = page.get('key', '')
                    logger.debug(f'  Page {display_name} ({page_key[:8]}...) 有 {len(transition_route_groups)} 个路由组引用')
                    for route_group_id in transition_route_groups:
                        if route_group_id in route_groups_map:
                            route_group_events = route_groups_map[route_group_id]
                            logger.debug(f'    提取路由组 {route_group_id[:8]}... 的 {len(route_group_events)} 个 transitionEvents')
                            # 深拷贝，避免多页面共享同一事件对象导致 CURRENT_PAGE 被互相覆盖
                            route_groups_transition_events.extend(deepcopy(route_group_events))
                        else:
                            logger.warning(f'    ⚠️  未找到路由组: {route_group_id}')
                
                # 处理 agentTransitionRouteGroups
                if agent_transition_route_groups and agent_route_groups_map:
                    display_name = page_value.get('displayName', '')
                    page_key = page.get('key', '')
                    logger.debug(f'  Page {display_name} ({page_key[:8]}...) 有 {len(agent_transition_route_groups)} 个 Agent 路由组引用')
                    for route_group_id in agent_transition_route_groups:
                        if route_group_id in agent_route_groups_map:
                            route_group_events = agent_route_groups_map[route_group_id]
                            logger.debug(f'    提取 Agent 路由组 {route_group_id[:8]}... 的 {len(route_group_events)} 个 transitionEvents')
                            # 深拷贝，避免多页面共享同一事件对象导致 CURRENT_PAGE 被互相覆盖
                            route_groups_transition_events.extend(deepcopy(route_group_events))
                        else:
                            logger.warning(f'    ⚠️  未找到 Agent 路由组: {route_group_id}')
                
                # 将路由组的 transitionEvents 存储到单独的字段
                if route_groups_transition_events:
                    pages[idx]['value']['routeGroupsTransitionEvents'] = route_groups_transition_events
                    separated_pages_count += 1
                    logger.debug(f'  Page {display_name} ({page_key[:8]}...): 原始 {len(original_transition_events)} 个, 路由组 {len(route_groups_transition_events)} 个')
        
        if separated_pages_count > 0:
            logger.info(f'✅ 已分离 {separated_pages_count} 个 page 的路由组 transitionEvents')
        
        # write by senlin.deng 2026-01-20
        # 将page中transitionEvents下面，当targetPageId与targetFlowId如果都是null时，将targetFlowId设置为uuid
        for idx, page in enumerate(pages):
            page_value = page.get('value', {})
            page_key = page.get('key', '')
            # logger.info(f"page_key: {page_key}, {page_value.get('displayName', '')}")
            transition_events = page_value.get('transitionEvents', [])
            agent_transition_events = page_value.get('routeGroupsTransitionEvents', [])
            for jdx, event in enumerate(transition_events):
                handler = event.get('transitionEventHandler', {})
                # write by senlin.deng 2026-01-21
                # 清理特殊 targetPageId 值（CURRENT_PAGE）设置为 page_key，PREVIOUS_PAGE设置为uuid作为占位节点
                if handler and handler.get('targetPageId') in ["CURRENT_PAGE"]:
                    pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetPageId'] = page_key
                if handler and handler.get('targetPageId') in ["PREVIOUS_PAGE"]:
                    pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler'].pop('targetPageId')
                    pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'] = str(uuid.uuid4())

                if handler and handler.get('targetPageId') == None and handler.get('targetFlowId') == None:
                    pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'] = str(uuid.uuid4())
                
                # write by senlin.deng 2026-01-22
                # 如果targetFlowId已经存在，则设置为新的uuid。避免重复生成跳转节点，导致跳转节点被去重缺失
                if pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler'].get('targetFlowId') and pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'] in usedflowid:
                    pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'] = str(uuid.uuid4())
                if pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler'].get('targetFlowId'):
                    usedflowid.add(pages[idx]['value']['transitionEvents'][jdx]['transitionEventHandler']['targetFlowId'])

            # write by senlin.deng 2026-01-21
            # 处理路由组中的 routeGroupsTransitionEvents，使得targetPageId为CURRENT_PAGE时，设置为page_key，能够跳转到当前page开头
            for jdx, event in enumerate(agent_transition_events):
                handler = event.get('transitionEventHandler', {})
                if handler and handler.get('targetPageId') in ["CURRENT_PAGE"]:
                    pages[idx]['value']['routeGroupsTransitionEvents'][jdx]['transitionEventHandler']['targetPageId'] = page_key
        fulfillments_output = {'pages': pages}
        with open(output_fulfillments, 'w', encoding='utf-8') as f:
            json.dump(fulfillments_output, f, ensure_ascii=False, indent=2)
        logger.info(f'✅ Step 0 完成: {len(entities)} entities, {len(intents)} intents, {len(pages)} pages')
        return True
        
    except json.JSONDecodeError as e:
        logger.error(f'JSON解析失败: {str(e)}')
        return False
    
    except Exception as e:
        logger.error(f'错误: {str(e)}')
        return False


def main():
    """主函数"""
    import glob
    
    # 查找exported_flow文件
    flow_files = glob.glob('exported_flow_*.json')
    
    if not flow_files:
        logger.error('❌ 错误: 未找到 exported_flow_*.json 文件')
        
        # 尝试查找上级目录
        parent_flow_files = glob.glob('../exported_flow_*.json')
        if parent_flow_files:
            logger.info(f'💡 发现上级目录有flow文件: {parent_flow_files[0]}')
        
        return
    
    # 使用找到的第一个文件
    flow_file = flow_files[0]
    
    logger.info(f'🔍 自动检测到exported_flow文件: {flow_file}')
    
    # 如果有多个文件，提示用户
    if len(flow_files) > 1:
        logger.warning(f'⚠️  发现多个exported_flow文件，将使用第一个: {flow_file}')
    
    # 执行提取
    extract_from_exported_flow(
        exported_flow_file=flow_file,
        output_entities='entities.json',
        output_intents='intents.json',
        output_fulfillments='fulfillments.json'
    )


if __name__ == '__main__':
    # 检查命令行参数
    if len(sys.argv) > 1:
        # 用户指定了文件路径
        flow_file = sys.argv[1]
        
        if not os.path.exists(flow_file):
            logger.error(f'❌ 错误: 文件不存在 {flow_file}')
            sys.exit(1)
        
        extract_from_exported_flow(
            exported_flow_file=flow_file,
            output_entities='entities.json',
            output_intents='intents.json',
            output_fulfillments='fulfillments.json'
        )
    else:
        # 自动查找文件
        main()


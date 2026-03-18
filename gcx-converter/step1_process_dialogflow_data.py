"""
Dialogflow CX 数据处理工具集
包含6个主要功能：
1. process_entities_by_language - 处理实体（entities）
2. process_intents_by_language - 处理意图（intents）
3. process_fulfillments_by_language - 处理执行动作（fulfillments）
4. extract_intent_parameters - 提取intent的parameters信息
5. extract_flow_configs - 提取flow配置
6. extract_webhooks - 提取webhook配置

作者：chenyu.zhu
日期：2025-12-17
"""

import json
import os
from typing import Dict, List, Any
from collections import defaultdict

from logger_config import get_logger
logger = get_logger(__name__)


def process_entities_by_language(input_file: str = 'entities.json'):
    """
    处理entities.json文件，按语言分类并生成三个版本的JSON文件
    
    Args:
        input_file: 输入的entities.json文件路径
    
    输出:
        - entities_en.json: 英文版本
        - entities_zh.json: 简体中文版本
        - entities_zh-hant.json: 繁体中文版本
    """
    logger.info('Step 1.1: 处理 Entities')
    
    # 读取原始文件
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 初始化三个语言版本的数据结构
    lang_data = {
        'en': {'entities': []},
        'zh': {'entities': []},
        'zh-hant': {'entities': []}
    }
    
    # 遍历每个entity
    for entity in data.get('entities', []):
        entity_id = entity.get('id')
        display_name = entity.get('displayName')
        kind = entity.get('kind')
        entries = entity.get('entries', [])
        nlu_settings = entity.get('nluSettings', {})
        auto_expansion_mode = entity.get('autoExpansionMode', None)
        
        # 按语言分组entries
        lang_entries = {
            'en': defaultdict(list),
            'zh': defaultdict(list),
            'zh-hant': defaultdict(list)
        }
        
        for entry in entries:
            value = entry.get('value')
            synonyms = entry.get('synonyms', [])
            lang = entry.get('lang')

            # 语言映射：将 zh-hk 映射到 zh-hant，zh-cn 映射到 zh
            if lang == 'zh-hk':
                lang = 'zh-hant'
            elif lang == 'zh-cn':
                lang = 'zh'
            
            # 将entry添加到对应语言的分组中
            if lang in lang_entries:
                lang_entries[lang][value].extend(synonyms)
        
        # 为每个语言版本创建entity
        for lang in ['en', 'zh', 'zh-hant']:
            if lang_entries[lang]:  # 如果该语言有数据
                entity_obj = {
                    'id': entity_id,
                    'displayName': display_name,
                    'kind': kind,
                    'entries': []
                }
                
                # 添加该语言的所有entries
                for value, synonyms_list in lang_entries[lang].items():
                    # 去重synonyms
                    # writed by senlin.deng 2026-01-13
                    # 去除多余实体中的标准值、同义词中间的空格
                    unique_synonyms = list(dict.fromkeys(synonyms_list))
                    unique_synonyms = [' '.join(s.split()) for s in unique_synonyms]
                    entity_obj['entries'].append({
                        'value': ' '.join(value.split()),
                        'synonyms': unique_synonyms,
                        'lang': lang
                    })
                
                # 添加nluSettings
                entity_obj['nluSettings'] = nlu_settings
                
                # 如果有autoExpansionMode，也添加进去
                if auto_expansion_mode:
                    entity_obj['autoExpansionMode'] = auto_expansion_mode
                
                lang_data[lang]['entities'].append(entity_obj)
    
    # 保存三个语言版本的文件
    output_files = {
        'en': 'entities_en.json',
        'zh': 'entities_zh.json',
        'zh-hant': 'entities_zh-hant.json'
    }
    
    for lang, filename in output_files.items():
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(lang_data[lang], f, ensure_ascii=False, indent=2)
    
    logger.info(f'✅ Entities 处理完成: en={len(lang_data["en"]["entities"])}, zh={len(lang_data["zh"]["entities"])}, zh-hant={len(lang_data["zh-hant"]["entities"])}')


def process_intents_by_language(input_file: str = 'intents.json'):
    """
    处理intents.json文件，按语言分类并生成简化版本的JSON文件
    
    简化内容：
    1. 只保留id和displayName
    2. trainingPhrases简化为纯文本列表
    3. 去除type、userDefined、timesAddedCount、lang等重复字段
    
    Args:
        input_file: 输入的intents.json文件路径
    
    输出:
        - intents_en.json: 英文版本
        - intents_zh.json: 简体中文版本
        - intents_zh-hant.json: 繁体中文版本
    """
    logger.info('Step 1.2: 处理 Intents')
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 初始化三个语言版本的数据结构
    lang_data = {
        'en': {'intents': []},
        'zh': {'intents': []},
        'zh-hant': {'intents': []}
    }
    
    # 统计信息
    stats = {
        'en': {'intents': 0, 'phrases': 0},
        'zh': {'intents': 0, 'phrases': 0},
        'zh-hant': {'intents': 0, 'phrases': 0}
    }
    
    # 遍历每个intent
    total_intents = len(data.get('intents', []))
    logger.info(f'正在处理 {total_intents} 个intents...')
    
    for idx, intent in enumerate(data.get('intents', []), 1):
        meta = intent.get('meta', {})
        training_phrases = intent.get('trainingPhrases', [])
        
        intent_id = meta.get('id')
        display_name = meta.get('displayName')
        
        # 按语言分组training phrases，并提取纯文本
        lang_phrases = {
            'en': [],
            'zh': [],
            'zh-hant': []
        }
        
        for phrase in training_phrases:
            lang = phrase.get('lang')

            # 语言映射：将 zh-hk 映射到 zh-hant，zh-cn 映射到 zh
            if lang == 'zh-hk':
                lang = 'zh-hant'
            elif lang == 'zh-cn':
                lang = 'zh'
            
            if lang in lang_phrases:
                # 提取text内容
                parts = phrase.get('parts', [])
                if parts:
                    # 合并所有parts的text（有些可能有多个part）
                    text = ''.join(part.get('text', '') for part in parts)
                    # 去除末尾的\r或\n
                    text = text.rstrip('\r\n')
                    if text:  # 只添加非空文本
                        lang_phrases[lang].append(text)
        
        # 为每个语言版本创建简化的intent（只要有 ID 和 displayName 就添加，trainingPhrases 为空时给空数组）
        # 注意：某些 intent 可能没有 trainingPhrases，但仍需要在 intents_mapping 中，以便 step2 能正确命名 workflow
        if intent_id and display_name:
            for lang in ['en', 'zh', 'zh-hant']:
                intent_obj = {
                    'id': intent_id,
                    'displayName': display_name,
                    'trainingPhrases': lang_phrases[lang] if lang_phrases[lang] else []
                }
                
                lang_data[lang]['intents'].append(intent_obj)
                
                # 更新统计
                stats[lang]['intents'] += 1
                stats[lang]['phrases'] += len(lang_phrases[lang])
    
    # 保存三个语言版本的文件
    output_files = {
        'en': 'intents_en.json',
        'zh': 'intents_zh.json',
        'zh-hant': 'intents_zh-hant.json'
    }
    
    for lang, filename in output_files.items():
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(lang_data[lang], f, ensure_ascii=False, indent=2)
    
    logger.info(f'✅ Intents 处理完成: en={stats["en"]["intents"]}, zh={stats["zh"]["intents"]}, zh-hant={stats["zh-hant"]["intents"]}')


def process_fulfillments_by_language(input_file: str = 'fulfillments.json'):
    """
    处理fulfillments.json文件，按语言分类并提取页面执行动作和跳转信息
    
    提取内容：
    1. page key (id) 和 displayName
    2. onLoad 中的 responses payload 和 function
    3. transitionEvents 中的跳转信息
    4. slots 槽位信息（用于 page 层级的槽位抽取）
    
    Args:
        input_file: 输入的fulfillments.json文件路径
    
    输出:
        - fulfillments_en.json: 英文版本
        - fulfillments_zh.json: 简体中文版本
        - fulfillments_zh-hant.json: 繁体中文版本
    """
    logger.info('Step 1.3: 处理 Fulfillments')
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 初始化三个语言版本的数据结构
    lang_data = {
        'en': {'pages': []},
        'zh': {'pages': []},
        'zh-hant': {'pages': []}
    }
    
    # 统计信息
    stats = {
        'en': {'pages': 0, 'responses': 0, 'transitions': 0, 'slots': 0},
        'zh': {'pages': 0, 'responses': 0, 'transitions': 0, 'slots': 0},
        'zh-hant': {'pages': 0, 'responses': 0, 'transitions': 0, 'slots': 0}
    }
    
    # 遍历每个page
    total_pages = len(data.get('pages', []))
    
    for idx, page in enumerate(data.get('pages', []), 1):
        
        page_key = page.get('key')
        page_value = page.get('value', {})
        display_name = page_value.get('displayName', '')
        
        # 提取 onLoad 信息
        on_load = page_value.get('onLoad', {})
        
        # 提取 function
        function_info = on_load.get('function')
        
        # 提取 setParameterActions
        set_parameter_actions = on_load.get('setParameterActions')
        
        # 提取 staticUserResponse 按语言分组
        static_user_response = on_load.get('staticUserResponse', {})
        candidates = static_user_response.get('candidates', [])
        
        # 按语言分组 responses
        lang_responses = {
            'en': [],
            'zh': [],
            'zh-hant': []
        }
        
        for candidate in candidates:
            selector = candidate.get('selector', {})
            lang = selector.get('lang')

            # 语言映射：将 zh-hk 映射到 zh-hant，zh-cn 映射到 zh
            if lang == 'zh-hk':
                lang = 'zh-hant'
            elif lang == 'zh-cn':
                lang = 'zh'
            
            responses = candidate.get('responses', [])
            
            if lang in lang_responses:
                for response in responses:
                    payload = response.get('payload')
                    if payload:
                        lang_responses[lang].append(payload)
        
        # 提取 transitionEvents（跳转信息，不按语言区分）
        transition_events = page_value.get('transitionEvents', [])
        processed_transitions = []
        
        for event in transition_events:
            transition_info = {
                'name': event.get('name'),
                'triggerIntentId': event.get('triggerIntentId')
            }
            
            # 提取 condition
            condition = event.get('condition', {})
            if condition:
                restriction = condition.get('restriction', {})
                if restriction:
                    transition_info['condition'] = {
                        'comparator': restriction.get('comparator'),
                        'rhs': restriction.get('rhs')
                    }
                    # 可选：也提取 lhs
                    lhs = restriction.get('lhs')
                    if lhs:
                        transition_info['condition']['lhs'] = lhs
            
            # 提取 transitionEventHandler
            handler = event.get('transitionEventHandler', {})
            if handler:
                transition_info['transitionEventHandler'] = {
                    'beforeTransition': handler.get('beforeTransition', {}),
                    'targetPageId': handler.get('targetPageId'),
                    'targetFlowId': handler.get('targetFlowId')
                }
            
            # 提取 conditionString（更易读的条件表达式）
            condition_string = event.get('conditionString')
            if condition_string:
                transition_info['conditionString'] = condition_string
            
            processed_transitions.append(transition_info)
        
        # write by senlin.deng 2026-01-18
        # 提取 routeGroupsTransitionEvents（路由组跳转信息，不按语言区分）
        # 这是从 Route Groups 中分离出来的 transitionEvents，需要处理
        route_groups_events = page_value.get('routeGroupsTransitionEvents', [])
        processed_route_groups = []
        
        for event in route_groups_events:
            route_group_info = {
                'name': event.get('name'),
                'triggerIntentId': event.get('triggerIntentId')
            }
            
            # 提取 condition
            condition = event.get('condition', {})
            if condition:
                restriction = condition.get('restriction', {})
                if restriction:
                    route_group_info['condition'] = {
                        'comparator': restriction.get('comparator'),
                        'rhs': restriction.get('rhs')
                    }
                    lhs = restriction.get('lhs')
                    if lhs:
                        route_group_info['condition']['lhs'] = lhs
            
            # 提取 transitionEventHandler
            handler = event.get('transitionEventHandler', {})
            if handler:
                route_group_info['transitionEventHandler'] = {
                    'beforeTransition': handler.get('beforeTransition', {}),
                    'targetPageId': handler.get('targetPageId'),
                    'targetFlowId': handler.get('targetFlowId')
                }
            
            # 提取 conditionString
            condition_string = event.get('conditionString')
            if condition_string:
                route_group_info['conditionString'] = condition_string
            
            processed_route_groups.append(route_group_info)
        
        # 提取 slots（槽位信息，不区分语言）
        # slots 用于 page 层级的参数抽取，如 brscaccount 等
        page_slots = page_value.get('slots', [])
        processed_slots = []
        
        for slot in page_slots:
            slot_info = {
                'displayName': slot.get('displayName', ''),
                'mode': slot.get('mode', ''),  # REQUIRED, OPTIONAL 等
            }
            
            # 提取 type 信息
            slot_type = slot.get('type', {})
            if slot_type:
                slot_info['type'] = {
                    'className': slot_type.get('className', ''),
                    'classType': slot_type.get('classType', ''),  # ENUMERATION, BUILT_IN_CLASS 等
                    'enumerationId': slot_type.get('enumerationId', '')
                }
            
            # 提取 fillBehavior（可选，包含 reprompt 信息）
            fill_behavior = slot.get('fillBehavior', {})
            if fill_behavior:
                slot_info['fillBehavior'] = fill_behavior
            
            processed_slots.append(slot_info)
        
        # 为每个语言版本创建page对象
        for lang in ['en', 'zh', 'zh-hant']:
            page_obj = {
                'pageId': page_key,
                'displayName': display_name
            }
            
            # 添加 onLoad 信息
            on_load_info = {}
            
            # 添加 responses
            if lang_responses[lang]:
                on_load_info['responses'] = lang_responses[lang]
                stats[lang]['responses'] += len(lang_responses[lang])
            
            # 添加 function（不区分语言）
            if function_info:
                on_load_info['function'] = function_info
            
            # 添加 setParameterActions（不区分语言）
            if set_parameter_actions:
                on_load_info['setParameterActions'] = set_parameter_actions
            
            if on_load_info:
                page_obj['onLoad'] = on_load_info
            
            # 添加 transitionEvents（不区分语言）
            if processed_transitions:
                page_obj['transitionEvents'] = processed_transitions
                stats[lang]['transitions'] += len(processed_transitions)
            
            # 添加 slots（不区分语言）
            if processed_slots:
                page_obj['slots'] = processed_slots
                stats[lang]['slots'] += len(processed_slots)
            
            # 添加 routeGroupsTransitionEvents（不区分语言）
            if processed_route_groups:
                page_obj['routeGroupsTransitionEvents'] = processed_route_groups
            
            # 只有当page有实际内容时才添加
            if 'onLoad' in page_obj or 'transitionEvents' in page_obj or 'slots' in page_obj or 'routeGroupsTransitionEvents' in page_obj:
                lang_data[lang]['pages'].append(page_obj)
                stats[lang]['pages'] += 1
    
    # 保存三个语言版本的文件
    output_files = {
        'en': 'fulfillments_en.json',
        'zh': 'fulfillments_zh.json',
        'zh-hant': 'fulfillments_zh-hant.json'
    }
    
    for lang, filename in output_files.items():
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(lang_data[lang], f, ensure_ascii=False, indent=2)
    
    logger.info(f'✅ Fulfillments 处理完成: en={stats["en"]["pages"]} pages ({stats["en"]["slots"]} slots), zh={stats["zh"]["pages"]} pages ({stats["zh"]["slots"]} slots), zh-hant={stats["zh-hant"]["pages"]} pages ({stats["zh-hant"]["slots"]} slots)')


def extract_intent_parameters(input_file: str = 'intents.json', output_file: str = 'intent_parameters.json'):
    """
    从intents.json文件中提取intent的parameters信息
    
    提取内容：
    1. intent的id和displayName
    2. intent的parameters（如果有）
    3. 区分有parameters和没有parameters的intents
    
    Args:
        input_file: 输入的intents.json文件路径
        output_file: 输出的parameters配置文件路径
    
    输出:
        - intent_parameters.json: 包含parameters映射信息
    """
    logger.info('Step 1.4: 提取 Intent Parameters')
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    result = {
        'intentsWithParameters': [],
        'intentsWithoutParameters': [],
        'summary': {
            'totalIntents': 0,
            'withParameters': 0,
            'withoutParameters': 0,
            'totalParameters': 0
        }
    }
    
    # 遍历每个intent
    total_intents = len(data.get('intents', []))
    logger.info(f'正在处理 {total_intents} 个intents...')
    
    for idx, intent in enumerate(data.get('intents', []), 1):
        meta = intent.get('meta', {})
        # parameters在meta里面，而不是在intent的顶层
        parameters = meta.get('parameters', [])
        
        intent_id = meta.get('id')
        display_name = meta.get('displayName')
        
        # 基本信息
        intent_info = {
            'id': intent_id,
            'displayName': display_name
        }
        
        # 检查是否有parameters
        if parameters:
            # 处理parameters信息
            processed_parameters = []
            for param in parameters:
                # writed by senlin.deng 2026-01-12
                # 统一将value转换为小写，使得code节点兼容googledialogflow的大小写不敏感的语法
                param_info = {
                    'id': param.get('id').lower(),
                    'displayName': param.get('displayName'),
                    'value': param.get('value'),
                    'entityTypeDisplayName': param.get('entityTypeDisplayName'),
                    'redact': param.get('redact', False)
                }
                processed_parameters.append(param_info)
            
            intent_info['parameters'] = processed_parameters
            intent_info['parameterCount'] = len(processed_parameters)
            result['intentsWithParameters'].append(intent_info)
            result['summary']['withParameters'] += 1
            result['summary']['totalParameters'] += len(processed_parameters)
        else:
            result['intentsWithoutParameters'].append(intent_info)
            result['summary']['withoutParameters'] += 1
        
        result['summary']['totalIntents'] += 1
    
    # 保存结果
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    logger.info(f'✅ Intent Parameters: {result["summary"]["withParameters"]} 有参数, {result["summary"]["withoutParameters"]} 无参数')


def extract_flow_configs(input_files: List[str] = None, output_file: str = 'flow_configs.json'):
    """
    从exported_flow_*.json文件中提取flow配置信息
    
    提取内容：
    1. 主flow的 flowId 和 displayName
    2. flows数组中每个flow的 key 和 displayName
    
    Args:
        input_files: 输入的flow文件列表，如果为None则自动搜索所有exported_flow_*.json
        output_file: 输出的flow配置文件路径
    
    输出:
        - flow_configs.json: 包含所有flow配置信息
    """
    logger.info('Step 1.5: 提取 Flow 配置')
    
    # 如果没有指定文件列表，自动搜索
    if input_files is None:
        import glob
        input_files = glob.glob('exported_flow_*.json')
    
    if not input_files:
        logger.error('错误：没有找到exported_flow_*.json文件')
        return
    
    all_flows = {
        'mainFlows': [],
        'subFlows': []
    }
    
    for file_path in input_files:
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 提取主flow信息
            main_flow = data.get('flow', {}).get('flow', {})
            if main_flow:
                flow_info = {
                    'flowId': main_flow.get('flowId'),
                    'displayName': main_flow.get('displayName'),
                    'sourceFile': os.path.basename(file_path)
                }
                all_flows['mainFlows'].append(flow_info)
            
            # 提取子flows信息
            sub_flows = data.get('flow', {}).get('flows', [])
            if sub_flows:
                for sub_flow in sub_flows:
                    flow_key = sub_flow.get('key')
                    flow_value = sub_flow.get('value', {})
                    sub_flow_info = {
                        'flowKey': flow_key,
                        'displayName': flow_value.get('displayName'),
                        'sourceFile': os.path.basename(file_path),
                        'parentFlow': flow_info.get('displayName')
                    }
                    all_flows['subFlows'].append(sub_flow_info)
        
        except Exception as e:
            logger.error(f'处理文件失败 {file_path}: {str(e)}')
            continue
    
    # 保存结果
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_flows, f, ensure_ascii=False, indent=2)
    
    logger.info(f'✅ Flow 配置: {len(all_flows["mainFlows"])} 主flows, {len(all_flows["subFlows"])} 子flows')


def extract_webhooks(input_files: List[str] = None, output_file: str = 'webhooks.json'):
    """
    从exported_flow_*.json文件中提取webhook配置信息
    
    提取内容：
    1. webhook的 key (webhookFulfillmentId)
    2. webhook的 name, displayName
    3. webhook的 genericWebService.uri
    4. webhook的 timeout
    
    Args:
        input_files: 输入的flow文件列表，如果为None则自动搜索所有exported_flow_*.json
        output_file: 输出的webhook配置文件路径
    
    输出:
        - webhooks.json: 包含所有webhook配置信息
    """
    logger.info('Step 1.6: 提取 Webhooks 配置')
    
    # 如果没有指定文件列表，自动搜索
    if input_files is None:
        import glob
        input_files = glob.glob('exported_flow_*.json')
    
    if not input_files:
        logger.error('没有找到exported_flow_*.json文件')
        return
    
    all_webhooks = {
        'webhooks': []
    }
    
    # 用于去重
    webhook_keys = set()
    
    for file_path in input_files:
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 提取webhooks信息
            webhooks = data.get('flow', {}).get('webhooks', [])
            
            if webhooks:
                for webhook in webhooks:
                    webhook_key = webhook.get('key')
                    
                    # 去重：如果这个webhook已经被处理过，跳过
                    if webhook_key in webhook_keys:
                        continue
                    
                    webhook_keys.add(webhook_key)
                    
                    webhook_value = webhook.get('value', {})
                    generic_web_service = webhook_value.get('genericWebService', {})
                    timeout = webhook_value.get('timeout', {})
                    
                    webhook_info = {
                        'webhookId': webhook_key,
                        'name': webhook_value.get('name'),
                        'displayName': webhook_value.get('displayName'),
                        'uri': generic_web_service.get('uri'),
                        'webhookType': generic_web_service.get('webhookType'),
                        'timeoutSeconds': timeout.get('seconds'),
                        'sourceFiles': [os.path.basename(file_path)]
                    }
                    
                    all_webhooks['webhooks'].append(webhook_info)
        
        except Exception as e:
            logger.error(f'处理文件失败 {file_path}: {str(e)}')
            continue
    
    # 保存结果
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_webhooks, f, ensure_ascii=False, indent=2)
    
    logger.info(f'✅ Webhooks 配置: {len(all_webhooks["webhooks"])} 个webhooks')


def process_all(
    entities_file: str = 'entities.json',
    intents_file: str = 'intents.json',
    fulfillments_file: str = 'fulfillments.json',
    flow_files: List[str] = None
):
    """
    一次性运行所有6个处理函数
    
    Args:
        entities_file: entities.json文件路径
        intents_file: intents.json文件路径
        fulfillments_file: fulfillments.json文件路径
        flow_files: flow文件列表，如果为None则自动搜索
    """
    logger.info('Step 1: Dialogflow CX 数据处理')
    
    try:
        # 1. 处理 entities
        if os.path.exists(entities_file):
            process_entities_by_language(entities_file)
        else:
            logger.warning(f'未找到 {entities_file}')
        
        # 2. 处理 intents
        if os.path.exists(intents_file):
            process_intents_by_language(intents_file)
        else:
            logger.warning(f'未找到 {intents_file}')
        
        # 3. 处理 fulfillments
        if os.path.exists(fulfillments_file):
            process_fulfillments_by_language(fulfillments_file)
        else:
            logger.warning(f'未找到 {fulfillments_file}')
        
        # 4. 提取 intent parameters
        if os.path.exists(intents_file):
            extract_intent_parameters(intents_file)
        else:
            logger.warning(f'未找到 {intents_file}')
        
        # 5. 提取 flow 配置
        extract_flow_configs(flow_files)
        
        # 6. 提取 webhooks
        extract_webhooks(flow_files)
        
        logger.info('✅ Step 1 所有处理完成')
        
    except Exception as e:
        logger.error(f'处理过程中出现错误: {str(e)}', exc_info=True)


if __name__ == '__main__':
    # 运行所有处理函数
    process_all()

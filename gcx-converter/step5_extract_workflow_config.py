"""
工作流配置提取工具
从指定的 exported_flow_*.json 文件中提取 flow 配置信息，生成 workflow_config.json
用户可以指定要处理的文件名

作者：chenyu.zhu
日期：2025-12-17
"""

import json
import os

from logger_config import get_logger
logger = get_logger(__name__)


def extract_workflow_config_from_flow(flow_data: dict, flow_file_name: str = "") -> dict:
    """
    从单个 flow 数据中提取工作流配置
    
    Args:
        flow_data: flow 配置数据
        flow_file_name: flow 文件名（可选）
        
    Returns:
        工作流配置字典
    """
    # 提取 flow.flow 中的信息
    flow_info = flow_data.get('flow', {}).get('flow', {})
    
    # 获取 displayName
    display_name = flow_info.get('displayName', 'Unnamed Workflow')
    
    # 获取 flowId
    flow_id = flow_info.get('flowId', '')
    
    # 获取 description（如果有的话）
    description = flow_info.get('description', f'{display_name} 工作流')
    
    # 生成工作流配置
    workflow_config = {
        "workflow_name": display_name,
        "workflow_info": {
            "description": description,
            "created_by": "HSBC",  # 固定为 HSBC
            "intent_description": f"Professional {display_name} assistant"
        }
    }
    
    # 如果有 flowId，添加到 workflow_info 中
    if flow_id:
        workflow_config["workflow_info"]["flow_id"] = flow_id
    
    # 如果有文件名，添加来源信息
    if flow_file_name:
        workflow_config["workflow_info"]["source_file"] = flow_file_name
    
    return workflow_config


def extract_single_workflow_config(
    flow_file: str = 'input/exported_flow_TXNAndSTMT_Deeplink.json',
    output_file: str = 'workflow_config.json'
):
    """
    从单个 flow 文件中提取工作流配置
    
    Args:
        flow_file: 输入的 flow 文件路径
        output_file: 输出的配置文件路径
    """
    logger.info(f'Step 5: 工作流配置提取 - {flow_file}')
    
    # 检查文件是否存在
    if not os.path.exists(flow_file):
        logger.error(f'Error: Input file {flow_file} not found')
        return
    
    # 读取 flow 文件
    logger.debug(f'Reading {flow_file}...')
    
    try:
        with open(flow_file, 'r', encoding='utf-8') as f:
            flow_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f'Error: Invalid JSON format - {e}')
        return
    except Exception as e:
        logger.error(f'Error: Failed to read file - {e}')
        return
    
    # 提取配置
    logger.debug('Extracting workflow configuration...')
    
    config = extract_workflow_config_from_flow(
        flow_data,
        os.path.basename(flow_file)
    )
    
    logger.info(f'Successfully extracted: {config["workflow_name"]}')
    
    # 保存结果
    logger.debug(f'Saving to {output_file}...')
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        
        logger.info(f'✅ Successfully generated {output_file}')
        
    except Exception as e:
        logger.error(f'[Error] Failed to save file: {e}')
        return
    
    # 显示结果
    logger.debug('\n' + '='*60)
    logger.debug('Workflow Configuration:')
    logger.debug('='*60)
    logger.debug(f'Name: {config["workflow_name"]}')
    logger.debug(f'Description: {config["workflow_info"]["description"]}')
    logger.debug(f'Created by: {config["workflow_info"]["created_by"]}')
    logger.debug(f'Intent Description: {config["workflow_info"]["intent_description"]}')
    
    if "flow_id" in config["workflow_info"]:
        logger.debug(f'Flow ID: {config["workflow_info"]["flow_id"]}')
    
    if "source_file" in config["workflow_info"]:
        logger.debug(f'Source File: {config["workflow_info"]["source_file"]}')
    
    logger.info('\n' + '='*60)
    logger.info('Processing completed!')
    logger.info('='*60)


def main():
    """
    主函数 - 根据用户输入或命令行参数提取工作流配置
    """
    import sys
    
    # 检查命令行参数
    if len(sys.argv) > 1:
        if sys.argv[1] == '--help' or sys.argv[1] == '-h':
            logger.info('='*60)
            logger.info('Workflow Config Extraction Tool - 使用说明')
            logger.info('='*60)
            logger.info('\n用法:')
            logger.info('  python extract_workflow_config.py')
            logger.info('      → 默认处理 exported_flow_TXNAndSTMT_Deeplink.json')
            logger.info('')
            logger.info('  python extract_workflow_config.py <input_file.json>')
            logger.info('      → 处理指定的文件')
            logger.info('')
            logger.info('  python extract_workflow_config.py <input_file.json> <output_file.json>')
            logger.info('      → 处理指定的文件并指定输出文件名')
            logger.info('')
            logger.info('示例:')
            logger.info('  python extract_workflow_config.py exported_flow_Common_EAT.json')
            logger.info('  python extract_workflow_config.py exported_flow_Greetings.json my_config.json')
            logger.info('='*60)
            return
        
        # 处理指定文件
        input_file = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else 'workflow_config.json'
        extract_single_workflow_config(input_file, output_file)
    else:
        # 默认处理 TXNAndSTMT_Deeplink.json
        extract_single_workflow_config(
            flow_file='exported_flow_TXNAndSTMT_Deeplink.json',
            output_file='workflow_config.json'
        )


if __name__ == '__main__':
    main()

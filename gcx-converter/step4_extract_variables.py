"""
变量提取工具 - Multi-Workflow 版本
从 nodes_config_{name}.json 中提取所有使用的变量，生成 variables_{name}.json 配置文件
支持处理多个独立的 workflow 文件

作者：chenyu.zhu
日期：2025-12-17
"""

import json
import re
import os

from logger_config import get_logger
logger = get_logger(__name__)


def extract_var_from_sys_func_expression(expr):
    """
    从 sys_func_* 表达式中提取变量名
    
    示例:
    - sys_func_TO_OBJECT($session_params_CCR_DebitAccountObject_id) -> CCR_DebitAccountObject_id
    - sys_func_IF("$session_params_currency=null", "", $session_params_currency) -> currency
    - sys_func_IF("$session_params_AVAILABLECREDITLIMIT=null", "N/A", $session_params_AVAILABLECREDITLIMIT) -> AVAILABLECREDITLIMIT
    
    Args:
        expr: 表达式字符串
        
    Returns:
        提取的变量名列表，如果无法提取则返回空列表
    """
    if not isinstance(expr, str):
        return []
    
    import re
    var_names = []
    
    # 匹配 sys_func_TO_OBJECT($session_params_xxx) 或 sys_func_TO_OBJECT($xxx)
    # 提取括号内的变量引用
    to_object_pattern = r'sys_func_TO_OBJECT\(\$([^)]+)\)'
    matches = re.findall(to_object_pattern, expr)
    for match in matches:
        # 从 $session_params_xxx 中提取最后的变量名部分
        # 例如: session_params_CCR_DebitAccountObject_id -> CCR_DebitAccountObject_id
        if match.startswith('session_params_'):
            var_name = match.replace('session_params_', '')
        else:
            var_name = match.split('_')[-1] if '_' in match else match
        if var_name:
            var_names.append(var_name)
    
    # 匹配 sys_func_IF(...) 表达式
    # 提取最后一个参数中的变量引用
    # 注意：表达式可能包含转义的引号 \"
    # 例如: sys_func_IF("$session_params_currency=null", "", $session_params_currency)
    if_pattern = r'sys_func_IF\([^,]+,\s*[^,]+,\s*\$([^)]+)\)'
    matches = re.findall(if_pattern, expr)
    for match in matches:
        # 从 $session_params_xxx 中提取最后的变量名部分
        # 例如: session_params_currency -> currency
        if match.startswith('session_params_'):
            var_name = match.replace('session_params_', '')
        else:
            var_name = match.split('_')[-1] if '_' in match else match
        if var_name:
            var_names.append(var_name)
    
    # 如果没匹配到，尝试更通用的模式：查找所有 $session_params_xxx 或 $xxx 模式
    if not var_names:
        var_ref_pattern = r'\$session_params_([a-zA-Z0-9_]+)|\$([a-zA-Z0-9_]+)'
        matches = re.findall(var_ref_pattern, expr)
        for match in matches:
            var_name = match[0] if match[0] else match[1]
            if var_name:
                # 如果是以 session_params_ 开头的，提取后面的部分
                if var_name.startswith('session_params_'):
                    var_name = var_name.replace('session_params_', '')
                var_names.append(var_name)
    
    # 去重并返回
    return list(set(var_names))


def extract_variables_from_nodes(nodes_data, language="en"):
    """
    从 nodes_data 列表中提取所有变量，返回 {variables: {...}} 结构
    
    Args:
        nodes_data: 节点列表
        language: 语言代码，用于设置变量的lang字段
        
    Returns:
        包含所有变量定义的字典
    """
    # 默认变量中文描述
    default_descriptions = {
        "last_user_response": "用户最新的回复内容",
        "response": "系统回复给用户的内容",
        "user_input": "用户输入内容",
        "all_response": "所有用户回复的历史记录",
        "last_response": "系统最后一次的回复"
    }

    all_vars = set()
    for node in nodes_data:
        # variable_assign
        if "variable_assign" in node and node["variable_assign"] is not None:
            all_vars.add(node["variable_assign"])
        # outputs
        if "outputs" in node and isinstance(node["outputs"], list):
            # 过滤掉 None 值，并处理两种格式：
            # 1. 字符串列表：["var1", "var2"]
            # 2. dict 列表：[{"name": "var1", "type": "string", "variable_assign": "var1"}, ...]
            for output in node["outputs"]:
                if output is None:
                    continue
                if isinstance(output, str):
                    all_vars.add(output)
                elif isinstance(output, dict):
                    # 从 dict 中提取变量名：优先使用 variable_assign，其次使用 name
                    var_name = output.get("variable_assign") or output.get("name")
                    if var_name and isinstance(var_name, str):
                        all_vars.add(var_name)
        # args
        if "args" in node and isinstance(node["args"], list):
            # 处理每个 arg，支持两种格式：
            # 1. 字符串：直接作为变量名
            # 2. dict：从 dict 中提取 name 或 default_value 字段
            for arg in node["args"]:
                if arg is None:
                    continue
                if isinstance(arg, str):
                    if arg.startswith('sys_func_'):
                        # 从 sys_func_* 表达式中提取变量名
                        extracted_vars = extract_var_from_sys_func_expression(arg)
                        if extracted_vars:
                            all_vars.update(extracted_vars)
                        # 不添加整个表达式作为变量名
                    else:
                        # 普通变量名，直接添加
                        all_vars.add(arg)
                elif isinstance(arg, dict):
                    # 从 dict 中提取变量名（如 Semantic NER 生成的 code 节点）
                    var_name = arg.get("name")
                    if var_name and isinstance(var_name, str):
                        all_vars.add(var_name)
        # prompt_template 里的 {{xxx}}
        if "prompt_template" in node and node["prompt_template"] is not None:
            if isinstance(node["prompt_template"], str):
                matches = re.findall(r"\{\{(\w+)\}\}", node["prompt_template"])
                # 过滤掉 None 值和空字符串
                valid_matches = [m for m in matches if m is not None and m != ""]
                all_vars.update(valid_matches)
        # plain_text 里的 {{xxx}}
        if "plain_text" in node and isinstance(node["plain_text"], list):
            for t in node["plain_text"]:
                if isinstance(t, dict) and "text" in t:
                    # text 可能是字符串或 dict
                    text_content = t["text"]
                    if isinstance(text_content, dict) and "text" in text_content:
                        # text 是 dict，提取嵌套的 text 字段
                        text_content = text_content["text"]
                    if isinstance(text_content, str):
                        matches = re.findall(r"\{\{(\w+)\}\}", text_content)
                        # 过滤掉 None 值
                        valid_matches = [m for m in matches if m is not None]
                        all_vars.update(valid_matches)
        # condition 变量
        if "if_else_conditions" in node:
            for cond in node["if_else_conditions"]:
                if "conditions" in cond:
                    for c in cond["conditions"]:
                        if "condition_variable" in c and c["condition_variable"] is not None:
                            all_vars.add(c["condition_variable"])

    # 过滤掉 None 值和空字符串，然后组装最终输出
    all_vars = {v for v in all_vars if v is not None and v != ""}

    # writed by senlin.deng 2026-01-14
    # 将有小写的变量去除，只保留小写；没有的先保留
    final_var = set()
    for v in all_vars:
        if v.lower() in all_vars and v!=v.lower():
            pass
        else:
            final_var.add(v)
    all_vars = final_var
    # logger.info(f'all_vars: {all_vars}')
    # logger.info(f'final_var: {final_var}')
    # exit()


    variables = {}
    for var_name in sorted(all_vars):
        # writed by senlin.deng 2026-01-14
        # 将变量名全部小写，除LLM_response和KB_response外
        process_var = var_name
        if var_name not in ['LLM_response', 'KB_response']:
            process_var = var_name.lower()
        variables[process_var] = {
            "type": "text",
            "description": default_descriptions.get(process_var, f"{process_var} 变量数据"),
            "lang": language
        }
    return {"variables": variables}


def process_nodes_config(
    input_file: str = 'nodes_config.json',
    output_file: str = 'variables.json',
    language: str = 'en'
):
    """
    处理 nodes_config.json 文件，提取变量并保存到 variables.json
    
    Args:
        input_file: 输入的 nodes 配置文件路径
        output_file: 输出的 variables 配置文件路径
    """
    logger.info(f'Step 4: 变量提取 - {input_file}')
    
    # 检查输入文件是否存在
    if not os.path.exists(input_file):
        logger.error(f'Input file {input_file} not found')
        return
    
    # 读取 nodes 配置文件
    logger.debug(f'Reading {input_file}...')
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f'Error: Invalid JSON format - {e}')
        return
    except Exception as e:
        logger.error(f'Error: Failed to read file - {e}')
        return
    
    # 获取 nodes 列表
    nodes_data = data.get('nodes', [])
    
    if not nodes_data:
        logger.warning('Warning: No nodes found in nodes_config.json')
        return
    
    logger.debug(f'Found {len(nodes_data)} nodes')
    
    # 提取变量
    logger.debug('Extracting variables...')
    result = extract_variables_from_nodes(nodes_data, language)
    
    variable_count = len(result.get('variables', {}))
    logger.debug(f'Successfully extracted {variable_count} variables')
    
    # 保存结果
    logger.debug(f'Saving to {output_file}...')
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f'✅ Successfully generated {output_file}')
    except Exception as e:
        logger.error(f'Error: Failed to save file - {e}')
        return
    
    # 显示部分变量示例
    if variable_count > 0:
        logger.debug('='*60)
        logger.debug('Variable Examples (first 10):')
        logger.debug('='*60)
        variables = result.get('variables', {})
        for idx, (var_name, var_info) in enumerate(list(variables.items())[:10], 1):
            logger.debug(f'{idx}. {var_name}')
            logger.debug(f'   Type: {var_info.get("type")}')
            logger.debug(f'   Description: {var_info.get("description")}')
    
    logger.info('\n' + '='*60)
    logger.info('Processing completed!')
    logger.info('='*60)


def process_multiple_workflows(workflow_list_file: str = 'generated_workflows.json'):
    """
    处理多个 workflow 的变量提取
    
    Args:
        workflow_list_file: 包含 workflow 名称列表的 JSON 文件
    """
    logger.info('='*60)
    logger.info('🔄 Multi-Workflow Variable Extraction Tool')
    logger.info('='*60)
    
    # 1. 读取 workflow 列表
    if not os.path.exists(workflow_list_file):
        logger.warning(f'⚠️  Warning: {workflow_list_file} not found')
        logger.debug('   Falling back to single workflow mode...')
        process_nodes_config('nodes_config.json', 'variables.json')
        return
    
    with open(workflow_list_file, 'r', encoding='utf-8') as f:
        workflow_data = json.load(f)
    
    workflows = workflow_data.get('workflows', [])
    
    if not workflows:
        logger.warning(f'⚠️  No workflows found in {workflow_list_file}')
        return
    
    logger.debug(f'📊 Found {len(workflows)} workflows to process')
    logger.info('='*60)
    
    # 2. 为每个 workflow 提取变量
    success_count = 0
    failed_count = 0
    
    for idx, workflow_name in enumerate(workflows, 1):
        logger.info(f'\n[{idx}/{len(workflows)}] Processing workflow: {workflow_name}')
        logger.info('-'*60)
        
        # 从 workflow_list_file 路径中提取语言
        # 例如: output/step2_workflow_config/zh-hant/generated_workflows.json -> zh-hant
        language = 'en'  # 默认值
        workflow_list_dir = os.path.dirname(workflow_list_file)
        if 'step2_workflow_config' in workflow_list_dir:
            parts = workflow_list_dir.split('step2_workflow_config')
            if len(parts) > 1:
                lang_part = parts[1].lstrip(os.sep).split(os.sep)[0]
                if lang_part in ['en', 'zh', 'zh-hant']:
                    language = lang_part
                elif lang_part == 'zh-cn':
                    language = 'zh'  # zh-cn目录当作zh处理

        input_file = f'output/step2_workflow_config/{language}/nodes_config_{workflow_name}.json'
        output_file = f'output/step4_variables/{language}/variables_{workflow_name}.json'
        
        if not os.path.exists(input_file):
            logger.error(f'   ❌ Error: {input_file} not found, skipping...')
            failed_count += 1
            continue
        
        try:
            # 读取 nodes 配置
            with open(input_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            nodes_data = data.get('nodes', [])
            
            if not nodes_data:
                logger.warning(f'   ⚠️  Warning: No nodes found in {input_file}')
                continue
            
            logger.debug(f'   Found {len(nodes_data)} nodes')
            
            # 提取变量
            result = extract_variables_from_nodes(nodes_data, language)
            variable_count = len(result.get('variables', {}))
            
            # 确保输出目录存在
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            # 保存结果
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            
            logger.info(f'   ✅ Generated: {output_file}')
            success_count += 1
            
        except Exception as e:
            logger.error(f'   ❌ Error processing {workflow_name}: {e}')
            failed_count += 1
            import traceback
            logger.error(traceback.format_exc())
    
    # 3. 总结
    logger.info('\n' + '='*60)
    logger.info('✅ Multi-Workflow Variable Extraction Completed!')
    logger.info('='*60)
    logger.info(f'Total workflows: {len(workflows)}')
    logger.info(f'Successfully processed: {success_count}')
    logger.info(f'Failed: {failed_count}')
    logger.info('='*60)


def main():
    """
    Main function - 支持多个 workflow
    """
    # 处理不同语言的多个 workflows
    import glob

    # 查找所有语言的generated_workflows.json文件
    workflow_files = glob.glob('output/step2_workflow_config/*/generated_workflows.json')

    if workflow_files:
        for workflow_file in workflow_files:
            logger.info(f'Processing workflows from: {workflow_file}')
            process_multiple_workflows(workflow_file)
    else:
        # 尝试处理单个文件
        process_multiple_workflows('output/step2_workflow_config/generated_workflows.json')


if __name__ == '__main__':
    main()

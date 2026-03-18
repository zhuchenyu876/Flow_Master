"""
一键运行所有步骤的 API 接口版本
================================
本脚本提供一个函数接口，支持：
- 传入文件路径
- 传入 agent 名称和描述
- 选择生成的语言（只生成一种语言的 agent 和 flow）
- 可选：上传对应语言的知识库

使用方法：
    from run_all_steps_api import run_all_steps
    
    result = run_all_steps(
        exported_flow_file="input/exported_flow_xxx.json",
        agent_name="My Agent",
        agent_description="Agent description",
        language="en",  # 只生成一种语言: "en", "zh", "zh-hant"
        create_kb=False,  # 是否创建知识库（需配置平台凭证）
    )
    # 最终输出文件位于 output/step8_final/，可手动导入目标平台

作者：chenyu.zhu
日期：2025-12-17
"""

import json
import os
import sys
import shutil
import glob
from datetime import datetime
from typing import Optional, Dict, Any, List

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# 导入日志配置
from logger_config import setup_file_logger, get_current_log_file, enable_print_capture, disable_print_capture


class RunAllStepsResult:
    """运行结果"""
    def __init__(self):
        self.success = False
        self.message = ""
        self.steps_completed = []
        self.generated_files = {}
        self.errors = []
        self.output_dir = ""
        self.final_agent_file = ""
        self.upload_result = None
        self.duration_seconds = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "steps_completed": self.steps_completed,
            "generated_files": self.generated_files,
            "errors": self.errors,
            "output_dir": self.output_dir,
            "final_agent_file": self.final_agent_file,
            "upload_result": self.upload_result,
            "duration_seconds": self.duration_seconds
        }


def run_all_steps(
    exported_flow_file: str,
    agent_name: str = "Migrated Agent",
    agent_description: str = "Agent migrated from Dialogflow CX",
    language: str = "en",
    create_kb: bool = False,
    robot_key: str = "",
    robot_token: str = "",
    username: str = "",
    base_url: str = "",
    output_base_dir: str = "output",
    dry_run: bool = False,
    verbose: bool = True
) -> RunAllStepsResult:
    """
    一键运行所有步骤的 API 接口
    
    Args:
        exported_flow_file: Dialogflow CX 导出的 flow JSON 文件路径
        agent_name: 生成的 Agent 名称
        agent_description: Agent 描述
        language: 目标语言，支持 "en", "zh", "zh-hant"（只生成一种语言）
        create_kb: 是否创建知识库（需配置 robot_key/token）
        robot_key: 平台 API Robot Key（可选，create_kb=True 时使用）
        robot_token: 平台 API Robot Token（可选，create_kb=True 时使用）
        username: 平台用户名（可选，create_kb=True 时使用）
        base_url: 平台 API 基础 URL（可选，create_kb=True 时使用）
        output_base_dir: 输出目录基础路径
        dry_run: 如果为 True，只检查不执行
        verbose: 是否打印详细日志
        
    Returns:
        RunAllStepsResult: 运行结果对象（最终文件位于 output/step8_final/）
    """
    result = RunAllStepsResult()
    start_time = datetime.now()
    
    # 设置日志文件
    log_file_path = setup_file_logger()
    if verbose:
        # 启用 print 捕获，所有输出同时写入日志文件
        enable_print_capture(log_file_path)
    
    # 验证语言参数
    supported_languages = ['en', 'zh', 'zh-hant']
    if language not in supported_languages:
        result.success = False
        result.message = f"不支持的语言: {language}。支持的语言: {', '.join(supported_languages)}"
        result.errors.append(result.message)
        return result
    
    # 验证文件存在
    if not os.path.exists(exported_flow_file):
        result.success = False
        result.message = f"文件不存在: {exported_flow_file}"
        result.errors.append(result.message)
        return result
    
    # 如果启用知识库创建，验证 API 配置
    if create_kb and (not robot_key or not robot_token or not username):
        result.success = False
        result.message = "create_kb=True 时需要提供 robot_key, robot_token, username"
        result.errors.append(result.message)
        return result
    
    def log(msg):
        if verbose:
            print(msg)
    
    def log_header(step_number, step_name):
        log('\n' + '='*70)
        log(f'Step {step_number}: {step_name}')
        log('='*70 + '\n')
    
    log('\n' + '='*70)
    log('🚀 Dialogflow CX 工作流转换 - API 模式')
    log('='*70)
    log(f'开始时间: {start_time.strftime("%Y-%m-%d %H:%M:%S")}')
    log(f'目标语言: {language}')
    log(f'Agent 名称: {agent_name}')
    log(f'创建知识库: {"是" if create_kb else "否"}')
    log(f'输出目录: {output_base_dir}')
    log('='*70)
    
    if dry_run:
        log('\n⚠️  DRY RUN 模式：只检查，不执行实际操作')
    
    # 保存初始工作目录
    initial_dir = os.getcwd()
    
    # 创建输出目录结构
    output_dir = output_base_dir
    result.output_dir = output_dir
    
    step_dirs = {
        0: os.path.join(output_dir, 'step0_extracted'),
        1: os.path.join(output_dir, 'step1_processed'),
        2: os.path.join(output_dir, 'step2_workflow_config', language),
        3: os.path.join(output_dir, 'qa_knowledge_bases'),
        4: os.path.join(output_dir, 'step4_variables', language),
        5: os.path.join(output_dir, 'step5_workflow_meta', language),
        6: os.path.join(output_dir, 'step6_final', language),
        7: os.path.join(output_dir, 'step7_final', language),
        8: os.path.join(output_dir, 'step8_final'),
    }
    
    # 创建所有目录
    for dir_path in step_dirs.values():
        os.makedirs(dir_path, exist_ok=True)
    
    log(f'\n📁 输出目录结构已创建: {output_dir}/')
    
    try:
        # ========================================================================
        # Step 0: 从 exported_flow 文件提取数据
        # ========================================================================
        log_header(0, '从 exported_flow 文件提取数据')
        
        from step0_extract_from_exported_flow import extract_from_exported_flow
        
        log(f'📁 使用 flow 文件: {exported_flow_file}\n')
        
        step0_entities = os.path.join(step_dirs[0], 'entities.json')
        step0_intents = os.path.join(step_dirs[0], 'intents.json')
        step0_fulfillments = os.path.join(step_dirs[0], 'fulfillments.json')
        
        if not dry_run:
            success = extract_from_exported_flow(
                exported_flow_file=exported_flow_file,
                output_entities=step0_entities,
                output_intents=step0_intents,
                output_fulfillments=step0_fulfillments
            )
            
            if not success:
                result.errors.append('Step 0 失败，无法提取数据')
                result.message = 'Step 0 失败'
                return result
        
        result.steps_completed.append('step0')
        result.generated_files['step0'] = [step0_entities, step0_intents, step0_fulfillments]
        log('✅ Step 0 完成')
        
        # ========================================================================
        # Step 1: 处理 Dialogflow CX 数据（只处理指定语言）
        # ========================================================================
        log_header(1, f'处理 Dialogflow CX 数据（语言: {language}）')
        
        from step1_process_dialogflow_data import (
            process_entities_by_language,
            process_intents_by_language,
            process_fulfillments_by_language,
            extract_intent_parameters,
            extract_flow_configs,
            extract_webhooks
        )
        
        entities_file = step0_entities
        intents_file = step0_intents
        fulfillments_file = step0_fulfillments
        
        step1_output_files = []
        
        if not dry_run:
            # 处理 entities
            if os.path.exists(entities_file):
                process_entities_by_language(entities_file)
            
            # 处理 intents
            if os.path.exists(intents_file):
                process_intents_by_language(intents_file)
            
            # 处理 fulfillments
            if os.path.exists(fulfillments_file):
                process_fulfillments_by_language(fulfillments_file)
            
            # 提取 intent parameters
            if os.path.exists(intents_file):
                extract_intent_parameters(intents_file, os.path.join(step_dirs[1], 'intent_parameters.json'))
            
            # 提取 flow 配置
            extract_flow_configs()
            if os.path.exists('flow_configs.json'):
                shutil.move('flow_configs.json', os.path.join(step_dirs[1], 'flow_configs.json'))
            
            # 提取 webhooks
            extract_webhooks()
            if os.path.exists('webhooks.json'):
                shutil.move('webhooks.json', os.path.join(step_dirs[1], 'webhooks.json'))
            
            # 只移动指定语言的文件
            step1_files = [
                f'entities_{language}.json',
                f'intents_{language}.json',
                f'fulfillments_{language}.json'
            ]
            
            for file in step1_files:
                if os.path.exists(file):
                    output_path = os.path.join(step_dirs[1], file)
                    shutil.move(file, output_path)
                    step1_output_files.append(output_path)
            
            # 清理其他语言的文件（如果生成了的话）
            for lang in supported_languages:
                if lang != language:
                    for prefix in ['entities_', 'intents_', 'fulfillments_']:
                        temp_file = f'{prefix}{lang}.json'
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
        
        result.steps_completed.append('step1')
        result.generated_files['step1'] = step1_output_files
        log('✅ Step 1 完成')
        
        # ========================================================================
        # Step 2: 转换 fulfillments 为 workflow 配置
        # ========================================================================
        log_header(2, f'转换 fulfillments 为 workflow 配置（语言: {language}）')
        
        fulfillments_lang_file = os.path.join(step_dirs[1], f'fulfillments_{language}.json')
        intents_lang_file = os.path.join(step_dirs[1], f'intents_{language}.json')
        intent_params_file = os.path.join(step_dirs[1], 'intent_parameters.json')
        
        if not os.path.exists(fulfillments_lang_file):
            result.errors.append(f'找不到 {fulfillments_lang_file}')
            result.message = 'Step 2 失败'
            return result
        
        generated_workflows = []
        step2_files = []
        
        if not dry_run:
            # from step2_workflow_converter import WorkflowConverter
            from step2.converter import WorkflowConverter
            
            # 加载 intents 映射
            intents_mapping = {}
            if os.path.exists(intents_lang_file):
                with open(intents_lang_file, 'r', encoding='utf-8') as f:
                    intents_data = json.load(f)
                for intent in intents_data.get('intents', []):
                    intent_id = intent.get('id', '')
                    intent_name = intent.get('displayName', '')
                    if intent_id and intent_name:
                        intents_mapping[intent_id] = intent_name
            
            log(f'   Loaded {len(intents_mapping)} intent mappings for {language}')
            
            # 创建转换器
            converter = WorkflowConverter(
                intents_mapping=intents_mapping, 
                language=language,
                intent_recognition_version=2,
                )
            
            # 转换
            generated_workflows = converter.convert_to_multiple_workflows(
                fulfillments_file=fulfillments_lang_file,
                flow_file=exported_flow_file,
                lang=language,
                output_dir=step_dirs[2]
            )
            
            for wf_name in generated_workflows:
                step2_files.append(os.path.join(step_dirs[2], f'nodes_config_{wf_name}.json'))
                step2_files.append(os.path.join(step_dirs[2], f'edge_config_{wf_name}.json'))
            step2_files.append(os.path.join(step_dirs[2], 'generated_workflows.json'))
        
        result.steps_completed.append('step2')
        result.generated_files['step2'] = step2_files
        log(f'✅ Step 2 完成，生成了 {len(generated_workflows)} 个 workflow')
        
        # ========================================================================
        # Step 3: 创建知识库并更新 workflow 配置
        # ========================================================================
        log_header(3, '创建知识库并更新 workflow 配置')
        
        if not create_kb:
            log('   ⏭️  跳过知识库创建（create_kb = False）')
        else:
            if not dry_run:
                try:
                    # 设置环境变量供 step3 使用
                    os.environ['ROBOT_KEY'] = robot_key
                    os.environ['ROBOT_TOKEN'] = robot_token
                    os.environ['USERNAME'] = username
                    os.environ['STEP_3_LANGUAGE'] = language
                    os.environ['STEP_3_STEP_1_CREATE_KB'] = 'True'
                    os.environ['STEP_3_STEP_2_UPDATE_WORKFLOW'] = 'True'
                    
                    import step3_kb_workflow
                    # 重新加载环境变量
                    from importlib import reload
                    reload(step3_kb_workflow)
                    
                    # 注意：此模式不支持数据库，无法自动复用KB和写入映射
                    # 建议使用 run_all_steps_server.py (FastAPI) 以获得完整功能
                    success = step3_kb_workflow.main(task_id=None, db_session=None)
                    
                    if success:
                        result.steps_completed.append('step3')
                        log('✅ Step 3 完成')
                    else:
                        log('⚠️  Step 3 执行时遇到问题，继续后续步骤')
                except Exception as e:
                    log(f'❌ Step 3 错误: {e}')
                    result.errors.append(f'Step 3 错误: {str(e)}')
            else:
                result.steps_completed.append('step3')
        
        # ========================================================================
        # Step 4: 提取 variables
        # ========================================================================
        log_header(4, f'提取 variables（语言: {language}）')
        
        step4_files = []
        
        if not dry_run:
            from step4_extract_variables import extract_variables_from_nodes
            
            for wf_name in generated_workflows:
                nodes_file = os.path.join(step_dirs[2], f'nodes_config_{wf_name}.json')
                variables_file = os.path.join(step_dirs[4], f'variables_{wf_name}.json')
                
                if os.path.exists(nodes_file):
                    with open(nodes_file, 'r', encoding='utf-8') as f:
                        nodes_data = json.load(f)
                    
                    variables_data = extract_variables_from_nodes(nodes_data)
                    
                    with open(variables_file, 'w', encoding='utf-8') as f:
                        json.dump(variables_data, f, indent=2, ensure_ascii=False)
                    
                    step4_files.append(variables_file)
        
        result.steps_completed.append('step4')
        result.generated_files['step4'] = step4_files
        log(f'✅ Step 4 完成，生成了 {len(step4_files)} 个 variables 文件')
        
        # ========================================================================
        # Step 5: 提取 workflow 配置
        # ========================================================================
        log_header(5, f'提取 workflow 配置（语言: {language}）')
        
        step5_files = []
        
        if not dry_run:
            from step5_extract_workflow_config import extract_workflow_config_from_flow
            
            with open(exported_flow_file, 'r', encoding='utf-8') as f:
                flow_data = json.load(f)
            
            for wf_name in generated_workflows:
                workflow_config = extract_workflow_config_from_flow(flow_data, exported_flow_file)
                workflow_config['workflow_name'] = wf_name
                
                # 添加 agent 名称和描述
                if 'workflow_info' not in workflow_config:
                    workflow_config['workflow_info'] = {}
                workflow_config['workflow_info']['agent_name'] = agent_name
                workflow_config['workflow_info']['agent_description'] = agent_description
                
                config_file = os.path.join(step_dirs[5], f'workflow_config_{wf_name}.json')
                with open(config_file, 'w', encoding='utf-8') as f:
                    json.dump(workflow_config, f, indent=2, ensure_ascii=False)
                
                step5_files.append(config_file)
        
        result.steps_completed.append('step5')
        result.generated_files['step5'] = step5_files
        log(f'✅ Step 5 完成，生成了 {len(step5_files)} 个配置文件')
        
        # ========================================================================
        # Step 6: 生成最终 workflow
        # ========================================================================
        log_header(6, f'生成最终 workflow（语言: {language}）')
        
        step6_files = []
        
        if not dry_run:
            from step6_workflow_generator import main as generate_single_workflow
            
            for idx, wf_name in enumerate(generated_workflows, 1):
                log(f'\n   [{idx}/{len(generated_workflows)}] 生成: {wf_name}')
                
                workflow_config_file = os.path.join(step_dirs[5], f'workflow_config_{wf_name}.json')
                nodes_config_file = os.path.join(step_dirs[2], f'nodes_config_{wf_name}.json')
                variables_config_file = os.path.join(step_dirs[4], f'variables_{wf_name}.json')
                edge_config_file = os.path.join(step_dirs[2], f'edge_config_{wf_name}.json')
                output_file = os.path.join(step_dirs[6], f'generated_workflow_{wf_name}.json')
                
                # 检查文件
                missing_files = []
                if not os.path.exists(nodes_config_file):
                    missing_files.append(f'nodes_config_{wf_name}.json')
                if not os.path.exists(variables_config_file):
                    missing_files.append(f'variables_{wf_name}.json')
                if not os.path.exists(edge_config_file):
                    missing_files.append(f'edge_config_{wf_name}.json')
                
                if missing_files:
                    log(f'      ⚠️  跳过: 缺少文件 {", ".join(missing_files)}')
                    continue
                
                # 如果没有 workflow_config，使用默认配置
                if not os.path.exists(workflow_config_file):
                    default_config = {
                        "workflow_name": wf_name,
                        "workflow_info": {
                            "description": f"{wf_name} 工作流",
                            "created_by": agent_name,
                            "intent_description": agent_description
                        }
                    }
                    with open(workflow_config_file, 'w', encoding='utf-8') as f:
                        json.dump(default_config, f, indent=2, ensure_ascii=False)
                
                try:
                    # 语言代码已经是规范化的
                    normalized_language = language

                    generate_single_workflow(
                        workflow_config=workflow_config_file,
                        nodes_config=nodes_config_file,
                        variables_config=variables_config_file,
                        edge_config=edge_config_file,
                        output_file=output_file,
                        language=normalized_language
                    )
                    step6_files.append(output_file)
                    log(f'      ✅ 已生成: {output_file}')
                except Exception as e:
                    log(f'      ❌ 生成失败: {str(e)}')
                    result.errors.append(f'生成 {wf_name} 失败: {str(e)}')
        
        result.steps_completed.append('step6')
        result.generated_files['step6'] = step6_files
        log(f'✅ Step 6 完成，生成了 {len(step6_files)} 个最终 workflow')
        
        # ========================================================================
        # Step 7: 清理孤立节点 + 布局优化
        # ========================================================================
        log_header(7, '清理孤立节点 + 布局优化')
        
        if not dry_run:
            from step7_clean_isolated_nodes import main as clean_isolated_nodes
            
            step6_files_list = [f for f in os.listdir(step_dirs[6]) if f.endswith('.json') and f.startswith('generated_workflow_')]
            
            if step6_files_list:
                log(f'   处理 {len(step6_files_list)} 个 workflow 文件...\n')
                clean_isolated_nodes(
                    dry_run=False,
                    input_dir=step_dirs[6],
                    output_dir=step_dirs[7],
                    optimize=True,
                    layout_direction='LR'
                )
        
        result.steps_completed.append('step7')
        log('✅ Step 7 完成')
        
        # ========================================================================
        # Step 8: 合并 workflow 到 planning JSON
        # ========================================================================
        log_header(8, '合并 workflow 到 planning JSON')
        
        final_agent_file = ""
        
        if not dry_run:
            from step8_merge_to_planning import main as merge_to_planning
            
            # 提取项目名称
            project_name = os.path.basename(exported_flow_file).replace('exported_flow_', '').replace('.json', '')
            
            # 使用 agent_name 作为模板名称（清理特殊字符）
            template_name = agent_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
            
            final_agent_file = os.path.join(step_dirs[8], f"{template_name}_{project_name}_{language}_merged.json")
            
            # 根据语言选择对应的 agent 模板文件
            agent_template_path = None
            lang_lower = language.lower()
            if lang_lower in ['en', 'english']:
                # 英文使用 agent_EN.json
                en_template = os.path.join('input', 'agent_EN.json')
                if os.path.exists(en_template):
                    agent_template_path = en_template
                    log(f'   📄 使用英文 Agent 模板: {en_template}')
            elif lang_lower in ['zh', 'chinese']:
                # 简体中文使用 agent-zh.json
                zh_template = os.path.join('input', 'agent-zh.json')
                if os.path.exists(zh_cn_template):
                    agent_template_path = zh_cn_template
                    log(f'   📄 使用简体中文 Agent 模板: {zh_template}')
                else:
                    default_template = os.path.join('input', 'agent.json')
                    if os.path.exists(default_template):
                        agent_template_path = default_template
                        log(f'   📄 简体中文模板不存在，使用默认模板: {default_template}')
            elif lang_lower in ['zh-hant', 'cantonese']:
                # 繁体中文/粤语使用 agent-zh-hant.json
                zh_hant_template = os.path.join('input', 'agent-zh-hant.json')
                if os.path.exists(zh_hant_template):
                    agent_template_path = zh_hant_template
                    log(f'   📄 使用繁体中文 Agent 模板: {zh_hant_template}')
                else:
                    default_template = os.path.join('input', 'agent.json')
                    if os.path.exists(default_template):
                        agent_template_path = default_template
                        log(f'   📄 繁体中文模板不存在，使用默认模板: {default_template}')
            else:
                # 其他语言使用默认的 agent.json
                default_template = os.path.join('input', 'agent.json')
                if os.path.exists(default_template):
                    agent_template_path = default_template
                    log(f'   📄 使用默认 Agent 模板: {default_template}')
            
            # 确保 agent_template_path 不为 None
            if agent_template_path is None:
                error_msg = f"未找到合适的 Agent 模板文件 (语言: {language})"
                log(f'   ❌ {error_msg}')
                log(f'   💡 请检查 input/ 目录下是否存在以下文件:')
                log(f'      - agent-zh-hant.json (繁体中文)')
                log(f'      - agent-zh.json (简体中文)')
                log(f'      - agent_EN.json (英文)')
                log(f'      - agent.json (默认)')
                result.errors.append(error_msg)
                raise FileNotFoundError(error_msg)
            
            step7_files_list = [f for f in os.listdir(step_dirs[7]) if f.endswith('.json') and f.startswith('generated_workflow_')]
            
            if step7_files_list:
                log(f'   找到 {len(step7_files_list)} 个 workflow 文件...\n')
                try:
                    merge_to_planning(
                        template_json_path=agent_template_path,
                        step7_dir=step_dirs[7],
                        output_path=final_agent_file,
                        exported_flow_file=exported_flow_file
                    )
                    
                    if os.path.exists(final_agent_file):
                        result.final_agent_file = final_agent_file
                        log(f'   ✅ 合并完成: {final_agent_file}')
                except Exception as e:
                    log(f'   ❌ 合并失败: {str(e)}')
                    result.errors.append(f'Step 8 合并失败: {str(e)}')
        
        result.steps_completed.append('step8')
        result.generated_files['step8'] = [final_agent_file] if final_agent_file else []
        log('✅ Step 8 完成')
        
        # ========================================================================
        # 完成
        # ========================================================================
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        result.duration_seconds = duration
        
        if len(result.errors) == 0:
            result.success = True
            result.message = '所有步骤完成'
        else:
            result.success = False
            result.message = f'完成但有 {len(result.errors)} 个错误'
        
        log('\n' + '='*70)
        log('🎉 处理完成！')
        log('='*70)
        log(f'结束时间: {end_time.strftime("%Y-%m-%d %H:%M:%S")}')
        log(f'总耗时: {duration:.2f}秒')
        log(f'完成步骤: {", ".join(result.steps_completed)}')
        if result.final_agent_file:
            log(f'最终输出文件: {result.final_agent_file}')
            log(f'💡 请将此文件手动导入目标平台（n8n / Dify / Coze 等）')
        if result.errors:
            log(f'错误数量: {len(result.errors)}')
            for err in result.errors:
                log(f'   - {err}')
        
        # 显示日志文件路径
        current_log_file = get_current_log_file()
        if current_log_file:
            log(f'\n📄 运行日志已保存到: {current_log_file}')
        
        log('='*70 + '\n')
        
        # 关闭日志捕获
        if verbose:
            disable_print_capture()
        
        return result
        
    except Exception as e:
        import traceback
        result.success = False
        result.message = f'执行失败: {str(e)}'
        result.errors.append(str(e))
        result.errors.append(traceback.format_exc())
        
        end_time = datetime.now()
        result.duration_seconds = (end_time - start_time).total_seconds()
        
        if verbose:
            print(f'\n❌ 错误: {str(e)}')
            traceback.print_exc()
            
            # 显示日志文件路径
            current_log_file = get_current_log_file()
            if current_log_file:
                print(f'\n📄 运行日志已保存到: {current_log_file}')
            
            # 关闭日志捕获
            disable_print_capture()
        
        return result


# 便捷函数：从配置字典调用
def run_from_config(config: Dict[str, Any]) -> RunAllStepsResult:
    """
    从配置字典运行
    
    Args:
        config: 配置字典，包含以下字段：
            - exported_flow_file: str（必填）
            - agent_name: str (可选)
            - agent_description: str (可选)
            - language: str (可选, 默认 "en")
            - create_kb: bool (可选, 默认 False)
            - robot_key: str (可选, create_kb=True 时使用)
            - robot_token: str (可选, create_kb=True 时使用)
            - username: str (可选, create_kb=True 时使用)
            - base_url: str (可选)
            - output_base_dir: str (可选)
            - dry_run: bool (可选)
            - verbose: bool (可选)
    
    Returns:
        RunAllStepsResult
    """
    return run_all_steps(
        exported_flow_file=config['exported_flow_file'],
        agent_name=config.get('agent_name', 'Migrated Agent'),
        agent_description=config.get('agent_description', 'Agent migrated from Dialogflow CX'),
        language=config.get('language', 'en'),
        create_kb=config.get('create_kb', False),
        robot_key=config.get('robot_key', ''),
        robot_token=config.get('robot_token', ''),
        username=config.get('username', ''),
        base_url=config.get('base_url', ''),
        output_base_dir=config.get('output_base_dir', 'output'),
        dry_run=config.get('dry_run', False),
        verbose=config.get('verbose', True)
    )


# 命令行入口
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Dialogflow CX 工作流转换 - API 模式')
    parser.add_argument('--flow-file', '-f', type=str, required=True,
                        help='Dialogflow CX 导出的 flow JSON 文件路径')
    parser.add_argument('--agent-name', '-n', type=str, default='Migrated Agent',
                        help='Agent 名称')
    parser.add_argument('--agent-description', '-d', type=str, default='Agent migrated from Dialogflow CX',
                        help='Agent 描述')
    parser.add_argument('--language', '-l', type=str, default='en',
                        choices=['en', 'zh', 'zh-hant'],
                        help='目标语言（只生成一种语言）')
    parser.add_argument('--create-kb', action='store_true',
                        help='创建知识库（需配置 --robot-key/token/username）')
    parser.add_argument('--robot-key', '-k', type=str, default='',
                        help='平台 API Robot Key（可选，--create-kb 时使用）')
    parser.add_argument('--robot-token', '-t', type=str, default='',
                        help='平台 API Robot Token（可选，--create-kb 时使用）')
    parser.add_argument('--username', '-u', type=str, default='',
                        help='平台用户名（可选，--create-kb 时使用）')
    parser.add_argument('--base-url', type=str, default='',
                        help='平台 API 基础 URL（可选，--create-kb 时使用）')
    parser.add_argument('--output-dir', '-o', type=str, default='output',
                        help='输出目录')
    parser.add_argument('--dry-run', action='store_true',
                        help='只检查，不执行')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='安静模式，减少输出')
    
    args = parser.parse_args()
    
    result = run_all_steps(
        exported_flow_file=args.flow_file,
        agent_name=args.agent_name,
        agent_description=args.agent_description,
        language=args.language,
        create_kb=args.create_kb,
        robot_key=args.robot_key,
        robot_token=args.robot_token,
        username=args.username,
        base_url=args.base_url,
        output_base_dir=args.output_dir,
        dry_run=args.dry_run,
        verbose=not args.quiet
    )
    
    # 输出结果摘要
    print("\n" + "="*70)
    print("📊 执行结果摘要")
    print("="*70)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    
    sys.exit(0 if result.success else 1)


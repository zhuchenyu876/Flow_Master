"""
端到端知识库工作流
====================

完整自动化流程:
1. 从 intents JSON 提取数据生成 Excel
2. 上传知识库
3. 生成知识库 ID 和 intent ID 的映射文件
4. 自动更新所有 workflow 配置文件中的 knowledge_base_ids

使用方法:
    1. 修改 config_kb_workflow.py 中的配置参数
    2. 运行: python end_to_end_kb_workflow.py

特点:
    - 完全自动化, 无需用户交互
    - 可配置跳过已完成的步骤
    - 自动备份原始配置文件
    - 智能匹配单/多 intent 文件
    - 智能复用已有映射，避免重复创建知识库
"""

import json
import os
import glob
import re
import shutil
import sys
from pathlib import Path

from logger_config import get_logger
logger = get_logger(__name__)

# 从 .env 文件读取配置
from dotenv import load_dotenv
load_dotenv()

# API 配置(统一从 .env 读取)
ROBOT_KEY = os.getenv("ROBOT_KEY", "")
ROBOT_TOKEN = os.getenv("ROBOT_TOKEN", "")
USERNAME = os.getenv("USERNAME", "")

# 步骤开关
STEP_1_CREATE_KB = os.getenv("STEP_3_STEP_1_CREATE_KB", "False").lower() == "true"
STEP_2_UPDATE_WORKFLOW = os.getenv("STEP_3_STEP_2_UPDATE_WORKFLOW", "True").lower() == "true"

# 知识库创建配置
CREATE_KB = os.getenv("STEP_3_CREATE_KB", "True").lower() == "true"
max_intents_str = os.getenv("STEP_3_MAX_INTENTS_TO_PROCESS", "").strip()
MAX_INTENTS_TO_PROCESS = int(max_intents_str) if max_intents_str and max_intents_str.isdigit() else None
KB_NAME_PREFIX = os.getenv("STEP_3_KB_NAME_PREFIX", "")
KB_NAME_SUFFIX = os.getenv("STEP_3_KB_NAME_SUFFIX", "")
LANGUAGE = os.getenv("STEP_3_LANGUAGE", "en")
LANGUAGE_CODE = LANGUAGE  # step3_kb_creator期望的变量名
MAX_KB_NAME_LENGTH = int(os.getenv("STEP_3_MAX_KB_NAME_LENGTH", "255"))
MAX_KB_DESC_LENGTH = int(os.getenv("STEP_3_MAX_KB_DESC_LENGTH", "128"))
REQUEST_DELAY = float(os.getenv("STEP_3_REQUEST_DELAY", "0.5"))
SMART_SKIP = os.getenv("STEP_3_SMART_SKIP", "True").lower() == "true"

# 文件路径配置
INTENTS_DIR = os.getenv("STEP_3_INTENTS_DIR", "output/step1_processed")
QA_OUTPUT_DIR = os.getenv("STEP_3_QA_OUTPUT_DIR", "output/qa_knowledge_bases")
QA_TEMP_DIR = os.getenv("STEP_3_QA_TEMP_DIR", os.path.join(QA_OUTPUT_DIR, "temp"))
WORKFLOW_CONFIG_DIR = os.getenv("STEP_3_WORKFLOW_CONFIG_DIR", f"output/step2_workflow_config/{LANGUAGE}" if LANGUAGE != "all" else "output/step2_workflow_config")
BACKUP_DIR = os.path.join(WORKFLOW_CONFIG_DIR, "bak")
KB_RESULTS_FILE = os.path.join(QA_OUTPUT_DIR, f"kb_per_intent_results_{LANGUAGE}.json")

# 导入知识库创建模块
try:
    from create_knowledge_base import KnowledgeBaseAPI
except ImportError as e:
    logger.error(f"❌ 错误: 无法导入 create_knowledge_base.py - {e}")
    logger.info("   请确保 create_knowledge_base.py 文件存在且依赖库已安装")
    sys.exit(1)
except Exception as e:
    logger.error(f"❌ 错误: 导入时发生异常 - {e}", exc_info=True)
    sys.exit(1)


# ========================================
# 步骤 1: 创建知识库
# ========================================

def step1_create_knowledge_bases(task_id=None, db_session=None):
    """创建知识库(直接调用 create_kb_per_intent.py 中的逻辑)
    
    Args:
        task_id: 任务ID(可选,用于写入数据库)
        db_session: 数据库会话(可选)
    
    说明:
        - 如果已有映射文件(由 step3_kb_reuse_helper 生成)，将自动复用
        - 只会创建尚未映射的知识库，避免重复创建
    """
    logger.info("="*80)
    logger.info("Step 3.1: 创建知识库")
    logger.info("="*80)
    
    if not STEP_1_CREATE_KB:
        logger.info(f"⏭️  跳过知识库创建步骤(STEP_1_CREATE_KB={STEP_1_CREATE_KB})，目前知识库都已上传过，无需重复创建")
        return True
    
    logger.info("💡 智能复用逻辑已启用：")
    logger.info("   - 自动检测已有映射文件")
    logger.info("   - 只创建尚未映射的知识库")
    logger.info("   - 避免重复创建和 API 调用")
    
    # 确定要处理的文件
    if LANGUAGE == "all":
        lang_files = [
            ("en", os.path.join(INTENTS_DIR, "intents_en.json")),
            ("zh", os.path.join(INTENTS_DIR, "intents_zh.json")),
            ("zh-hant", os.path.join(INTENTS_DIR, "intents_zh-hant.json"))
        ]
    else:
        lang_files = [(LANGUAGE, os.path.join(INTENTS_DIR, f"intents_{LANGUAGE}.json"))]
    
    # 处理每个语言文件
    processed_any = False
    for lang_code, input_file in lang_files:
        if not os.path.exists(input_file):
            logger.warning(f"⚠️  跳过 {lang_code}: 文件不存在 {input_file}")
            continue
        
        processed_any = True
        logger.info(f"\n{'='*80}")
        logger.info(f"🌍 处理语言: {lang_code}")
        logger.info(f"{'='*80}\n")
        
        # 直接导入并调用 process_language_file 函数
        try:
            import step3_kb_creator
            
            # 临时修改模块的全局变量以使用配置
            old_values = {}
            for var_name in ['ROBOT_KEY', 'ROBOT_TOKEN', 'USERNAME', 'CREATE_KB',
                           'MAX_INTENTS_TO_PROCESS', 'KB_NAME_PREFIX', 'KB_NAME_SUFFIX',
                           'MAX_KB_NAME_LENGTH', 'MAX_KB_DESC_LENGTH', 'REQUEST_DELAY', 'SMART_SKIP',
                           'LANGUAGE_CODE']:
                if hasattr(step3_kb_creator, var_name):
                    old_values[var_name] = getattr(step3_kb_creator, var_name)
                setattr(step3_kb_creator, var_name, globals()[var_name])
            
            # 调用处理函数(传入task_id, db_session和output_dir)
            step3_kb_creator.process_language_file(input_file, lang_code, task_id=task_id, db_session=db_session, output_dir=QA_OUTPUT_DIR)
            
            # 恢复原值
            for var_name, old_value in old_values.items():
                setattr(step3_kb_creator, var_name, old_value)
            
        except Exception as e:
            logger.error(f"❌ 处理失败: {e}", exc_info=True)
            return False
    
    if processed_any:
        logger.info("✅ 知识库创建完成")
    
    return processed_any


# ========================================
# 步骤 2: 更新 Workflow 配置
# ========================================

def load_kb_mapping(task_id=None, db_session=None):
    """加载知识库映射关系
    
    优先级:
    1. 从数据库读取(如果提供了 task_id 和 db_session)
    2. 从本地 JSON 文件读取(兼容独立运行模式)
    
    Args:
        task_id: 任务ID(可选)
        db_session: 数据库会话(可选)
        
    Returns:
        (display_name_to_kb, results): 映射字典和结果字典
    """
    display_name_to_kb = {}
    results = {}
    
    # 方式1: 从数据库读取(优先)
    if task_id and db_session:
        try:
            logger.debug("   正在从数据库加载知识库映射...")
            from database import crud
            from database.models import KBStatus
            
            logger.debug(f"   查询 task_id={task_id} 的映射记录...")
            mappings = crud.get_kb_mappings(db_session, task_id, status=KBStatus.CREATED)
            logger.debug(f"   数据库查询完成，返回 {len(mappings) if mappings else 0} 条记录")
            
            if mappings:
                logger.info(f"✅ 从数据库加载了 {len(mappings)} 个知识库映射")
                logger.debug("   正在处理映射记录...")
                
                for idx, mapping in enumerate(mappings, 1):
                    if mapping.kb_id:
                        display_name = mapping.intent_name
                        kb_id = mapping.kb_id
                        
                        # 确保 kb_id 是整数
                        kb_id = int(kb_id) if isinstance(kb_id, str) else kb_id
                        
                        # 添加到映射字典
                        display_name_to_kb[display_name] = kb_id
                        display_name_to_kb[display_name.lower()] = kb_id
                        display_name_to_kb[display_name.lower().replace('_', '')] = kb_id
                        
                        # 添加到结果字典(与文件格式一致)
                        results[display_name] = {
                            "intent_id": mapping.intent_id,
                            "display_name": display_name,
                            "kb_id": kb_id,
                            "kb_name": mapping.kb_name,
                            "status": "success"
                        }
                    
                    # 每处理 50 个输出一次进度
                    if idx % 50 == 0:
                        logger.debug(f"   已处理 {idx}/{len(mappings)} 个映射记录...")
                
                logger.debug(f"   映射记录处理完成，共 {len(display_name_to_kb)} 个有效映射")
                return display_name_to_kb, results
            else:
                logger.warning(f"⚠️  数据库中未找到 task_id={task_id} 的知识库映射")
        except Exception as e:
            logger.warning(f"⚠️  从数据库读取失败: {e}")
    
    # 方式2: 从本地文件读取(降级方案)
    # 动态构建文件路径(避免使用模块加载时的旧值)
    qa_output_dir = os.getenv("STEP_3_QA_OUTPUT_DIR", "output/qa_knowledge_bases")
    language = os.getenv("STEP_3_LANGUAGE", "en")
    kb_results_file = os.path.join(qa_output_dir, f"kb_per_intent_results_{language}.json")
    
    if os.path.exists(kb_results_file):
        logger.info(f"📂 从本地文件加载映射: {kb_results_file}")
        
        with open(kb_results_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        results = data.get("results", {})
        
        # 创建映射: display_name -> kb_id
        for display_name, record in results.items():
            if record.get("status") == "success" and record.get("kb_id"):
                kb_id = record.get("kb_id")
                # 确保 kb_id 是整数
                kb_id = int(kb_id) if isinstance(kb_id, str) else kb_id
                display_name_to_kb[display_name] = kb_id
                # 添加小写版本用于模糊匹配
                display_name_to_kb[display_name.lower()] = kb_id
                # 添加去下划线版本
                display_name_to_kb[display_name.lower().replace('_', '')] = kb_id
        
        logger.info(f"✅ 加载了 {len(results)} 个 intent 的知识库映射")
        return display_name_to_kb, results
    else:
        logger.error(f"❌ 错误: 找不到映射文件 {kb_results_file}")
        return None, None


def find_intents_in_file(filepath):
    """从配置文件中找出所有相关的 intent
    返回: list of intent names
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    found_intents = []
    nodes = data.get("nodes", [])
    
    # 方法1: 从 condition_name 中查找 Intent_xxx
    for node in nodes:
        if node.get("type") == "condition":
            conditions = node.get("if_else_conditions", [])
            for cond in conditions:
                cond_name = cond.get("condition_name", "")
                if cond_name.startswith("Intent_"):
                    intent_name = cond_name.replace("Intent_", "")
                    if intent_name and intent_name not in found_intents:
                        found_intents.append(intent_name)
    
    # 方法2: 从 knowledgeAssignment 节点的 page_intents 中查找
    for node in nodes:
        if node.get("type") == "knowledgeAssignment":
            page_intents = node.get("page_intents", [])
            for intent_name in page_intents:
                if intent_name and intent_name not in found_intents:
                    found_intents.append(intent_name)
    
    return found_intents


def match_intent_to_kb(intent_name, kb_mapping):
    """匹配 intent 名称到知识库 ID
    """
    # 精确匹配
    if intent_name in kb_mapping:
        return kb_mapping[intent_name]
    
    # 小写匹配
    if intent_name.lower() in kb_mapping:
        return kb_mapping[intent_name.lower()]
    
    # 去下划线匹配
    normalized = intent_name.lower().replace('_', '')
    if normalized in kb_mapping:
        return kb_mapping[normalized]
    
    # 部分匹配
    for display_name, kb_id in kb_mapping.items():
        if isinstance(display_name, str):
            if display_name.lower().replace('_', '') == normalized:
                return kb_id
    
    return None


def extract_intent_from_filename(filename):
    """从文件名提取 intent 名称
    例如: nodes_config_transactionservicing_accountinfo.json -> TransactionServicing_AccountInfo
    """
    # 跳过特殊文件
    if filename in ["nodes_config.json", "edge_config.json"]:
        return None
    
    # 跳过 intent_数字 文件(这些文件包含多个intent, 需要从内容提取)
    if re.match(r'nodes_config_intent_\d+\.json', filename):
        return None
    
    # 提取 nodes_config_xxx.json 中的 xxx
    match = re.match(r'nodes_config_(.+)\.json', filename)
    if not match:
        return None
    
    name_part = match.group(1)
    
    # 转换为 PascalCase
    # transactionservicing_accountinfo -> TransactionServicing_AccountInfo
    parts = name_part.split('_')
    intent_name = '_'.join([p.capitalize() for p in parts])
    
    return intent_name


def update_workflow_file(filepath, kb_mapping):
    """更新单个 workflow 配置文件
    
    Args:
        filepath: 配置文件路径
        kb_mapping: display_name -> kb_id 的映射字典
    
    Returns:
        更新的节点数量
    """
    # logger.debug(f"   🔧 update_workflow_file: {os.path.basename(filepath)}")
    # logger.debug(f"      kb_mapping keys 数量: {len(kb_mapping)}")
    # if len(kb_mapping) > 0:
    #     logger.debug(f"      前5个keys: {list(kb_mapping.keys())[:5]}")
    
    with open(filepath, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    nodes = config.get("nodes", [])
    updated_count = 0
    
    # 统计有 page_intents 的节点数量
    # kb_nodes_with_page_intents = [n for n in nodes if n.get("type") == "knowledgeAssignment" and n.get("page_intents")]
    # logger.debug(f"      找到 {len(kb_nodes_with_page_intents)} 个有 page_intents 的 KB 节点")
    
    for node in nodes:
        if node.get("type") == "knowledgeAssignment":
            old_ids = node.get("knowledge_base_ids", [])
            
            # 检查是否有 page_intents 字段
            page_intents = node.get("page_intents", [])
            
            if page_intents:
                # Page-level 节点: 只使用该 page 对应的 intent(s) 的知识库ID
                kb_ids = []
                for intent_name in page_intents:
                    kb_id = match_intent_to_kb(intent_name, kb_mapping)
                    if kb_id:
                        kb_id = int(kb_id) if isinstance(kb_id, str) else kb_id
                        if kb_id not in kb_ids:
                            kb_ids.append(kb_id)
                    else:
                        logger.warning(f"        ⚠️  未找到知识库: {intent_name}")
                
                if kb_ids:
                    node["knowledge_base_ids"] = sorted(kb_ids)
                    updated_count += 1
                    # logger.debug(f"      🔹 节点 '{node.get('name')}': {old_ids} → {sorted(kb_ids)} (Page intents: {page_intents})")
                else:
                    logger.warning(f"      ⚠️  节点 '{node.get('name')}': 未找到任何匹配的知识库ID (Page intents: {page_intents})")
            else:
                # Flow-level 节点: 保持所有知识库ID(会在外层函数设置)
                # 跳过更新, 由调用方处理
                pass
    
    if updated_count > 0:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    
    return updated_count


def backup_workflow_configs():
    """备份原始配置文件"""
    
    # 动态获取目录路径（运行时环境变量可能已更新）
    language = os.getenv("STEP_3_LANGUAGE", LANGUAGE)
    workflow_config_dir = os.getenv("STEP_3_WORKFLOW_CONFIG_DIR", 
        f"output/step2_workflow_config/{language}" if language != "all" else "output/step2_workflow_config")
    backup_dir = os.path.join(workflow_config_dir, "bak")
    
    # 规范化路径（解决 Windows 路径问题）
    workflow_config_dir = os.path.normpath(workflow_config_dir)
    backup_dir = os.path.normpath(backup_dir)
    
    logger.info(f"📦 备份原始配置文件到: {backup_dir}")
    
    # 确保备份目录存在
    try:
        os.makedirs(backup_dir, exist_ok=True)
        if not os.path.exists(backup_dir):
            logger.error(f"❌ 无法创建备份目录: {backup_dir}")
            return False
        logger.debug(f"   ✅ 备份目录已创建/存在: {backup_dir}")
    except Exception as e:
        logger.error(f"❌ 创建备份目录失败: {backup_dir} - {e}")
        return False
    
    pattern = os.path.join(workflow_config_dir, "nodes_config_*.json")
    files = glob.glob(pattern)
    
    if not files:
        logger.warning(f"⚠️  未找到任何配置文件: {pattern}")
        return True
    
    backed_up_count = 0
    skipped_count = 0
    error_count = 0
    
    for filepath in files:
        filename = os.path.basename(filepath)
        backup_path = os.path.join(backup_dir, filename)
        
        # 检查源文件是否存在
        if not os.path.exists(filepath):
            logger.warning(f"⚠️  源文件不存在, 跳过: {filepath}")
            error_count += 1
            continue
        
        # 如果备份文件已存在, 跳过(避免覆盖)
        if os.path.exists(backup_path):
            skipped_count += 1
            continue
        
        try:
            # 规范化路径（解决 Windows 路径问题）
            backup_dir_normalized = os.path.normpath(backup_dir)
            backup_path_normalized = os.path.normpath(backup_path)
            filepath_normalized = os.path.normpath(filepath)
            
            # 再次确保备份目录存在(防止并发问题)
            os.makedirs(backup_dir_normalized, exist_ok=True)
            
            # 验证目录是否真的创建成功
            if not os.path.exists(backup_dir_normalized):
                raise FileNotFoundError(f"无法创建备份目录: {backup_dir_normalized}")
            
            # 验证源文件存在
            if not os.path.exists(filepath_normalized):
                raise FileNotFoundError(f"源文件不存在: {filepath_normalized}")
            
            # 使用 shutil.copy2 复制文件（保留元数据，更可靠）
            shutil.copy2(filepath_normalized, backup_path_normalized)
            
            # 验证备份文件是否创建成功
            if not os.path.exists(backup_path_normalized):
                raise FileNotFoundError(f"备份文件创建失败: {backup_path_normalized}")
            
            backed_up_count += 1
        except FileNotFoundError as e:
            logger.error(f"❌ 备份文件失败(文件或目录不存在): {filename}")
            logger.error(f"   源文件: {filepath}")
            logger.error(f"   备份路径: {backup_path}")
            logger.error(f"   错误详情: {e}")
            error_count += 1
        except Exception as e:
            logger.error(f"❌ 备份文件失败: {filename} - {e}")
            logger.error(f"   源文件: {filepath}")
            logger.error(f"   备份路径: {backup_path}")
            logger.error(f"   错误详情: {e}")
            error_count += 1
    
    if backed_up_count > 0:
        logger.info(f"✅ 已备份 {backed_up_count} 个配置文件到 {backup_dir}")
    if skipped_count > 0:
        logger.info(f"ℹ️  跳过 {skipped_count} 个已存在的备份文件")
    if error_count > 0:
        logger.warning(f"⚠️  {error_count} 个文件备份失败")
        # 如果有错误但至少备份了一些文件, 继续执行; 如果全部失败, 返回False
        if backed_up_count == 0:
            return False
    
    return True


def step2_update_workflow_configs(task_id=None, db_session=None):
    """更新所有 workflow 配置文件
    
    Args:
        task_id: 任务ID(可选, 用于从数据库读取映射)
        db_session: 数据库会话(可选)
    """
    logger.info("="*80)
    logger.info("Step 3.2: 更新 Workflow 配置")
    logger.info("="*80)
    
    # 动态获取目录路径（运行时环境变量可能已更新）
    language = os.getenv("STEP_3_LANGUAGE", LANGUAGE)
    workflow_config_dir = os.getenv("STEP_3_WORKFLOW_CONFIG_DIR", 
        f"output/step2_workflow_config/{language}" if language != "all" else "output/step2_workflow_config")
    backup_dir = os.path.join(workflow_config_dir, "bak")
    qa_output_dir = os.getenv("STEP_3_QA_OUTPUT_DIR", QA_OUTPUT_DIR)
    kb_results_file = os.path.join(qa_output_dir, f"kb_per_intent_results_{language}.json")
    
    if not STEP_2_UPDATE_WORKFLOW:
        logger.info("⏭️  跳过 Workflow 配置更新步骤(STEP_2_UPDATE_WORKFLOW=False)")
        return True
    
    # 加载映射(优先从数据库, 其次从文件)
    if task_id and db_session:
        logger.info(f"📂 加载知识库映射(优先级: 数据库 > 本地文件)")
    else:
        logger.info(f"📂 从本地文件加载映射: {kb_results_file}")
    
    kb_mapping, results = load_kb_mapping(task_id=task_id, db_session=db_session)
    
    # 检查是否真的加载失败(返回None)
    if kb_mapping is None or results is None:
        logger.error(f"❌ 无法加载知识库映射(数据库和文件均未找到)")
        return False
    
    # 检查是否有有效的映射
    if len(kb_mapping) == 0:
        logger.warning(f"⚠️  加载的映射文件为空(可能所有intent都未成功创建KB)")
        # 这里不终止, 继续执行(可能有些情况不需要KB)
    
    logger.info(f"✅ 成功加载 {len(kb_mapping)} 个知识库映射")
    
    # 备份
    logger.info(f"📦 备份原始配置文件到: {backup_dir}")
    logger.debug(f"   配置文件目录: {workflow_config_dir}")
    if not backup_workflow_configs():
        logger.error("❌ 备份失败")
        return False
    logger.debug("   备份完成")
    
    # 查找所有配置文件
    logger.debug(f"   正在查找配置文件...")
    pattern = os.path.join(workflow_config_dir, "nodes_config_*.json")
    files = glob.glob(pattern)
    logger.debug(f"   找到 {len(files)} 个文件")
    
    # 排除特殊文件
    files = [f for f in files if os.path.basename(f) not in ["nodes_config.json"]]
    
    logger.info(f"🔍 找到 {len(files)} 个配置文件需要更新")
    logger.info("-"*80)
    
    updated_files = 0
    updated_nodes = 0
    skipped_files = []
    
    for file_idx, filepath in enumerate(sorted(files), 1):
        filename = os.path.basename(filepath)
        logger.info(f"\n📄 [{file_idx}/{len(files)}] {filename}")
        
        # 方法1: 从文件内容中提取 intent
        intents_from_content = find_intents_in_file(filepath)
        
        # 方法2: 从文件名中提取 intent(主 intent)
        intent_from_filename = extract_intent_from_filename(filename)
        
        # 确定使用哪种方法
        if intents_from_content:
            # 多 intent 文件
            logger.debug(f"   📋 检测到 {len(intents_from_content)} 个 intent (从内容提取)")
            
            main_intent = intent_from_filename
            main_kb_id = None
            
            if main_intent:
                main_kb_id = match_intent_to_kb(main_intent, kb_mapping)
                if main_kb_id:
                    main_kb_id = int(main_kb_id) if isinstance(main_kb_id, str) else main_kb_id
                    logger.debug(f"      🎯 主 Intent (从文件名): {main_intent} → KB {main_kb_id}")
            
            # 更新 flow-level 节点
            if main_kb_id:
                with open(filepath, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                flow_level_count = 0
                for node in config.get("nodes", []):
                    if node.get("type") == "knowledgeAssignment" and not node.get("page_intents"):
                        node["knowledge_base_ids"] = [main_kb_id]
                        flow_level_count += 1
                if flow_level_count > 0:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(config, f, ensure_ascii=False, indent=2)
                    logger.debug(f"   ✅ 更新了 {flow_level_count} 个 Flow-level 节点 (主 intent)")

            # 更新 page-level 节点
            with open(filepath, 'r', encoding='utf-8') as f:
                config = json.load(f)
            page_level_count = 0
            for node in config.get("nodes", []):
                if node.get("type") == "knowledgeAssignment":
                    page_intents = node.get("page_intents", [])
                    if page_intents:
                        kb_ids = []
                        for intent_name in page_intents:
                            kb_id = match_intent_to_kb(intent_name, kb_mapping)
                            if kb_id:
                                kb_id = int(kb_id) if isinstance(kb_id, str) else kb_id
                                if kb_id not in kb_ids:
                                    kb_ids.append(kb_id)
                        if kb_ids:
                            node["knowledge_base_ids"] = sorted(kb_ids)
                            page_level_count += 1
            
            if page_level_count > 0:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                updated_files += 1
                updated_nodes += page_level_count
                logger.debug(f"   ✅ 更新了 {page_level_count} 个 page-level 节点")
            else:
                logger.debug(f"   ⚠️  没有找到可更新的 page-level 节点")

        elif intent_from_filename:
            # 单 intent 文件(或无法从内容提取 intent 的文件)
            logger.debug(f"   📋 Intent: {intent_from_filename} (从文件名提取)")
            
            with open(filepath, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            has_page_intents = False
            for node in config.get("nodes", []):
                if node.get("type") == "knowledgeAssignment" and node.get("page_intents"):
                    has_page_intents = True
                    break
            
            if has_page_intents:
                # 如果有 page_intents 节点, 应该根据 page_intents 来匹配, 而不是文件名
                logger.debug(f"   ℹ️  检测到 page_intents 节点, 将根据 page_intents 匹配知识库ID")
                
                page_level_count = 0
                for node in config.get("nodes", []):
                    if node.get("type") == "knowledgeAssignment":
                        page_intents = node.get("page_intents", [])
                        if page_intents:
                            kb_ids = []
                            for intent_name in page_intents:
                                kb_id = match_intent_to_kb(intent_name, kb_mapping)
                                if kb_id:
                                    kb_id = int(kb_id) if isinstance(kb_id, str) else kb_id
                                    if kb_id not in kb_ids:
                                        kb_ids.append(kb_id)
                            
                            if kb_ids:
                                node["knowledge_base_ids"] = sorted(kb_ids)
                                page_level_count += 1
                
                kb_id = match_intent_to_kb(intent_from_filename, kb_mapping)
                flow_level_count = 0
                if kb_id:
                    kb_id = int(kb_id) if isinstance(kb_id, str) else kb_id
                    for node in config.get("nodes", []):
                        if node.get("type") == "knowledgeAssignment" and not node.get("page_intents"):
                            node["knowledge_base_ids"] = [kb_id]
                            flow_level_count += 1
                
                if page_level_count > 0 or flow_level_count > 0:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(config, f, ensure_ascii=False, indent=2)
                
                updated_files += 1
                updated_nodes += page_level_count + flow_level_count
                logger.debug(f"   ✅ 更新了 {page_level_count} 个 page-level 节点 + {flow_level_count} 个 flow-level 节点")
            else:
                kb_id = match_intent_to_kb(intent_from_filename, kb_mapping)
                
                if kb_id:
                    kb_id = int(kb_id) if isinstance(kb_id, str) else kb_id
                    logger.debug(f"   ✅ 匹配到 KB ID: {kb_id}")
                    logger.debug(f"   🔧 更新 knowledge_base_ids: [{kb_id}]")
                    
                    count = 0
                    for node in config.get("nodes", []):
                        if node.get("type") == "knowledgeAssignment":
                            node["knowledge_base_ids"] = [kb_id]
                            count += 1
                    
                    if count > 0:
                        with open(filepath, 'w', encoding='utf-8') as f:
                            json.dump(config, f, ensure_ascii=False, indent=2)
                    
                    updated_files += 1
                    updated_nodes += count
                    logger.debug(f"   ✅ 更新了 {count} 个节点")
                else:
                    logger.warning(f"   ⚠️  跳过: 未找到知识库ID")
                    skipped_files.append(filename)
        else:
            logger.debug(f"   ℹ️  无法从文件名或内容提取有效 Intent, 跳过文件 {filename}")
            skipped_files.append(filename)

    if updated_files > 0:
        logger.info(f"✅ 成功更新了 {updated_files} 个文件中的 {updated_nodes} 个知识库节点")
    if skipped_files:
        logger.info(f"ℹ️  跳过了 {len(skipped_files)} 个文件(未找到匹配的知识库或无更新)")

    return True


# ========================================
# 主流程
# ========================================

def main(task_id=None, db_session=None):
    """端到端主流程
    
    Args:
        task_id: 任务ID(可选, 用于从数据库读取映射)
        db_session: 数据库会话(可选)
    """
    logger.info("="*80)
    logger.info("🚀 端到端知识库工作流")
    logger.info("="*80)
    logger.info("")
    logger.info("流程:")
    logger.info("  1. 创建知识库(从 intents JSON → Excel → 知识库)")
    logger.info("  2. 生成映射文件(intent_id + intent_name → kb_id)")
    logger.info("  3. 更新 workflow 配置(knowledge_base_ids)")
    logger.info("")
    logger.info("="*80)
    
    # 步骤 1: 创建知识库
    if not step1_create_knowledge_bases(task_id=task_id, db_session=db_session):
        logger.error("❌ 步骤 1 失败, 流程终止")
        return False
    
    logger.info("✅ 步骤 1 完成: 知识库创建成功")
    logger.info("")
    
    # 步骤 2: 更新 workflow 配置
    if not step2_update_workflow_configs(task_id=task_id, db_session=db_session):
        logger.error("❌ 步骤 2 失败, 流程终止")
        return False
    
    logger.info("✅ 步骤 2 完成: Workflow 配置更新成功")
    logger.info("")
    
    # 完成
    # 动态获取目录路径用于显示
    language = os.getenv("STEP_3_LANGUAGE", LANGUAGE)
    qa_output_dir = os.getenv("STEP_3_QA_OUTPUT_DIR", QA_OUTPUT_DIR)
    workflow_config_dir = os.getenv("STEP_3_WORKFLOW_CONFIG_DIR", 
        f"output/step2_workflow_config/{language}" if language != "all" else "output/step2_workflow_config")
    kb_results_file = os.path.join(qa_output_dir, f"kb_per_intent_results_{language}.json")
    backup_dir = os.path.join(workflow_config_dir, "bak")
    
    logger.info("="*80)
    logger.info("🎉 Step 3 端到端流程完成！")
    logger.info("="*80)
    logger.info("")
    logger.info("✅ 知识库已创建")
    logger.info(f"✅ 映射文件已生成: {kb_results_file}")
    logger.info("✅ Workflow 配置已更新")
    logger.info(f"✅ 原始文件已备份: {backup_dir}")
    logger.info("")
    logger.info("="*80)
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

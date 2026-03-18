# -*- coding: utf-8 -*-
"""
Step 3 知识库映射复用辅助模块
================================
在 Step 3 执行前，检查数据库是否已有相同 robot_key + intent_name + language 的历史映射，
如果有则复用，避免重复创建知识库。

核心功能：
1. 读取当前任务的 intents 列表
2. 对每个 intent，查询数据库是否有历史映射
3. 如果找到：复用 kb_id，为当前 task_id 创建新映射记录
4. 将所有映射（复用的 + 新建的）写入 JSON 文件，供 step3 使用
"""

import json
import os
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from logger_config import get_logger
logger = get_logger(__name__)

from database import crud
from database.models import KBStatus


def check_and_reuse_kb_mappings(
    db: Session,
    task_id: str,
    robot_key: str,
    intents_file: str,
    language: str,
    output_dir: str
) -> Tuple[int, int]:
    """
    检查并复用知识库映射
    
    Args:
        db: 数据库会话
        task_id: 当前任务 ID
        robot_key: Robot Key
        intents_file: intents JSON 文件路径
        language: 语言代码
        output_dir: 输出目录（用于保存映射 JSON）
        
    Returns:
        (reused_count, new_count): 复用数量和需要新建的数量
    """
    logger.info(f"🔍 检查知识库映射复用机会...")
    logger.info(f"   Robot Key: {robot_key[:20]}... (长度: {len(robot_key)})")
    logger.info(f"   Language: {language}")
    logger.info(f"   Intents File: {intents_file}")
    
    # 调试：检查数据库中是否有相同 robot_key 的任务
    from database.models import MigrationTask
    existing_tasks = db.query(MigrationTask).filter(
        MigrationTask.robot_key == robot_key
    ).all()
    logger.debug(f"   🔍 数据库中找到 {len(existing_tasks)} 个相同 robot_key 的历史任务")
    
    if existing_tasks:
        for task in existing_tasks[:3]:  # 只显示前3个
            logger.debug(f"      - Task ID: {task.task_id[:8]}... | 状态: {task.status.value} | 创建时间: {task.created_at}")
    else:
        # 尝试查找所有任务，看看 robot_key 是否有差异
        all_tasks = db.query(MigrationTask).limit(5).all()
        if all_tasks:
            logger.debug(f"   ⚠️  未找到匹配的任务，但数据库中有任务。对比前5个任务的 robot_key:")
            for task in all_tasks:
                logger.debug(f"      - {task.robot_key[:30]}... (长度: {len(task.robot_key)}) | Task: {task.task_id[:8]}...")
            logger.debug(f"   🔍 当前查询的 robot_key: {robot_key[:30]}... (长度: {len(robot_key)})")
        else:
            logger.debug(f"   ⚠️  数据库中没有任何任务记录")
    
    # 1. 读取 intents 数据
    if not os.path.exists(intents_file):
        logger.warning(f"   Intents 文件不存在: {intents_file}")
        return 0, 0
    
    try:
        with open(intents_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"   读取 intents 文件失败: {e}")
        return 0, 0
    
    # 支持两种格式：{"intents": [...]} 或直接 [...]
    if isinstance(data, dict) and 'intents' in data:
        intents = data['intents']
    elif isinstance(data, list):
        intents = data
    else:
        logger.warning(f"   无效的 intents 数据格式")
        return 0, 0
    
    if not intents:
        logger.warning(f"   未找到 intents 数据")
        return 0, 0
    
    logger.info(f"   找到 {len(intents)} 个 intent")
    
    # 2. 【优化】批量查询所有历史映射（避免 N+1 查询问题）
    logger.info(f"   📥 批量查询历史映射...")
    
    from database.models import MigrationTask, KnowledgeBaseMapping
    
    # 2.1 查找所有使用相同 robot_key 的任务
    existing_tasks = db.query(MigrationTask).filter(
        MigrationTask.robot_key == robot_key
    ).all()
    
    # 2.2 构建历史映射字典 {intent_name: mapping}
    historical_mappings = {}
    db_mapping_count = 0
    
    if existing_tasks:
        task_ids = [task.task_id for task in existing_tasks]
        logger.info(f"   找到 {len(existing_tasks)} 个历史任务，查询其知识库映射...")
        
        # 一次性查询所有匹配的映射
        all_mappings = db.query(KnowledgeBaseMapping).filter(
            KnowledgeBaseMapping.task_id.in_(task_ids),
            KnowledgeBaseMapping.language == language,
            KnowledgeBaseMapping.status == KBStatus.CREATED,
            KnowledgeBaseMapping.kb_id.isnot(None)
        ).all()
        
        db_mapping_count = len(all_mappings)
        logger.info(f"   查询到 {db_mapping_count} 个历史知识库映射")
        
        # 构建字典（如果有重复，保留最新的）
        for mapping in all_mappings:
            intent_key = mapping.intent_name
            if intent_key not in historical_mappings:
                historical_mappings[intent_key] = mapping
    else:
        logger.info(f"   未找到历史任务")
    
    # 2.3 【提示】如果数据库映射为空，但 Dyna.ai 中已有 KB
    if db_mapping_count == 0 and len(intents) > 0:
        logger.warning(f"")
        logger.warning(f"   ⚠️  数据库中没有历史映射记录")
        logger.warning(f"   ⚠️  如果 Dyna.ai 空间中已存在同名知识库，创建时会报错")
        logger.warning(f"")
        logger.warning(f"   💡 建议操作：")
        logger.warning(f"      1. 登录 Dyna.ai 平台手动删除旧知识库")
        logger.warning(f"      2. 或者不清空数据库，让系统自动复用映射")
        logger.warning(f"      3. 详见：scripts/README.md")
        logger.warning(f"")
    
    # 3. 准备映射结果
    reused_count = 0
    new_count = 0
    kb_results = {}  # 用于生成 JSON 文件
    reused_details = []  # 记录复用的详情
    
    # 4. 遍历每个 intent，查找历史映射（在内存中匹配，无需数据库查询）
    logger.info(f"   📝 正在匹配历史映射...")
    
    for idx, intent in enumerate(intents, 1):
        intent_id = intent.get('id', '')
        display_name = intent.get('displayName', '')
        training_phrases = intent.get('trainingPhrases', [])
        
        if not display_name:
            logger.debug(f"   [{idx}/{len(intents)}] 跳过：intent 无 displayName")
            continue
        
        # 从内存中查找历史映射（O(1) 查找，无数据库查询）
        existing_mapping = historical_mappings.get(display_name)
        
        if existing_mapping and existing_mapping.kb_id:
            # 找到历史映射，复用 kb_id
            reused_details.append({
                "intent_name": display_name,
                "kb_id": existing_mapping.kb_id,
                "kb_name": existing_mapping.kb_name,
                "from_task": existing_mapping.task_id[:8]
            })
            
            # 【优化】复用的 KB 只生成 JSON，不写数据库（避免数据冗余）
            # 因为映射关系已经在历史任务中存在，无需重复记录
            
            # 添加到 JSON 结果（供 step3_kb_workflow 使用）
            kb_results[display_name] = {
                "intent_id": intent_id,
                "display_name": display_name,
                "kb_id": str(existing_mapping.kb_id),
                "kb_name": existing_mapping.kb_name,
                "status": "success",
                "reused": True,
                "training_phrases_count": len(training_phrases)
            }
            
            reused_count += 1
            
            # 每 20 个显示一次进度
            if idx % 20 == 0:
                logger.info(f"      进度: {idx}/{len(intents)} ({reused_count} 个复用)")
        else:
            # 没有找到历史映射，标记为需要新建
            new_count += 1
    
    # 5. 保存映射 JSON 文件（供 step3_kb_workflow 使用）
    # 注意：复用的 KB 不写数据库，只生成 JSON（避免数据冗余）
    if kb_results:
        qa_output_dir = os.path.join(output_dir, 'qa_knowledge_bases')
        os.makedirs(qa_output_dir, exist_ok=True)
        
        kb_results_file = os.path.join(qa_output_dir, f"kb_per_intent_results_{language}.json")
        
        # 如果文件已存在，合并结果
        if os.path.exists(kb_results_file):
            try:
                with open(kb_results_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                existing_results = existing_data.get('results', {})
                # 合并：新的覆盖旧的
                existing_results.update(kb_results)
                kb_results = existing_results
            except Exception as e:
                logger.warning(f"   读取已有映射文件失败，将创建新文件: {e}")
        
        # 保存结果
        output_data = {
            "language": language,
            "total_intents": len(intents),
            "reused_count": reused_count,
            "results": kb_results
        }
        
        try:
            with open(kb_results_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            logger.info(f"   💾 映射文件已保存: {kb_results_file}")
        except Exception as e:
            logger.error(f"   保存映射文件失败: {e}")
    
    # 6. 输出统计（已简化，不再输出详细日志）
    
    logger.info(f"")
    
    return reused_count, new_count


def get_reused_kb_mappings_summary(db: Session, task_id: str) -> Dict:
    """
    获取当前任务复用的知识库映射摘要
    
    Args:
        db: 数据库会话
        task_id: 任务 ID
        
    Returns:
        摘要信息字典
    """
    mappings = crud.get_kb_mappings(db, task_id, status=KBStatus.CREATED)
    
    return {
        "task_id": task_id,
        "total_mappings": len(mappings),
        "mappings": [
            {
                "intent_name": m.intent_name,
                "kb_id": m.kb_id,
                "kb_name": m.kb_name,
                "language": m.language
            }
            for m in mappings
        ]
    }



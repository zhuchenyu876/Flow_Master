"""
更新 workflow config 中的 knowledge_base_ids
从 kb_per_intent_results_en.json 读取映射关系
"""

import json
import os
import glob
import re

from logger_config import get_logger, is_verbose

logger = get_logger(__name__)
VERBOSE = is_verbose()

if not VERBOSE:
    # 将本文件中的 print 全部视为 DEBUG 日志，正常模式只保留少量 INFO
    def print(*args, **kwargs):  # type: ignore[override]
        try:
            message = " ".join(str(a) for a in args)
        except Exception:
            message = " ".join(map(str, args))
        logger.debug(message)

# 配置
KB_RESULTS_FILE = "output/qa_knowledge_bases/kb_per_intent_results_en.json"
WORKFLOW_CONFIG_DIR = "output/step2_workflow_config"
BACKUP_DIR = "output/step2_workflow_config/backup_before_kb_update"

def load_kb_mapping():
    """加载 intent_id -> kb_id 的映射"""
    
    logger.info("加载知识库映射关系")
    
    with open(KB_RESULTS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    results = data.get("results", {})
    
    # 创建两种映射：
    # 1. intent_id -> kb_id
    # 2. display_name -> kb_id  
    # 3. display_name (小写) -> kb_id (用于模糊匹配)
    
    intent_id_to_kb = {}
    display_name_to_kb = {}
    display_name_lower_to_kb = {}
    
    for display_name, record in results.items():
        if record.get("status") == "success" and record.get("kb_id"):
            intent_id = record.get("intent_id")
            kb_id = record.get("kb_id")
            # 确保 kb_id 是整数
            kb_id = int(kb_id) if isinstance(kb_id, str) else kb_id
            
            if intent_id and kb_id:
                intent_id_to_kb[intent_id] = kb_id
                display_name_to_kb[display_name] = kb_id
                display_name_lower_to_kb[display_name.lower()] = kb_id
    
    logger.info(f"✅ 加载了 {len(intent_id_to_kb)} 个 intent 的知识库映射")
    
    return intent_id_to_kb, display_name_to_kb, display_name_lower_to_kb


def normalize_name_for_matching(name):
    """标准化名称用于匹配"""
    # 移除下划线，转小写
    return name.replace('_', '').lower()


def find_kb_id_for_file(filename, intent_id_to_kb, display_name_to_kb, display_name_lower_to_kb):
    """根据文件名查找对应的 kb_id"""
    
    # 从文件名提取 intent 名称
    # 例如：nodes_config_transactionservicing_accountinfo.json -> transactionservicing_accountinfo
    base_name = os.path.basename(filename)
    
    # 跳过主配置文件
    if base_name == "nodes_config.json" or base_name == "edge_config.json":
        return None, None
    
    match = re.match(r'nodes_config_(.+)\.json', base_name)
    
    if not match:
        return None, None
    
    intent_part = match.group(1)
    
    # 跳过 intent_数字 格式的文件（这些可能是临时或测试文件）
    if re.match(r'intent_\d+$', intent_part):
        return None, None
    
    # 尝试匹配
    # 1. 直接匹配 (小写)
    for display_name, kb_id in display_name_to_kb.items():
        if normalize_name_for_matching(display_name) == normalize_name_for_matching(intent_part):
            return kb_id, display_name
    
    # 2. 部分匹配
    normalized_intent_part = normalize_name_for_matching(intent_part)
    for display_name, kb_id in display_name_to_kb.items():
        normalized_display = normalize_name_for_matching(display_name)
        if normalized_display in normalized_intent_part or normalized_intent_part in normalized_display:
            return kb_id, display_name
    
    return None, None


def update_kb_ids_in_file(filename, kb_id, display_name):
    """更新单个文件中的所有 knowledge_base_ids"""
    
    with open(filename, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    nodes = config.get("nodes", [])
    updated_count = 0
    
    for node in nodes:
        if node.get("type") == "knowledgeAssignment":
            old_kb_ids = node.get("knowledge_base_ids", [])
            # 更新为正确的 kb_id (整数)
            node["knowledge_base_ids"] = [int(kb_id)]
            updated_count += 1
            print(f"     更新节点 '{node.get('name')}': {old_kb_ids} → [{kb_id}]")
    
    # 保存更新后的文件
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    return updated_count


def main():
    """主函数"""
    
    logger.info("Step 3: 更新 Workflow Config 中的知识库 ID")
    
    # 1. 加载映射关系
    intent_id_to_kb, display_name_to_kb, display_name_lower_to_kb = load_kb_mapping()
    
    # 2. 创建备份目录
    os.makedirs(BACKUP_DIR, exist_ok=True)
    print(f"\n📁 备份目录: {BACKUP_DIR}")
    
    # 3. 查找所有 nodes_config 文件
    pattern = os.path.join(WORKFLOW_CONFIG_DIR, "nodes_config*.json")
    config_files = glob.glob(pattern)
    
    print(f"\n📊 找到 {len(config_files)} 个配置文件")
    print("="*80)
    
    # 4. 处理每个文件
    total_updated = 0
    matched_files = 0
    unmatched_files = []
    
    for config_file in config_files:
        filename = os.path.basename(config_file)
        print(f"\n📄 处理: {filename}")
        
        # 备份原文件
        backup_path = os.path.join(BACKUP_DIR, filename)
        with open(config_file, 'r', encoding='utf-8') as f:
            backup_data = f.read()
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(backup_data)
        
        # 查找对应的 kb_id
        kb_id, matched_display_name = find_kb_id_for_file(
            filename, 
            intent_id_to_kb, 
            display_name_to_kb, 
            display_name_lower_to_kb
        )
        
        if kb_id:
            print(f"   ✅ 匹配到: {matched_display_name} (KB ID: {kb_id})")
            updated = update_kb_ids_in_file(config_file, kb_id, matched_display_name)
            total_updated += updated
            matched_files += 1
        else:
            print(f"   ⚠️  未找到匹配的知识库")
            unmatched_files.append(filename)
    
    # 5. 显示结果（对外只保留一行 INFO 概览，其余细节走 DEBUG）
    logger.info(
        f"Step 3: 更新完成 - 共 {len(config_files)} 个配置文件，"
        f"成功匹配并更新 {matched_files} 个文件，更新 {total_updated} 个知识库节点"
    )
    
    if unmatched_files:
        print(f"\n⚠️  未匹配的文件 ({len(unmatched_files)} 个):")
        for filename in unmatched_files:
            print(f"   - {filename}")
        print(f"\n💡 这些文件可能:")
        print(f"   1. 是通用配置文件（如 nodes_config.json）")
        print(f"   2. 对应多个 intent 的聚合配置")
        print(f"   3. 需要手动指定知识库ID")
    
    print(f"\n💾 原始文件已备份到: {BACKUP_DIR}")
    print("="*80)


if __name__ == "__main__":
    main()


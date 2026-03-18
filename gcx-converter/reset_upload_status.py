# -*- coding: utf-8 -*-
"""
重置上传状态
============
功能：
1. 清除所有上传状态
2. 或只清除失败的任务状态
3. 允许重新上传

使用方法：
    # 清除所有状态
    python reset_upload_status.py --language en --all
    
    # 只清除失败的任务
    python reset_upload_status.py --language en --failed-only
    
    # 清除所有语言的状态
    python reset_upload_status.py --all-languages --all

作者：AI Assistant
日期：2025-12-15
"""

import os
import json
import argparse
from typing import Dict
from pathlib import Path
from logger_config import get_logger
logger = get_logger(__name__)

# ========================================
# 配置
# ========================================

STATUS_FILES = {
    "en": "output/batch_upload_status_7_files_en.json",
    "zh": "output/batch_upload_status_7_files_zh-cn.json",
    "zh-hant": "output/batch_upload_status_7_files_zh-hant.json",
}


# ========================================
# 工具函数
# ========================================

def load_status(language: str) -> Dict:
    """加载状态文件"""
    file_path = STATUS_FILES.get(language)
    if not file_path or not os.path.exists(file_path):
        return {"tasks": {}, "summary": {}}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"tasks": {}, "summary": {}}


def save_status(language: str, status_data: Dict):
    """保存状态文件"""
    file_path = STATUS_FILES.get(language)
    if not file_path:
        return False
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)
    return True


def backup_status(language: str):
    """备份状态文件"""
    file_path = STATUS_FILES.get(language)
    if not file_path or not os.path.exists(file_path):
        return None
    
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = file_path.replace(".json", f"_backup_{timestamp}.json")
    
    try:
        import shutil
        shutil.copy2(file_path, backup_path)
        return backup_path
    except:
        return None


def reset_all(language: str, backup: bool = True):
    """清除所有状态"""
    file_path = STATUS_FILES.get(language)
    
    if not os.path.exists(file_path):
        logger.warning(f"状态文件不存在: {file_path}")
        return
    
    # 备份
    if backup:
        backup_path = backup_status(language)
        if backup_path:
            logger.info(f"已备份到: {backup_path}")
    
    # 清空状态
    new_status = {"tasks": {}, "summary": {}}
    save_status(language, new_status)
    logger.info(f"已清空所有状态: {language}")


def reset_failed_only(language: str):
    """
    只清除失败的任务
    """
    status_data = load_status(language)
    
    if not status_data:
        logger.warning(f"未找到状态文件: {language}")
        return
    
    tasks = status_data.get("tasks", {})
    failed_count = 0
    
    # 找出失败的任务
    failed_tasks = []
    for path, info in tasks.items():
        if info.get("final_status") == "failed" or (not info.get("success")):
            failed_tasks.append(path)
            failed_count += 1
    
    # 删除失败的任务
    for path in failed_tasks:
        del tasks[path]
    
    save_status(language, status_data)
    logger.info(f"已清除 {failed_count} 个失败任务: {language}")


def reset_pending_only(language: str):
    """
    只清除待查询的任务（成功创建但未完成）
    """
    status_data = load_status(language)
    
    if not status_data:
        logger.warning(f"未找到状态文件: {language}")
        return
    
    tasks = status_data.get("tasks", {})
    pending_count = 0
    
    # 找出待查询的任务
    pending_tasks = []
    for path, info in tasks.items():
        if info.get("success") and not info.get("final_status"):
            pending_tasks.append(path)
            pending_count += 1
    
    # 删除待查询的任务
    for path in pending_tasks:
        del tasks[path]
    
    save_status(language, status_data)
    logger.info(f"已清除 {pending_count} 个待查询任务: {language}")


# ========================================
# 主函数
# ========================================

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="重置上传状态")
    parser.add_argument("--language", "-l",
                       choices=["en", "zh", "zh-hant"],
                       help="语言版本")
    parser.add_argument("--all", "-a", action="store_true",
                       help="清除所有状态")
    parser.add_argument("--failed-only", "-f", action="store_true",
                       help="只清除失败的任务")
    parser.add_argument("--pending-only", "-p", action="store_true",
                       help="只清除待查询的任务")
    parser.add_argument("--all-languages", "-A", action="store_true",
                       help="应用到所有语言")
    parser.add_argument("--no-backup", action="store_true",
                       help="不备份（慎用）")
    
    args = parser.parse_args()
    
    logger.info("\n" + "="*80)
    logger.info("重置上传状态")
    logger.info("="*80)
    
    # 确认操作
    if args.all and not args.no_backup:
        confirm = input("\n⚠️  警告：这将清除所有状态！确认继续？(yes/no): ")
        if confirm.lower() not in ['yes', 'y']:
            logger.info("已取消")
            return
    
    # 确定要操作的语言
    languages = []
    if args.all_languages:
        languages = list(STATUS_FILES.keys())
    elif args.language:
        languages = [args.language]
    else:
        logger.error("\n❌ 请指定 --language 或 --all-languages")
        parser.print_help()
        return
    
    logger.info(f"\n操作语言: {', '.join(languages)}")
    
    # 执行重置
    for language in languages:
        logger.info(f"\n处理: {language}")
        logger.info("-" * 40)
        
        if args.all:
            reset_all(language, backup=not args.no_backup)
        elif args.failed_only:
            reset_failed_only(language)
        elif args.pending_only:
            reset_pending_only(language)
        else:
            logger.warning(f"请指定操作类型: --all, --failed-only, 或 --pending-only")
            parser.print_help()
            return
    
    logger.info("\n" + "="*80)
    logger.info("重置完成")
    logger.info("="*80 + "\n")


if __name__ == "__main__":
    main()

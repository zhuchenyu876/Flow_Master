# -*- coding: utf-8 -*-
"""
标记已上传的文件
================
功能：
1. 手动标记某些文件为已完成状态
2. 避免重复上传

使用方法：
    # 标记单个文件
    python mark_uploaded.py --file "input/7/exported_flow_xxx.json" --language en
    
    # 标记多个文件
    python mark_uploaded.py --file "input/7/exported_flow_xxx.json" --file "input/7/exported_flow_yyy.json" --language en
    
    # 标记所有文件为已完成
    python mark_uploaded.py --all --language en

作者：AI Assistant
日期：2025-12-15
"""

import os
import json
import argparse
from typing import Dict
from datetime import datetime
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
        logger.error(f"不支持的语言: {language}")
        return False
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)
    return True


def mark_file_as_uploaded(language: str, file_path: str, record_id: int = None):
    """标记文件为已上传"""
    status_data = load_status(language)
    
    if file_path not in status_data["tasks"]:
        status_data["tasks"][file_path] = {}
    
    status_data["tasks"][file_path].update({
        "success": True,
        "final_status": "completed",
        "marked_manually": True,
        "marked_at": datetime.now().isoformat()
    })
    
    if record_id:
        status_data["tasks"][file_path]["record_id"] = record_id
    
    save_status(language, status_data)
    logger.info(f"已标记: {Path(file_path).name}")


def mark_all_as_uploaded(language: str, directory: str = "input/7"):
    """标记目录下所有文件为已上传"""
    if not os.path.exists(directory):
        logger.error(f"目录不存在: {directory}")
        return
    
    json_files = list(Path(directory).glob("exported_flow_*.json"))
    
    if not json_files:
        logger.warning(f"未找到任何 exported_flow_*.json 文件")
        return
    
    logger.info(f"找到 {len(json_files)} 个文件")
    
    confirm = input(f"确认标记所有文件为已上传？(yes/no): ")
    if confirm.lower() not in ['yes', 'y']:
        logger.info("已取消")
        return
    
    status_data = load_status(language)
    
    for json_file in json_files:
        file_name = json_file.name
        relative_path = f"input/{Path(directory).name}/{file_name}"
        
        if relative_path not in status_data["tasks"]:
            status_data["tasks"][relative_path] = {}
        
        status_data["tasks"][relative_path].update({
            "success": True,
            "final_status": "completed",
            "marked_manually": True,
            "marked_at": datetime.now().isoformat()
        })
    
    save_status(language, status_data)
    logger.info(f"已标记 {len(json_files)} 个文件")


def unmark_file(language: str, file_path: str):
    """取消标记（允许重新上传）"""
    status_data = load_status(language)
    
    if file_path in status_data["tasks"]:
        del status_data["tasks"][file_path]
        save_status(language, status_data)
        logger.info(f"已取消标记: {Path(file_path).name}")
    else:
        logger.warning(f"文件未被标记: {Path(file_path).name}")


# ========================================
# 主函数
# ========================================

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="标记已上传的文件")
    parser.add_argument("--language", "-l", required=True, 
                       choices=["en", "zh", "zh-hant"],
                       help="语言版本")
    parser.add_argument("--file", "-f", action="append",
                       help="要标记的文件路径（可多次使用）")
    parser.add_argument("--all", "-a", action="store_true",
                       help="标记所有文件")
    parser.add_argument("--unmark", "-u", action="store_true",
                       help="取消标记（允许重新上传）")
    parser.add_argument("--directory", "-d", default="input/7",
                       help="输入目录（默认: input/7）")
    parser.add_argument("--record-id", "-r", type=int,
                       help="Record ID（可选）")
    
    args = parser.parse_args()
    
    logger.info("\n" + "="*80)
    logger.info("标记已上传文件")
    logger.info("="*80)
    logger.info(f"语言: {args.language}")
    
    if args.all:
        # 标记所有文件
        mark_all_as_uploaded(args.language, args.directory)
    elif args.file:
        # 标记指定文件
        for file_path in args.file:
            if args.unmark:
                unmark_file(args.language, file_path)
            else:
                mark_file_as_uploaded(args.language, file_path, args.record_id)
    else:
        logger.warning("请指定 --file 或 --all")
        parser.print_help()
    
    logger.info("="*80 + "\n")


if __name__ == "__main__":
    main()

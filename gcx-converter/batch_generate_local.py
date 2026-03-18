# -*- coding: utf-8 -*-
"""
批量生成本地文件（不上传）
======================================
功能：
1. 读取指定目录中的所有 exported_flow JSON 文件
2. 为每个文件生成转换后的本地 JSON 文件
3. 不创建知识库，不上传到网站

使用方法：
    # 处理 input/7/ 目录，生成英文版本
    python batch_generate_local.py -i input/7 -l en
    python batch_generate_local.py -i input/1 -l en -s
    # 处理 input/PoC 目录，生成繁体中文版本
    python batch_generate_local.py -i "input/PoC Flow Data By Journey - 23" -l zh-hant

    # 处理指定目录，生成所有语言版本
    python batch_generate_local.py -i input/7 -l all

作者：AI Assistant
日期：2025-01-06
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List

from run_all_steps_api import run_all_steps, RunAllStepsResult
from logger_config import get_logger

logger = get_logger(__name__)

# 支持的语言
SUPPORTED_LANGUAGES = ['en', 'zh', 'zh-hant']


def load_status(status_file: str) -> Dict:
    """加载处理状态"""
    default_status = {"tasks": {}, "summary": {}}
    if os.path.exists(status_file):
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                if "tasks" not in loaded_data:
                    loaded_data["tasks"] = {}
                if "summary" not in loaded_data:
                    loaded_data["summary"] = {}
                return loaded_data
        except json.JSONDecodeError:
            logger.warning(f"  ⚠️  状态文件 {status_file} 格式错误，将重新初始化。")
            return default_status
        except Exception as e:
            logger.error(f"  ❌ 加载状态文件 {status_file} 失败: {e}")
            return default_status
    return default_status


def save_status(status_data: Dict, status_file: str):
    """保存处理状态"""
    os.makedirs(os.path.dirname(status_file), exist_ok=True)
    with open(status_file, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)


def process_files(input_dir: str, language: str, output_dir: str = "output", 
                  force: bool = False, verbose: bool = True,
                  intent_version: int = 1) -> Dict:
    """
    批量处理文件
    
    Args:
        input_dir: 输入文件夹路径
        language: 目标语言 (en/zh/zh-hant)
        output_dir: 输出目录
        force: 是否强制重新处理已完成的文件
        verbose: 是否显示详细输出
        intent_version: 意图识别版本 (1=condition节点, 2=semantic节点)
    
    Returns:
        处理结果统计
    """
    version_name = "semantic" if intent_version == 2 else "condition"
    status_file = f"output/batch_generate_local_status_{language}_{version_name}.json"
    
    logger.info("\n" + "="*80)
    logger.info(f"批量生成本地文件（只生成，不上传）")
    logger.info("="*80)
    logger.info(f"📂 输入目录: {input_dir}")
    logger.info(f"🌐 语言: {language}")
    logger.info(f"📁 输出目录: {output_dir}")
    logger.info(f"🔧 意图版本: {intent_version} ({version_name})")
    logger.info(f"🔄 强制重新处理: {'是' if force else '否'}")
    
    # 验证目录
    if not os.path.exists(input_dir):
        logger.error(f"\n❌ 输入目录不存在: {input_dir}")
        return {"success": 0, "failed": 0, "skipped": 0, "total": 0}
    
    # 查找所有 JSON 文件
    json_files = list(Path(input_dir).glob("exported_flow_*.json"))
    
    if not json_files:
        logger.error(f"\n❌ 未找到任何 exported_flow_*.json 文件")
        return {"success": 0, "failed": 0, "skipped": 0, "total": 0}
    
    logger.info(f"\n📁 找到 {len(json_files)} 个文件")
    
    # 加载状态
    status_data = load_status(status_file)
    
    logger.info("\n" + "-"*80)
    
    success_count = 0
    failed_count = 0
    skipped_count = 0
    
    for idx, json_file in enumerate(json_files, 1):
        file_name = json_file.name
        full_file_path = str(json_file.resolve())
        
        logger.info(f"\n[{idx}/{len(json_files)}] {file_name}")
        
        # 检查是否已处理（除非强制重新处理）
        if not force and full_file_path in status_data.get("tasks", {}):
            existing = status_data["tasks"][full_file_path]
            if existing.get("success") and existing.get("final_status") == "completed":
                logger.info(f"  ⏭️  已完成，跳过")
                skipped_count += 1
                continue
        
        # 调用 run_all_steps（只生成本地文件）
        logger.info(f"  🔄 开始处理...")
        try:
            result: RunAllStepsResult = run_all_steps(
                robot_key="",           # 不需要 API 凭证
                robot_token="",         # 不需要 API 凭证
                username="local",       # 本地模式
                exported_flow_file=full_file_path,
                agent_name=f"{Path(file_name).stem}_{language}",
                agent_description="Agent migrated from Dialogflow CX (local generation)",
                language=language,
                create_kb=False,        # ❌ 不创建知识库
                upload_agent=False,     # ❌ 不上传 Agent
                output_base_dir=output_dir,
                verbose=verbose,
                intent_recognition_version=intent_version  # 1=condition, 2=semantic
            )
            
            # 更新状态
            if result.success:
                success_count += 1
                status_data["tasks"][full_file_path] = {
                    "file_name": file_name,
                    "language": language,
                    "success": True,
                    "final_status": "completed",
                    "duration_seconds": result.duration_seconds,
                    "output_dir": result.output_dir,
                    "final_agent_file": result.final_agent_file,
                    "created_at": datetime.now().isoformat()
                }
                logger.info(f"  ✅ 处理成功！耗时: {result.duration_seconds:.2f} 秒")
                logger.info(f"     输出目录: {result.output_dir}")
                if result.final_agent_file:
                    logger.info(f"     最终文件: {result.final_agent_file}")
            else:
                failed_count += 1
                status_data["tasks"][full_file_path] = {
                    "file_name": file_name,
                    "language": language,
                    "success": False,
                    "final_status": "failed",
                    "error": result.message or "未知错误",
                    "errors": result.errors,
                    "duration_seconds": result.duration_seconds,
                    "created_at": datetime.now().isoformat()
                }
                logger.error(f"  ❌ 处理失败: {result.message}")
                if result.errors:
                    for error_msg in result.errors:
                        logger.error(f"     错误: {error_msg}")
            
            save_status(status_data, status_file)
            
        except Exception as e:
            failed_count += 1
            error_msg = f"处理时发生异常: {str(e)}"
            status_data["tasks"][full_file_path] = {
                "file_name": file_name,
                "language": language,
                "success": False,
                "final_status": "failed",
                "error": error_msg,
                "created_at": datetime.now().isoformat()
            }
            save_status(status_data, status_file)
            logger.error(f"  ❌ {error_msg}")
    
    # 总结
    logger.info("\n" + "="*80)
    logger.info("批量生成完成")
    logger.info("="*80)
    logger.info(f"✅ 成功: {success_count}")
    logger.info(f"❌ 失败: {failed_count}")
    logger.info(f"⏭️  跳过: {skipped_count}")
    logger.info(f"📊 总计: {len(json_files)}")
    logger.info(f"\n📁 状态文件: {status_file}")
    logger.info("="*80 + "\n")
    
    # 更新总结
    status_data["summary"] = {
        "total": len(json_files),
        "success": success_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "last_run": datetime.now().isoformat(),
        "language": language,
        "input_dir": input_dir,
        "intent_version": intent_version,
        "version_name": version_name
    }
    save_status(status_data, status_file)
    
    return {
        "success": success_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "total": len(json_files)
    }


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='批量生成本地文件（不上传到网站）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 处理 PoC 23 目录，生成英文 semantic 版本
  python batch_generate_local.py -i "input/PoC Flow Data By Journey - 23" -l en --semantic

  # 处理 PoC 23 目录，生成英文 condition 版本（默认）
  python batch_generate_local.py -i "input/PoC Flow Data By Journey - 23" -l en

  # 处理目录，生成所有语言版本
  python batch_generate_local.py -i input/7 -l all --semantic

  # 强制重新处理（忽略之前的完成状态）
  python batch_generate_local.py -i input/7 -l en --force --semantic
        """
    )
    
    parser.add_argument('-i', '--input-dir', type=str, required=True,
                        help='输入文件夹路径（包含 exported_flow_*.json 文件）')
    parser.add_argument('-l', '--language', type=str, default='en',
                        choices=['en', 'zh', 'zh-hant', 'all'],
                        help='目标语言 (en/zh/zh-hant/all)，默认: en')
    parser.add_argument('-o', '--output-dir', type=str, default='output',
                        help='输出目录，默认: output')
    parser.add_argument('--semantic', '-s', action='store_true',
                        help='使用 semantic 语义判断版本 (intent_version=2)，默认使用 condition 版本')
    parser.add_argument('--force', '-f', action='store_true',
                        help='强制重新处理已完成的文件')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='安静模式，减少输出')
    
    args = parser.parse_args()
    
    # 确定意图版本
    intent_version = 2 if args.semantic else 1
    
    # 确定要处理的语言列表
    if args.language == 'all':
        languages = SUPPORTED_LANGUAGES
    else:
        languages = [args.language]
    
    # 处理每种语言
    all_results = {}
    for lang in languages:
        logger.info(f"\n{'#'*80}")
        logger.info(f"# 处理语言: {lang}")
        logger.info(f"{'#'*80}")
        
        result = process_files(
            input_dir=args.input_dir,
            language=lang,
            output_dir=args.output_dir,
            force=args.force,
            verbose=not args.quiet,
            intent_version=intent_version
        )
        all_results[lang] = result
    
    # 如果处理了多种语言，显示总结
    if len(languages) > 1:
        logger.info("\n" + "="*80)
        logger.info("所有语言处理完成 - 总结")
        logger.info("="*80)
        total_success = sum(r['success'] for r in all_results.values())
        total_failed = sum(r['failed'] for r in all_results.values())
        total_skipped = sum(r['skipped'] for r in all_results.values())
        
        for lang, result in all_results.items():
            logger.info(f"  {lang}: ✅{result['success']} ❌{result['failed']} ⏭️{result['skipped']}")
        
        logger.info("-"*40)
        logger.info(f"  总计: ✅{total_success} ❌{total_failed} ⏭️{total_skipped}")
        logger.info("="*80)


if __name__ == "__main__":
    main()


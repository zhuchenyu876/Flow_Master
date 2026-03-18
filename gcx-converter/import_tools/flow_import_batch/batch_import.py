# -*- coding: utf-8 -*-
"""
Flow 批量导入工具
=================
功能：批量导入多个 workflow JSON 文件到 Dyna.ai 平台

使用方法：
1. 修改脚本中的 IMPORT_DIR 变量，指向要导入的文件目录
2. 运行: python batch_import.py

作者：AI Assistant
日期：2025-01-XX
"""

import os
import sys
from pathlib import Path
from flow_import import load_config, import_flow


def batch_import(directory: str, pattern: str = "generated_workflow_*.json", dry_run: bool = False):
    """
    批量导入目录中的所有匹配文件
    
    Args:
        directory: 要导入的文件目录
        pattern: 文件匹配模式（默认：generated_workflow_*.json）
        dry_run: 如果为 True，只检查不上传
    """
    # 加载配置
    config = load_config()
    
    # 转换为绝对路径
    if not os.path.isabs(directory):
        script_dir = Path(__file__).parent
        directory = os.path.join(script_dir, directory)
        directory = os.path.abspath(directory)
    
    if not os.path.exists(directory):
        print(f"  ❌ 目录不存在: {directory}")
        return
    
    # 查找所有匹配的文件
    import_dir = Path(directory)
    files = list(import_dir.glob(pattern))
    
    if not files:
        print(f"  ⚠️  在目录 {directory} 中未找到匹配 {pattern} 的文件")
        return
    
    print(f"\n  📁 找到 {len(files)} 个文件")
    print(f"     目录: {directory}")
    print(f"     模式: {pattern}")
    
    if dry_run:
        print("\n  🔍 [检查模式] 将导入以下文件:")
        for f in files:
            print(f"     - {f.name}")
        return
    
    # 统计信息
    success_count = 0
    failed_count = 0
    failed_files = []
    
    # 逐个导入
    for i, file_path in enumerate(sorted(files), 1):
        print(f"\n{'='*80}")
        print(f"[{i}/{len(files)}] 导入: {file_path.name}")
        print('='*80)
        
        success = import_flow(str(file_path), config, dry_run=False)
        
        if success:
            success_count += 1
        else:
            failed_count += 1
            failed_files.append(file_path.name)
    
    # 输出统计结果
    print("\n" + "="*80)
    print("📊 导入统计")
    print("="*80)
    print(f"  总计: {len(files)} 个文件")
    print(f"  ✅ 成功: {success_count} 个")
    print(f"  ❌ 失败: {failed_count} 个")
    
    if failed_files:
        print(f"\n  失败的文件:")
        for f in failed_files:
            print(f"    - {f}")


def main():
    """主函数"""
    print("="*80)
    print("🚀 Flow 批量导入工具")
    print("="*80)
    
    # 配置要导入的目录（相对于脚本目录）
    # 可以根据需要修改这些变量
    IMPORT_DIR = "../output/step7_final/en"  # 要导入的文件目录
    FILE_PATTERN = "generated_workflow_*.json"  # 文件匹配模式
    
    # 检查命令行参数
    dry_run = '--dry-run' in sys.argv or '-d' in sys.argv
    
    # 如果提供了目录参数，使用提供的目录
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        IMPORT_DIR = sys.argv[1]
    
    # 如果提供了模式参数，使用提供的模式
    if len(sys.argv) > 2 and not sys.argv[2].startswith('-'):
        FILE_PATTERN = sys.argv[2]
    
    # 执行批量导入
    batch_import(IMPORT_DIR, FILE_PATTERN, dry_run=dry_run)


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""
批量为Python文件添加水印脚本
==========================

为项目中所有Python文件添加作者水印 "chenyu.zhu"

作者：chenyu.zhu
日期：2025-12-17
"""

import os
import re
from pathlib import Path

def add_watermark_to_file(file_path):
    """为单个Python文件添加水印"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查是否已经有chenyu.zhu水印
        if 'chenyu.zhu' in content:
            print(f"⏭️  跳过 {file_path} (已有水印)")
            return False

        lines = content.split('\n')
        insert_pos = 0

        # 查找文档字符串的结束位置
        in_docstring = False
        docstring_delimiter = None

        for i, line in enumerate(lines):
            stripped = line.strip()

            # 查找文档字符串开始
            if not in_docstring and stripped.startswith('"""'):
                in_docstring = True
                docstring_delimiter = '"""'
                insert_pos = i
            elif not in_docstring and stripped.startswith("'''"):
                in_docstring = True
                docstring_delimiter = "'''"
                insert_pos = i

            # 查找文档字符串结束
            if in_docstring and stripped.endswith(docstring_delimiter):
                insert_pos = i + 1
                break

        # 如果没有找到文档字符串，在文件开头添加
        if insert_pos == 0:
            # 添加文档字符串头部
            header = f'''"""
{Path(file_path).stem} 模块

作者：chenyu.zhu
日期：2025-12-17
"""

'''
            new_content = header + content
        else:
            # 在文档字符串后添加作者信息
            author_info = [
                "",
                "作者：chenyu.zhu",
                "日期：2025-12-17",
                ""
            ]

            # 在insert_pos位置插入作者信息
            lines[insert_pos:insert_pos] = author_info
            new_content = '\n'.join(lines)

        # 写回文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        print(f"✅ 已添加水印: {file_path}")
        return True

    except Exception as e:
        print(f"❌ 处理失败 {file_path}: {e}")
        return False

def main():
    """主函数"""
    print("🔧 开始批量添加水印...")

    # 获取项目根目录
    root_dir = Path(__file__).parent

    # 查找所有Python文件
    python_files = []
    for ext in ['**/*.py']:
        python_files.extend(root_dir.glob(ext))

    # 排除一些不需要的文件
    exclude_patterns = [
        'add_watermark.py',  # 排除自己
        '__pycache__',
        '.git',
        'node_modules',
        'output',
        '*.pyc'
    ]

    filtered_files = []
    for file_path in python_files:
        should_exclude = False
        for pattern in exclude_patterns:
            if pattern in str(file_path):
                should_exclude = True
                break
        if not should_exclude:
            filtered_files.append(file_path)

    print(f"📁 找到 {len(filtered_files)} 个Python文件待处理")

    # 处理每个文件
    processed_count = 0
    for file_path in sorted(filtered_files):
        if add_watermark_to_file(file_path):
            processed_count += 1

    print(f"\n🎉 完成！共处理了 {processed_count} 个文件")

if __name__ == "__main__":
    main()
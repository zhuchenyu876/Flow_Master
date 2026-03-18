"""
清空知识库映射数据库脚本
=========================

用途：清理历史的知识库映射记录，方便重新测试

⚠️ 重要提示：
    - 本脚本只清空**数据库中的映射记录**
    - 不会删除 Dyna.ai 平台上的实际知识库
    - 如果 Dyna.ai 中知识库仍存在，重新迁移时会遇到 "already exists" 错误
    - 建议同时清空 Dyna.ai 中的知识库，或使用方案 2（保留数据库映射）

使用方法：
    python scripts/clear_kb_mappings.py --all                    # 清空所有映射
    python scripts/clear_kb_mappings.py --robot-key "xxx"        # 清空特定 robot_key 的映射
    python scripts/clear_kb_mappings.py --task-id "xxx"          # 清空特定 task_id 的映射
    python scripts/clear_kb_mappings.py --before "2025-12-01"    # 清空指定日期之前的映射
    python scripts/clear_kb_mappings.py --dry-run --all          # 预览模式（不实际删除）

注意：
    - 删除操作不可逆，请谨慎使用
    - 建议先使用 --dry-run 预览
"""

import argparse
import sys
import os
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.connection import get_db
from database import crud
from database.models import KnowledgeBaseMapping, MigrationTask
from logger_config import get_logger

logger = get_logger(__name__)


def confirm_deletion(message: str) -> bool:
    """确认删除操作"""
    print(f"\n⚠️  {message}")
    response = input("确认删除？(yes/no): ").strip().lower()
    return response in ['yes', 'y']


def clear_all_mappings(db, dry_run=False):
    """清空所有知识库映射"""
    mappings = db.query(KnowledgeBaseMapping).all()
    count = len(mappings)
    
    print(f"\n📊 将删除 {count} 条知识库映射记录")
    
    if count == 0:
        print("✅ 数据库已经是空的")
        return
    
    # 显示统计信息
    robot_keys = db.query(MigrationTask.robot_key).distinct().count()
    tasks = db.query(MigrationTask).count()
    
    print(f"   - 涉及 {robot_keys} 个 robot_key")
    print(f"   - 涉及 {tasks} 个迁移任务")
    
    if dry_run:
        print("\n🔍 预览模式：不会实际删除")
        print("\n前 10 条记录：")
        for i, mapping in enumerate(mappings[:10], 1):
            print(f"   {i}. Task: {mapping.task_id[:8]}... | Intent: {mapping.intent_name} | KB: {mapping.kb_id}")
        if count > 10:
            print(f"   ... (还有 {count - 10} 条)")
        return
    
    if not confirm_deletion(f"即将删除所有 {count} 条映射记录"):
        print("❌ 操作已取消")
        return
    
    # 删除所有映射
    db.query(KnowledgeBaseMapping).delete()
    db.commit()
    
    print(f"✅ 已删除 {count} 条知识库映射记录")
    logger.info(f"清空了所有知识库映射 ({count} 条)")


def clear_by_robot_key(db, robot_key: str, dry_run=False):
    """清空特定 robot_key 的知识库映射"""
    # 查找所有相关任务
    tasks = db.query(MigrationTask).filter(MigrationTask.robot_key == robot_key).all()
    
    if not tasks:
        print(f"❌ 未找到 robot_key='{robot_key}' 的任务")
        return
    
    task_ids = [task.id for task in tasks]
    
    # 查找所有相关映射
    mappings = db.query(KnowledgeBaseMapping).filter(
        KnowledgeBaseMapping.task_id.in_(task_ids)
    ).all()
    
    count = len(mappings)
    
    print(f"\n📊 robot_key: {robot_key}")
    print(f"   - 找到 {len(tasks)} 个相关任务")
    print(f"   - 找到 {count} 条知识库映射记录")
    
    if count == 0:
        print("✅ 没有需要删除的记录")
        return
    
    if dry_run:
        print("\n🔍 预览模式：不会实际删除")
        print(f"\n任务列表:")
        for task in tasks[:5]:
            print(f"   - {task.id[:8]}... ({task.created_at})")
        if len(tasks) > 5:
            print(f"   ... (还有 {len(tasks) - 5} 个任务)")
        
        print(f"\n前 10 条映射:")
        for i, mapping in enumerate(mappings[:10], 1):
            print(f"   {i}. Task: {mapping.task_id[:8]}... | Intent: {mapping.intent_name} | KB: {mapping.kb_id}")
        if count > 10:
            print(f"   ... (还有 {count - 10} 条)")
        return
    
    if not confirm_deletion(f"即将删除 robot_key='{robot_key}' 的 {count} 条映射记录"):
        print("❌ 操作已取消")
        return
    
    # 删除映射
    db.query(KnowledgeBaseMapping).filter(
        KnowledgeBaseMapping.task_id.in_(task_ids)
    ).delete(synchronize_session=False)
    db.commit()
    
    print(f"✅ 已删除 {count} 条知识库映射记录")
    logger.info(f"清空了 robot_key={robot_key} 的知识库映射 ({count} 条)")


def clear_by_task_id(db, task_id: str, dry_run=False):
    """清空特定 task_id 的知识库映射"""
    # 查找任务
    task = db.query(MigrationTask).filter(MigrationTask.id == task_id).first()
    
    if not task:
        print(f"❌ 未找到 task_id='{task_id}' 的任务")
        return
    
    # 查找映射
    mappings = db.query(KnowledgeBaseMapping).filter(
        KnowledgeBaseMapping.task_id == task_id
    ).all()
    
    count = len(mappings)
    
    print(f"\n📊 task_id: {task_id}")
    print(f"   - robot_key: {task.robot_key}")
    print(f"   - 创建时间: {task.created_at}")
    print(f"   - 找到 {count} 条知识库映射记录")
    
    if count == 0:
        print("✅ 没有需要删除的记录")
        return
    
    if dry_run:
        print("\n🔍 预览模式：不会实际删除")
        print(f"\n前 10 条映射:")
        for i, mapping in enumerate(mappings[:10], 1):
            print(f"   {i}. Intent: {mapping.intent_name} | KB: {mapping.kb_id} | Status: {mapping.status}")
        if count > 10:
            print(f"   ... (还有 {count - 10} 条)")
        return
    
    if not confirm_deletion(f"即将删除 task_id='{task_id}' 的 {count} 条映射记录"):
        print("❌ 操作已取消")
        return
    
    # 删除映射
    db.query(KnowledgeBaseMapping).filter(
        KnowledgeBaseMapping.task_id == task_id
    ).delete()
    db.commit()
    
    print(f"✅ 已删除 {count} 条知识库映射记录")
    logger.info(f"清空了 task_id={task_id} 的知识库映射 ({count} 条)")


def clear_before_date(db, before_date: str, dry_run=False):
    """清空指定日期之前的知识库映射"""
    try:
        cutoff_date = datetime.strptime(before_date, "%Y-%m-%d")
    except ValueError:
        print(f"❌ 日期格式错误：{before_date}，应该是 YYYY-MM-DD 格式")
        return
    
    # 查找映射
    mappings = db.query(KnowledgeBaseMapping).filter(
        KnowledgeBaseMapping.created_at < cutoff_date
    ).all()
    
    count = len(mappings)
    
    print(f"\n📊 清空 {before_date} 之前的映射记录")
    print(f"   - 找到 {count} 条记录")
    
    if count == 0:
        print("✅ 没有需要删除的记录")
        return
    
    # 统计涉及的任务
    task_ids = set(m.task_id for m in mappings)
    print(f"   - 涉及 {len(task_ids)} 个任务")
    
    if dry_run:
        print("\n🔍 预览模式：不会实际删除")
        print(f"\n前 10 条映射:")
        for i, mapping in enumerate(sorted(mappings, key=lambda m: m.created_at)[:10], 1):
            print(f"   {i}. {mapping.created_at} | Task: {mapping.task_id[:8]}... | Intent: {mapping.intent_name}")
        if count > 10:
            print(f"   ... (还有 {count - 10} 条)")
        return
    
    if not confirm_deletion(f"即将删除 {before_date} 之前的 {count} 条映射记录"):
        print("❌ 操作已取消")
        return
    
    # 删除映射
    db.query(KnowledgeBaseMapping).filter(
        KnowledgeBaseMapping.created_at < cutoff_date
    ).delete()
    db.commit()
    
    print(f"✅ 已删除 {count} 条知识库映射记录")
    logger.info(f"清空了 {before_date} 之前的知识库映射 ({count} 条)")


def main():
    parser = argparse.ArgumentParser(
        description='清空知识库映射数据库',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/clear_kb_mappings.py --all --dry-run        # 预览所有记录
  python scripts/clear_kb_mappings.py --all                  # 清空所有记录
  python scripts/clear_kb_mappings.py --robot-key "xxx"      # 清空特定 robot_key
  python scripts/clear_kb_mappings.py --task-id "xxx"        # 清空特定任务
  python scripts/clear_kb_mappings.py --before "2025-12-01"  # 清空指定日期前的记录
        """
    )
    
    parser.add_argument('--all', action='store_true', help='清空所有映射记录')
    parser.add_argument('--robot-key', type=str, help='清空特定 robot_key 的映射')
    parser.add_argument('--task-id', type=str, help='清空特定 task_id 的映射')
    parser.add_argument('--before', type=str, help='清空指定日期之前的映射 (格式: YYYY-MM-DD)')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不实际删除')
    
    args = parser.parse_args()
    
    # 检查是否至少指定了一个选项
    if not (args.all or args.robot_key or args.task_id or args.before):
        parser.print_help()
        print("\n❌ 错误: 请至少指定一个清空选项 (--all, --robot-key, --task-id, --before)")
        sys.exit(1)
    
    # 连接数据库
    print("📡 连接数据库...")
    db = next(get_db())
    
    try:
        if args.all:
            clear_all_mappings(db, dry_run=args.dry_run)
        elif args.robot_key:
            clear_by_robot_key(db, args.robot_key, dry_run=args.dry_run)
        elif args.task_id:
            clear_by_task_id(db, args.task_id, dry_run=args.dry_run)
        elif args.before:
            clear_before_date(db, args.before, dry_run=args.dry_run)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()
        print("\n✅ 数据库连接已关闭")


if __name__ == "__main__":
    main()


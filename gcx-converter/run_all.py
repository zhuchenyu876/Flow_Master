# -*- coding: utf-8 -*-
"""
一键运行所有步骤 - 本地命令行版本
================================
无需启动服务器，直接在本地运行所有迁移步骤

使用方法：
    1. 配置 .env 文件（或直接修改下面的配置）
    2. 运行: python run_all.py

作者：Edison
日期：2025-12-02
"""

import os
import sys

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# 导入日志配置
from logger_config import get_logger, get_current_log_file

# 创建 logger
logger = get_logger(__name__)

from run_all_steps_api import run_all_steps

# ============================================
# 配置区域 - 请根据需要修改以下配置
# ============================================

# API 认证信息（可以从 .env 读取或直接在这里填写）
ROBOT_KEY = os.getenv("ROBOT_KEY", "")  # 你的 robot key
ROBOT_TOKEN = os.getenv("ROBOT_TOKEN", "")  # 你的 robot token
USERNAME = os.getenv("USERNAME", "")  # 你的用户名/邮箱

# 输入文件路径（Google Dialogflow CX 导出的 JSON 文件）
origin_file = r'input\PoC Flow Data By Journey - 23\exported_flow_AccountServicing_FAQ.json'
origin_file = r'input\PoC Flow Data By Journey - 23\exported_flow_WayToBank_Fulfillment.json'
origin_file = r'input\7\exported_flow_TXNAndSTMT_Fulfillment.json'
EXPORTED_FLOW_FILE = os.getenv("EXPORTED_FLOW_FILE", origin_file)

# Agent 配置
AGENT_NAME = os.getenv("AGENT_NAME", "CardServicing Agent")
AGENT_DESCRIPTION = os.getenv("AGENT_DESCRIPTION", "从 Google Dialogflow CX 迁移的 Agent")

# 语言选择: "en", "zh", "zh-hant"
LANGUAGE = os.getenv("LANGUAGE", "en")

# 功能开关
CREATE_KB = os.getenv("CREATE_KB", "true").lower() == "true"  # 是否创建知识库
UPLOAD_AGENT = os.getenv("UPLOAD_AGENT", "true").lower() == "true"  # 是否上传 Agent

# ============================================
# 主函数
# ============================================

def main():
    """一键运行所有步骤"""
    sys.stdout.reconfigure(encoding='utf-8')
    
    logger.info("=" * 60)
    logger.info("🚀 Dialogflow CX 迁移工具 - 本地运行版")
    logger.info("=" * 60)

    # 检查必要配置
    if not EXPORTED_FLOW_FILE or not os.path.exists(EXPORTED_FLOW_FILE):
        logger.error(f"❌ 错误: 输入文件不存在: {EXPORTED_FLOW_FILE}")
        logger.info("   请检查 EXPORTED_FLOW_FILE 配置")
        return

    # 如果需要上传，检查认证信息
    if UPLOAD_AGENT or CREATE_KB:
        if not ROBOT_KEY or not ROBOT_TOKEN or not USERNAME:
            logger.error("❌ 错误: 上传功能需要配置认证信息")
            logger.info("   请在 .env 文件中设置:")
            logger.info("   - ROBOT_KEY")
            logger.info("   - ROBOT_TOKEN")
            logger.info("   - USERNAME")
            logger.info("")
            logger.info("   或者设置 CREATE_KB=false 和 UPLOAD_AGENT=false 只生成本地文件")
            return

    # 显示配置
    logger.info("\n📋 当前配置:")
    logger.info(f"   输入文件: {EXPORTED_FLOW_FILE}")
    logger.info(f"   Agent 名称: {AGENT_NAME}")
    logger.info(f"   语言: {LANGUAGE}")
    logger.info(f"   创建知识库: {'是' if CREATE_KB else '否'}")
    logger.info(f"   上传 Agent: {'是' if UPLOAD_AGENT else '否'}")
    if UPLOAD_AGENT or CREATE_KB:
        logger.info(f"   用户: {USERNAME}")
    logger.info("")

    # 确认运行
    user_input = input("是否开始运行？(y/n): ").strip().lower()
    if user_input != 'y':
        logger.info("已取消")
        return

    logger.info("\n" + "=" * 60)
    logger.info("🔄 开始执行迁移...")
    logger.info("=" * 60 + "\n")

    # 运行所有步骤
    result = run_all_steps(
        robot_key=ROBOT_KEY,
        robot_token=ROBOT_TOKEN,
        username=USERNAME,
        exported_flow_file=EXPORTED_FLOW_FILE,
        agent_name=AGENT_NAME,
        agent_description=AGENT_DESCRIPTION,
        language=LANGUAGE,
        create_kb=CREATE_KB,
        upload_agent=UPLOAD_AGENT
    )

    # 显示结果
    logger.info("\n" + "=" * 60)
    if result.success:
        logger.info("✅ 迁移完成!")
    else:
        logger.error("❌ 迁移失败!")
    logger.info("=" * 60)

    logger.info(f"\n📝 结果信息: {result.message}")
    logger.info(f"⏱️  耗时: {result.duration_seconds:.2f} 秒")

    if result.steps_completed:
        logger.info(f"\n✅ 完成的步骤:")
        for step in result.steps_completed:
            logger.info(f"   - {step}")

    if result.generated_files:
        logger.info(f"\n📁 生成的文件:")
        for step_name, files in result.generated_files.items():
            logger.info(f"   [{step_name}]")
            if isinstance(files, list):
                for file_path in files:
                    logger.info(f"      - {file_path}")
            elif isinstance(files, dict):
                for key, value in files.items():
                    logger.info(f"      - {key}: {value}")
            else:
                logger.info(f"      - {files}")

    if result.final_agent_file:
        logger.info(f"\n🤖 最终 Agent 文件: {result.final_agent_file}")

    if result.upload_result:
        logger.info(f"\n📤 上传结果:")
        if isinstance(result.upload_result, dict):
            for key, value in result.upload_result.items():
                logger.info(f"   - {key}: {value}")
        else:
            logger.info(f"   {result.upload_result}")

    if result.errors:
        logger.error(f"\n⚠️  错误信息:")
        for error in result.errors:
            logger.error(f"   - {error}")

    logger.info(f"\n📂 输出目录: {result.output_dir}")
    
    # 显示日志文件路径
    log_file = get_current_log_file()
    if log_file:
        logger.info(f"\n📄 运行日志已保存到: {log_file}")

    logger.info("\n" + "=" * 60)
    logger.info("完成!")
    logger.info("=" * 60)


def run_without_prompt():
    """无确认直接运行（用于自动化）"""
    sys.stdout.reconfigure(encoding='utf-8')
    
    logger.info("=" * 60)
    logger.info("🚀 Dialogflow CX 迁移工具 - 自动运行模式")
    logger.info("=" * 60)
    
    # 检查输入文件
    if not EXPORTED_FLOW_FILE or not os.path.exists(EXPORTED_FLOW_FILE):
        logger.error(f"❌ 错误: 输入文件不存在: {EXPORTED_FLOW_FILE}")
        return None
    
    # 运行
    result = run_all_steps(
        robot_key=ROBOT_KEY,
        robot_token=ROBOT_TOKEN,
        username=USERNAME,
        exported_flow_file=EXPORTED_FLOW_FILE,
        agent_name=AGENT_NAME,
        agent_description=AGENT_DESCRIPTION,
        language=LANGUAGE,
        create_kb=CREATE_KB,
        upload_agent=UPLOAD_AGENT
    )
    
    if result.success:
        logger.info("✅ 迁移完成!")
    else:
        logger.error(f"❌ 迁移失败: {result.message}")
    
    # 显示日志文件路径
    log_file = get_current_log_file()
    if log_file:
        logger.info(f"\n📄 运行日志已保存到: {log_file}")
    
    return result


if __name__ == "__main__":
    # 检查是否有 --auto 参数
    if len(sys.argv) > 1 and sys.argv[1] == "--auto":
        run_without_prompt()
    else:
        main()

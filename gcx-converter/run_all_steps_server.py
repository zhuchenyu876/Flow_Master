# -*- coding: utf-8 -*-
"""
Dialogflow CX 迁移工具 - FastAPI 服务（MySQL 版）
=====================================================
使用 MySQL 数据库存储任务状态、知识库映射等信息

启动服务：
    python run_all_steps_server.py

或使用 uvicorn：
    uvicorn run_all_steps_server:app --host 0.0.0.0 --port 8000 --reload

API 文档：
    http://localhost:8000/docs (Swagger UI)
    http://localhost:8000/redoc (ReDoc)

配置文件：
    复制 env.example 为 .env 并修改配置

作者：Edison
日期：2025-12-02
"""

import os
import sys
import json
import re
import traceback
import threading
import shutil
from pathlib import Path
from queue import Queue, Full
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入统一日志配置
from logger_config import get_logger
logger = get_logger(__name__)

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()  # 从 .env 文件加载配置

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from step2.converter import WorkflowConverter, load_intents_with_training_phrases
# from step2_workflow_converter import WorkflowConverter
try:
    # Pydantic v2+
    from pydantic import ConfigDict
except ImportError:  # 兼容老版本
    ConfigDict = dict
from sqlalchemy.orm import Session

# 数据库模块
from database import (
    get_db, init_db, db_manager,
    MigrationTask, TaskStep, KnowledgeBaseMapping, TaskError,
    TaskStatus, StepStatus, KBStatus, STEP_NAMES
)
from database import crud


# ========================================
# Pydantic 模型定义
# ========================================

class LanguageEnum(str, Enum):
    EN = "en"
    ZH = "zh"
    ZH_HANT = "zh-hant"


class GoogleConvertRequest(BaseModel):
    """Google CX 转换请求模型"""
    record_id: int = Field(..., description="任务ID")
    file_path: List[str] = Field(..., description="Dialogflow CX 导出的 flow JSON 文件路径列表（支持单文件或多文件）")
    username: str = Field(..., description="用户名")
    language: str = Field(..., description="目标语言: en, zh, zh-hant")
    cybertron_robot_key: str = Field(..., alias="cybertron-robot-key", description="Robot Key")
    cybertron_robot_token: str = Field(..., alias="cybertron-robot-token", description="Robot Token")
    name: str = Field(default="", description="Agent 名称")
    avatar_name: str = Field(default="", description="Agent 头像名称")
    avatar_color: str = Field(default="", description="Agent 头像颜色")
    description: str = Field(default="", description="Agent 描述")
    emb_language: str = Field(default="", description="嵌入语言")
    emb_model: str = Field(default="", description="嵌入模型")
    faq_version: str = Field(default="Knowledge Base Version", description="意图判断版本: Knowledge Base Version, Semantic Judgement Version")
    is_debug: bool = Field(default=False, description="是否为调试模式: true, false")
    semantic_confidence: float = Field(default=0.5, description="语义判断embedding模型置信度: 0.0-1.0")
    enable_short_memory: bool = Field(default=False, description="是否启用短期记忆: true, false")
    short_chat_count: int = Field(default=5, description="短记忆聊天次数")
    enable_global_intent: bool = Field(default=False, description="是否启用全局意图: true, false")
    llmcodemodel: str = Field(default="qwen3-30b-a3b", description="LLM节点模型")
    use_sft_model: bool = Field(default=False, description="是否启用SFT模型: true, false")
    sft_model_name: str = Field(default="internal0-br-llm-hsbcllm-v1-20250105", description="SFT模型名称")
    ner_version: str = Field(default="llm", description="NER版本: llm, semantic")
    
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "record_id": 123,
                "file_path": ["input/exported_flow_1.json", "input/exported_flow_2.json"],
                "username": "user@example.com",
                "language": "zh",
                "cybertron-robot-key": "your_robot_key",
                "cybertron-robot-token": "your_robot_token",
                "name": "My Agent",
                "avatar_name": "5",
                "avatar_color": "5",
                "description": "Agent description",
                "emb_language": "chinese",
                "emb_model": "text-embedding-ada-002",
            }
        },
    )


class GoogleConvertResponse(BaseModel):
    """Google CX 转换响应模型"""
    code: str = Field(..., description="状态码: 0成功, 其他失败")
    message: str = Field(..., description="状态消息")


class TaskDetailResponse(BaseModel):
    """任务详情响应"""
    task_id: str
    record_id: int
    status: str
    message: str
    language: str
    current_step: int
    current_step_name: Optional[str]
    progress_percent: float
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    version: str = "2.0.0"
    database: str = "connected"
    timestamp: str


# ========================================
# 配置（从 .env 文件读取）
# ========================================

# 任务完成后的回调通知地址（可选，留空则跳过回调）
CALLBACK_URL = os.getenv("CALLBACK_URL", "")

# 最大并发任务数（线程池大小，默认串行 1）
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "1"))

# 最大排队任务数（0 表示不限制，默认 100）
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "100"))

# 任务队列与工作线程
task_queue: "Queue[tuple[str, GoogleConvertRequest]]" = Queue(
    maxsize=MAX_QUEUE_SIZE if MAX_QUEUE_SIZE > 0 else 0
)
workers_started = False

# 启动固定数量的工作线程，确保并发不超过 MAX_WORKERS
def start_task_workers():
    global workers_started
    if workers_started:
        return
    for i in range(MAX_WORKERS):
        t = threading.Thread(target=task_worker, name=f"convert-worker-{i+1}", daemon=True)
        t.start()
    workers_started = True
    logger.debug(f"🎯 任务队列已启动，最大并发: {MAX_WORKERS}")


def task_worker():
    """工作线程：从队列中取出任务并执行"""
    while True:
        task = task_queue.get()
        if task is None:
            break
        task_id, request = task
        try:
            logger.info(f"start task exec,  task_id={request.record_id}")
            # workflow迁移：执行所有步骤的迁移动作
            execute_convert_task(task_id, request)
        except Exception as e:
            logger.error(f"任务执行异常 task_id={task_id}: {e}", exc_info=True)
        finally:
            task_queue.task_done()

# 服务配置
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))


# ========================================
# FastAPI 应用
# ========================================

app = FastAPI(
    title="Dialogflow CX 工作流迁移工具 API",
    description="""
## 功能说明

将 Dialogflow CX 的 Agent 工作流转换为目标平台格式（支持 n8n、Dify、Coze 等）。

### 主要接口

- `POST /google_convert/task/create` - 创建迁移任务（异步执行）
- `GET /google_convert/task/{record_id}` - 查询任务状态
- `GET /google_convert/tasks` - 列出所有任务

### 处理流程

1. 接收请求，创建任务
2. 后台执行迁移（提取数据 → 转换 → 创建知识库 → 生成工作流 JSON）
3. 完成后回调通知（可选）
    """,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========================================
# 启动事件
# ========================================

@app.on_event("startup")
async def startup_event():
    """启动时初始化数据库与任务队列"""
    try:
        logger.info("🚀 初始化数据库...")
        init_db()
        logger.info("✅ 数据库初始化完成")
        start_task_workers()
    except Exception as e:
        logger.error(f"❌ 启动失败: {e}", exc_info=True)
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """关闭时清理资源"""
    try:
        logger.info("🛑 正在关闭服务器...")
        # 等待任务队列完成
        task_queue.join()
        logger.info("✅ 服务器已优雅关闭")
    except Exception as e:
        logger.warning(f"⚠️  关闭时发生错误: {e}")


# ========================================
# 健康检查
# ========================================

@app.get("/", response_model=HealthResponse, tags=["健康检查"])
async def root(db: Session = Depends(get_db)):
    """服务健康检查"""
    try:
        db.execute("SELECT 1")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"
    
    return HealthResponse(
        status="ok",
        version="2.0.0",
        database=db_status,
        timestamp=datetime.now().isoformat()
    )


@app.get("/health", response_model=HealthResponse, tags=["健康检查"])
async def health_check(db: Session = Depends(get_db)):
    """健康检查"""
    return await root(db)


@app.get("/ping", tags=["健康检查"])
async def ping():
    """
    简单健康检查，用于 k8s liveness/readinessProbe
    """
    return {"status": "ok"}


# ========================================
# 主接口：Google CX 转换
# ========================================

@app.post("/google_convert/task/create", response_model=GoogleConvertResponse, tags=["迁移"])
async def create_convert_task(
    request: GoogleConvertRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    创建 Google CX 转换任务
    
    - 异步执行迁移流程
    - 完成后回调通知
    - 只处理指定语言的内容
    - 支持单文件或多文件输入（通过列表传入）
    """
    # 获取文件路径列表
    file_paths = request.file_path
    
    # 验证文件列表
    if not file_paths:
        return GoogleConvertResponse(
            code="400",
            message="未提供文件路径，请在 file_path 参数中提供文件路径列表"
        )
    
    # 验证所有文件是否存在
    missing_files = [fp for fp in file_paths if not os.path.exists(fp)]
    if missing_files:
        return GoogleConvertResponse(
            code="400",
            message=f"以下文件不存在: {', '.join(missing_files)}"
        )
    
    # 验证语言
    supported_languages = ['en', 'zh', 'zh-hant']
    if request.language not in supported_languages:
        return GoogleConvertResponse(
            code="400",
            message=f"不支持的语言: {request.language}。支持: {', '.join(supported_languages)}"
        )
    
    # workflow迁移步骤：确保工作线程已启动，该任务执行所有迁移步骤
    start_task_workers()

    # 队列容量检查，避免无限排队
    if MAX_QUEUE_SIZE > 0 and task_queue.full():
        return GoogleConvertResponse(
            code="429",
            message=f"任务排队已达上限({MAX_QUEUE_SIZE})，请稍后再试"
        )
    
    # 生成 task_id（在数据库操作之前，避免数据库操作阻塞接口）
    import uuid
    task_id = str(uuid.uuid4())
    
    try:
        # 创建任务记录（添加超时保护）
        try:
            # writed by senlin.den 2026-01-30
            # 多文件时存储为 JSON 格式（只保留文件名，避免路径过长）
            if len(file_paths) > 1:
                exported_flow_file_str = json.dumps([os.path.basename(fp) for fp in file_paths])
            else:
                exported_flow_file_str = file_paths[0]
            exported_flow_file_str = exported_flow_file_str[:500].strip()
            
            task = crud.create_task(
                db=db,
                robot_key=request.cybertron_robot_key,
                robot_token=request.cybertron_robot_token,
                username=request.username,
                exported_flow_file=exported_flow_file_str,
                agent_name=request.name or "Migrated Agent",
                agent_description=request.description or "",
                language=request.language,
                create_kb=True,
                upload_agent=False,
                base_url="",
                output_dir="output"
            )
            # 使用返回的 task_id（如果 crud.create_task 内部生成）
            if hasattr(task, 'task_id'):
                task_id = task.task_id
            
            # 保存 record_id 到任务（使用 message 字段临时存储额外信息）
            extra_info = {
                "record_id": request.record_id,
                "avatar_name": request.avatar_name,
                "avatar_color": request.avatar_color,
                "emb_language": request.emb_language,
                "emb_model": request.emb_model
            }
            crud.update_task_status(db, task_id, TaskStatus.PENDING, json.dumps(extra_info))
            logger.info(f"✅ 数据库记录创建成功: task_id={task_id}")
        except Exception as db_error:
            # 数据库操作失败，记录错误但继续入队（任务执行时会尝试创建记录）
            # logger.error(f"⚠️ 数据库操作失败，但继续入队: {db_error}", exc_info=True)
            # 如果数据库操作失败，使用预生成的 task_id
            # 任务执行时会尝试创建或更新数据库记录
            # writed by senlin.den 2026-01-30
            # 数据库操作失败，直接返回错误
            logger.error(f"❌ 数据库操作异常: {db_error}", exc_info=True)
            return GoogleConvertResponse(
                code="500",
                message=f"数据库操作失败: {str(db_error)}"
            )
        
        # 将任务放入队列，由固定线程池处理（最大并发 MAX_WORKERS）
        # 这一步必须成功，否则接口会卡住
        try:
            task_queue.put_nowait((task_id, request))
            logger.debug(
                f"📥 任务已入队 task_id={task_id} | queue_size={task_queue.qsize()} | "
                f"max_workers={MAX_WORKERS}"
            )
        except Full:
            # 极端情况下并发检查之后队列被填满，返回友好提示
            return GoogleConvertResponse(
                code="429",
                message=f"任务排队已达上限({MAX_QUEUE_SIZE})，请稍后再试"
            )
        
        return GoogleConvertResponse(
            code="0",
            message=f"任务已创建并入队，task_id: {task_id}"
        )
        
    except Exception as e:
        logger.error(f"❌ 创建任务异常: {e}", exc_info=True)
        return GoogleConvertResponse(
            code="500",
            message=f"创建任务失败: {str(e)}"
        )


def execute_convert_task(task_id: str, request: GoogleConvertRequest):
    """后台执行转换任务"""
    
    res_id = None
    status = 2  # 默认失败
    error_message = ""
    
    result = {}
    try:
        with db_manager.session_scope() as db:
            # 更新任务状态为运行中
            crud.update_task_status(db, task_id, TaskStatus.RUNNING, "开始执行迁移...")
            
            # 执行所有迁移步骤Step1-step9
            result = run_migration_with_callback(
                db=db,
                task_id=task_id,
                request=request
            )
            
            if result["success"]:
                status = 1
                res_id = task_id
                crud.update_task_status(
                    db, task_id, TaskStatus.COMPLETED, "迁移完成",
                    final_agent_file=result.get("final_agent_file"),
                    upload_success=True
                )
            else:
                status = 2
                error_message = result.get("error", "未知错误")
                crud.update_task_status(
                    db, task_id, TaskStatus.FAILED, error_message
                )
                
    except Exception as e:
        status = 2
        error_message = str(e)
        logger.error(f"任务执行异常 task_id={task_id}: {e}", exc_info=True)
        
        # 尝试更新数据库状态（可能失败，但不影响回调）
        try:
            with db_manager.session_scope() as db:
                crud.update_task_status(db, task_id, TaskStatus.FAILED, f"执行失败: {str(e)}")
                crud.create_error(
                    db, task_id,
                    error_message=str(e),
                    error_type=type(e).__name__,
                    error_detail=traceback.format_exc()
                )
        except Exception as db_error:
            logger.error(f"更新数据库状态失败: {db_error}")
    
    finally:
        # 确保回调总是执行（无论成功还是失败）
        try:
            logger.info(f"📤 准备回调通知: task_id={task_id}, status={status}, res_id={res_id or task_id}")
            callback_result(
                request=request,
                status=status,
                res_id=res_id or task_id,
                error_message=error_message
            )
            logger.info(f"✅ 回调通知成功: task_id={task_id}, status={status}")
        except Exception as e:
            logger.error(f"❌ 回调失败 task_id={task_id}: {e}", exc_info=True)
        
        # writed by senlin.den 2026-01-09
        # 清理临时文件，调试模型不清理
        if request.is_debug != True:
            try:
                agent_file = result.get("final_agent_file")
                if agent_file and os.path.exists(agent_file):
                    # 获取上上级目录：output/task_id
                    # agent_file 路径: output/task_id/step8_final/agent_file.json
                    # 上上级目录: output/task_id
                    parent_dir = os.path.dirname(os.path.dirname(agent_file))
                    if os.path.exists(parent_dir):
                        try:
                            shutil.rmtree(parent_dir)
                            logger.info(f"✅ 已删除任务输出目录: {parent_dir}")
                        except Exception as e:
                            logger.error(f"❌ 删除目录失败 {parent_dir}: {e}", exc_info=True)
                    else:
                        logger.warning(f"⚠️ 目录不存在，跳过删除: {parent_dir}")
                else:
                    logger.warning(f"⚠️ 最终文件不存在，跳过删除: {agent_file}")
            except Exception as e:
                logger.error(f"❌ 清理文件失败: {e}", exc_info=True)


def callback_result(request: GoogleConvertRequest, status: int, res_id: str, error_message: str = ""):
    """回调通知结果（可选，CALLBACK_URL 为空时跳过）"""
    
    if not CALLBACK_URL:
        logger.debug("⏭️  未配置 CALLBACK_URL，跳过回调通知")
        return
    
    # 构建回调请求头
    headers = {
        "Content-Type": "application/json"
    }
    # 如果请求携带了认证信息，一并传递给回调
    if request.cybertron_robot_key:
        headers["cybertron-robot-key"] = request.cybertron_robot_key
    if request.cybertron_robot_token:
        headers["cybertron-robot-token"] = request.cybertron_robot_token
    if request.username:
        headers["username"] = request.username
    
    # 根据语言构建 res 结构
    lang_key = request.language
    if lang_key in ["zh-hant", "cantonese"]:
        lang_key = "zh-hant"  # 统一使用 zh-hant
    
    body = {
        "record_id": request.record_id,
        "status": status,
        "res": {
            lang_key: {
                "res_id": res_id
            }
        }
    }
    
    if error_message and status == 2:
        body["error_message"] = error_message
    
    logger.info(f"📤 回调通知接口: {CALLBACK_URL}")
    logger.debug(f"   Body: {json.dumps(body, ensure_ascii=False)}")
    
    try:
        response = requests.post(
            CALLBACK_URL,
            headers=headers,
            json=body,
            timeout=30
        )
        logger.debug(f"   响应: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"   回调请求失败: {e}")


def validate_step1_output(step1_dir: str, lang: str) -> tuple:
    """
    验证 Step 1 的输出文件
    
    Args:
        step1_dir: Step 1 输出目录
        lang: 语言代码
        
    Returns:
        (is_valid, error_message)
    """
    required_file = os.path.join(step1_dir, f'intents_{lang}.json')
    
    if not os.path.exists(required_file):
        return False, f"Step 1 处理失败：未生成必需的文件 intents_{lang}.json"
    
    try:
        with open(required_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if 'intents' not in data:
            return False, f"Step 1 输出格式错误：intents_{lang}.json 缺少 'intents' 字段"
        
        intents = data.get('intents', [])
        if not isinstance(intents, list):
            return False, f"Step 1 输出格式错误：'intents' 必须是数组"
        
        if len(intents) == 0:
            return False, f"Step 1 输出错误：intents_{lang}.json 中没有任何意图数据"
        
        logger.debug(f"✅ Step 1 输出验证通过: {len(intents)} 个 intents")
        return True, ""
        
    except json.JSONDecodeError as e:
        return False, f"Step 1 输出文件 JSON 格式错误: {str(e)}"
    except Exception as e:
        return False, f"Step 1 输出验证失败: {str(e)}"


def _process_single_file_steps(
    db: Session,
    task_id: str,
    request: GoogleConvertRequest,
    file_path: str,
    output_dir: str,
    lang: str,
    file_index: int = 0,
    global_config: dict = {},
    is_multi_file: bool = False
) -> Dict[str, Any]:
    """
    处理单个文件的 Step 0-8 流程
    
    Args:
        db: 数据库会话
        task_id: 任务ID
        request: 请求对象
        file_path: 当前处理的文件路径
        output_dir: 输出目录
        lang: 语言
        file_index: 文件索引（多文件时使用）
        is_multi_file: 是否为多文件模式
    
    Returns:
        包含处理结果的字典，包括 final_agent_file 路径
    """
    result = {
        "success": False,
        "error": None,
        "final_agent_file": None
    }
    
    # 多文件模式下的日志前缀
    log_prefix = f"[文件 {file_index + 1}] " if is_multi_file else ""
    
    try:
        # ========================================
        # Step 0: 提取数据（增强验证）
        # ========================================
        if not is_multi_file:
            crud.update_step_status(db, task_id, 0, StepStatus.RUNNING, "正在验证文件格式...")
        else:
            logger.info(f"{log_prefix}正在验证文件格式: {os.path.basename(file_path)}")
        
        from step0_extract_from_exported_flow import extract_from_exported_flow, validate_dialogflow_cx_file
        
        # 先验证文件格式
        is_valid, error_msg = validate_dialogflow_cx_file(file_path)
        if not is_valid:
            logger.error(f"{log_prefix}❌ 文件格式验证失败: {error_msg}")
            if not is_multi_file:
                crud.update_step_status(db, task_id, 0, StepStatus.FAILED, error_msg)
                crud.create_error(db, task_id, error_msg, step_number=0, error_type="ValidationError")
            raise Exception(error_msg)
        
        if not is_multi_file:
            crud.update_step_status(db, task_id, 0, StepStatus.RUNNING, "正在提取数据...")
        else:
            logger.info(f"{log_prefix}正在提取数据...")
        
        step0_dir = os.path.join(output_dir, 'step0_extracted')
        os.makedirs(step0_dir, exist_ok=True)
        
        step0_entities = os.path.join(step0_dir, 'entities.json')
        step0_intents = os.path.join(step0_dir, 'intents.json')
        step0_fulfillments = os.path.join(step0_dir, 'fulfillments.json')
        
        success = extract_from_exported_flow(
            exported_flow_file=file_path,
            output_entities=step0_entities,
            output_intents=step0_intents,
            output_fulfillments=step0_fulfillments
        )
        
        if not success:
            error_msg = f"{log_prefix}Step 0 提取数据失败：文件结构不符合预期"
            logger.error(f"❌ {error_msg}")
            if not is_multi_file:
                crud.update_step_status(db, task_id, 0, StepStatus.FAILED, error_msg)
                crud.create_error(db, task_id, error_msg, step_number=0, error_type="ExtractionError")
            raise Exception(error_msg)
        
        if not is_multi_file:
            crud.update_step_status(db, task_id, 0, StepStatus.COMPLETED, "数据提取完成",
                                   output_files=[step0_entities, step0_intents, step0_fulfillments])
        else:
            logger.info(f"{log_prefix}✅ 数据提取完成")
        
        # ========================================
        # Step 1: 处理数据（增强验证）
        # ========================================
        if not is_multi_file:
            crud.update_step_status(db, task_id, 1, StepStatus.RUNNING, f"正在处理数据（语言: {lang}）...")
        else:
            logger.info(f"{log_prefix}正在处理数据（语言: {lang}）...")
        
        from step1_process_dialogflow_data import (
            process_entities_by_language,
            process_intents_by_language,
            process_fulfillments_by_language,
            extract_intent_parameters,
            extract_flow_configs,
            extract_webhooks
        )
        import shutil
        
        step1_dir = os.path.join(output_dir, 'step1_processed')
        os.makedirs(step1_dir, exist_ok=True)
        
        try:
            # 处理实体
            if os.path.exists(step0_entities):
                logger.debug(f"{log_prefix}处理实体数据...")
                process_entities_by_language(step0_entities)
            
            # 处理意图（必需）
            if os.path.exists(step0_intents):
                logger.debug(f"{log_prefix}处理意图数据...")
                process_intents_by_language(step0_intents)
            else:
                raise Exception(f"未找到意图文件: {step0_intents}")
            
            # 处理响应
            if os.path.exists(step0_fulfillments):
                logger.debug(f"{log_prefix}处理响应数据...")
                process_fulfillments_by_language(step0_fulfillments)
            
            # 提取意图参数
            if os.path.exists(step0_intents):
                logger.debug(f"{log_prefix}提取意图参数...")
                extract_intent_parameters(step0_intents, os.path.join(step1_dir, 'intent_parameters.json'))
            
            # 提取Flow配置
            logger.debug(f"{log_prefix}提取Flow配置...")
            extract_flow_configs()
            if os.path.exists('flow_configs.json'):
                shutil.move('flow_configs.json', os.path.join(step1_dir, 'flow_configs.json'))
            
            # 提取Webhooks
            logger.debug(f"{log_prefix}提取Webhooks配置...")
            extract_webhooks()
            if os.path.exists('webhooks.json'):
                shutil.move('webhooks.json', os.path.join(step1_dir, 'webhooks.json'))
            
            # 移动生成的文件到目标目录
            step1_files = []
            for prefix in ['entities_', 'intents_', 'fulfillments_']:
                src_file = f'{prefix}{lang}.json'
                if os.path.exists(src_file):
                    dst_file = os.path.join(step1_dir, src_file)
                    shutil.move(src_file, dst_file)
                    step1_files.append(dst_file)
            
            # 清理其他语言文件
            for other_lang in ['en', 'zh', 'zh-hant']:
                if other_lang != lang:
                    for prefix in ['entities_', 'intents_', 'fulfillments_']:
                        temp_file = f'{prefix}{other_lang}.json'
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
            
            # 验证输出文件
            is_valid, error_msg = validate_step1_output(step1_dir, lang)
            if not is_valid:
                logger.error(f"{log_prefix}❌ {error_msg}")
                if not is_multi_file:
                    crud.update_step_status(db, task_id, 1, StepStatus.FAILED, error_msg)
                    crud.create_error(db, task_id, error_msg, step_number=1, error_type="ValidationError")
                raise Exception(error_msg)
            
            logger.info(f"{log_prefix}✅ Step 1 完成，生成 {len(step1_files)} 个文件")
            if not is_multi_file:
                crud.update_step_status(db, task_id, 1, StepStatus.COMPLETED, "数据处理完成", output_files=step1_files)
            
        except Exception as e:
            error_msg = f"{log_prefix}Step 1 数据处理失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            if not is_multi_file:
                crud.update_step_status(db, task_id, 1, StepStatus.FAILED, error_msg)
                crud.create_error(db, task_id, str(e), step_number=1, error_type=type(e).__name__)
            raise Exception(error_msg)
        
        # ========================================
        # Step 2: 转换 Workflow
        # ========================================
        if not is_multi_file:
            crud.update_step_status(db, task_id, 2, StepStatus.RUNNING, "正在转换 workflow...")
        else:
            logger.info(f"{log_prefix}正在转换 workflow...")
        
        step2_dir = os.path.join(output_dir, 'step2_workflow_config', lang)
        os.makedirs(step2_dir, exist_ok=True)
        
        intents_lang_file = os.path.join(step1_dir, f'intents_{lang}.json')
        fulfillments_lang_file = os.path.join(step1_dir, f'fulfillments_{lang}.json')
        
        intents_mapping = {}
        intents_training_phrases = {}
        if os.path.exists(intents_lang_file):
            intents_mapping, intents_training_phrases = load_intents_with_training_phrases(intents_lang_file)
            logger.info(f"{log_prefix}   - ✅ 已加载 intents_mapping: {len(intents_mapping)} 个意图")
            logger.info(f"{log_prefix}   - ✅ 已加载 intents_training_phrases: {len(intents_training_phrases)} 个意图的训练短语")
            for i, (name, phrases) in enumerate(list(intents_training_phrases.items())[:3]):
                logger.debug(f"{log_prefix}      - {name}: {len(phrases)} 个训练短语")
        else:
            logger.warning(f"{log_prefix}   - ⚠️ intents_lang_file 不存在: {intents_lang_file}")
            logger.warning(f"{log_prefix}   - ⚠️ intents_training_phrases 为空，版本2的语义判断节点将没有训练短语！")
        
        logger.info(f"{log_prefix}🔄 Step 2: 开始转换 workflow...")
        logger.info(f"{log_prefix}   - Intents 文件: {intents_lang_file}")
        logger.info(f"{log_prefix}   - Fulfillments 文件: {fulfillments_lang_file}")
        logger.info(f"{log_prefix}   - 语言: {lang}")
        
        intent_parameters_file = os.path.join(step1_dir, 'intent_parameters.json')
        logger.info(f"{log_prefix}   - Intent parameters 文件: {intent_parameters_file}")
        logger.info(f"{log_prefix}   - 文件存在: {os.path.exists(intent_parameters_file)}")
        
        # 检查必要文件是否存在
        if not os.path.exists(fulfillments_lang_file):
            error_msg = f"Fulfillments 文件不存在: {fulfillments_lang_file}"
            logger.error(f"{log_prefix}   - ❌ {error_msg}")
            raise FileNotFoundError(error_msg)
        
        logger.info(f"{log_prefix}   - 创建 WorkflowConverter...")
        logger.debug(f"{log_prefix}   - intents_mapping 数量: {len(intents_mapping)}")
        logger.debug(f"{log_prefix}   - intent_parameters_file: {intent_parameters_file}")
        logger.debug(f"{log_prefix}   - language: {lang}")
        
        # 意图识别版本
        intent_recognition_version = request.faq_version
        if intent_recognition_version not in ['Knowledge Base Version', 'Semantic Judgement Version']:
            logger.error(f"{log_prefix}faq_version 参数: {intent_recognition_version} 不支持")
            raise ValueError(f"faq_version 参数: {intent_recognition_version} 不支持")
        else:
            if intent_recognition_version == 'Knowledge Base Version':
                intent_recognition_version = 1
            else:
                intent_recognition_version = 2
        
        try:
            converter = WorkflowConverter(
                intents_mapping=intents_mapping,
                intent_parameters_file=intent_parameters_file,
                intent_recognition_version=intent_recognition_version,
                intents_training_phrases=intents_training_phrases,
                language=lang,
                global_config=global_config
            )
            logger.info(f"{log_prefix}   - ✅ WorkflowConverter 已创建，开始转换...")
        except FileNotFoundError as e:
            error_msg = f"文件未找到: {str(e)}"
            logger.error(f"{log_prefix}   - ❌ {error_msg}")
            error_detail = traceback.format_exc()
            logger.error(f"{log_prefix}   - 错误详情:\n{error_detail}")
            raise
        except json.JSONDecodeError as e:
            error_msg = f"JSON 解析错误: {str(e)}"
            logger.error(f"{log_prefix}   - ❌ {error_msg}")
            logger.error(f"{log_prefix}   - 文件: {intent_parameters_file}")
            error_detail = traceback.format_exc()
            logger.error(f"{log_prefix}   - 错误详情:\n{error_detail}")
            raise
        except Exception as e:
            error_msg = f"创建 WorkflowConverter 失败: {str(e)}"
            error_type = type(e).__name__
            logger.error(f"{log_prefix}   - ❌ {error_msg}")
            logger.error(f"{log_prefix}   - 错误类型: {error_type}")
            error_detail = traceback.format_exc()
            logger.error(f"{log_prefix}   - 错误详情:\n{error_detail}")
            raise
        
        # 获取 step1 处理后的 entities 文件路径
        entities_lang_file = os.path.join(output_dir, 'step1_processed', f'entities_{lang}.json')
        
        generated_workflows = converter.convert_to_multiple_workflows(
            fulfillments_file=fulfillments_lang_file,
            flow_file=file_path,
            lang=lang,
            output_dir=step2_dir,
            entities_file=entities_lang_file if os.path.exists(entities_lang_file) else None
        )
        
        logger.info(f"{log_prefix}✅ Step 2 完成: 生成了 {len(generated_workflows)} 个 workflow")
        if not is_multi_file:
            crud.update_step_status(db, task_id, 2, StepStatus.COMPLETED,
                                   f"生成了 {len(generated_workflows)} 个 workflow")
            crud.update_task_status(db, task_id, TaskStatus.RUNNING, "", total_workflows=len(generated_workflows))
        
        if intent_recognition_version == 1:
            # ========================================
            # Step 3: 创建知识库（带复用检查）
            # ========================================
            if not is_multi_file:
                crud.update_step_status(db, task_id, 3, StepStatus.RUNNING, "正在检查知识库复用...")
            
            # Step 3.0: 检查并复用已有的知识库映射
            logger.info("="*80)
            logger.info(f"{log_prefix}Step 3.0: 检查数据库中的知识库映射")
            logger.info("="*80)
            
            from step3_kb_reuse_helper import check_and_reuse_kb_mappings
            
            intents_file = os.path.join(output_dir, 'step1_processed', f'intents_{lang}.json')
            reused_count, new_count = 0, 0
            
            try:
                reused_count, new_count = check_and_reuse_kb_mappings(
                    db=db,
                    task_id=task_id,
                    robot_key=request.cybertron_robot_key,
                    intents_file=intents_file,
                    language=lang,
                    output_dir=output_dir
                )
                
                total_intents = reused_count + new_count
                
                if reused_count > 0:
                    logger.debug("")
                    logger.debug("="*80)
                    logger.debug(f"{log_prefix}🎯 发现数据库中已有知识库映射！")
                    logger.debug("="*80)
                    logger.debug(f"{log_prefix}✅ 复用知识库: {reused_count} 个")
                    logger.debug(f"{log_prefix}🆕 需要新建: {new_count} 个")
                    logger.debug(f"{log_prefix}📊 总计: {total_intents} 个意图")
                    logger.debug("")
                    logger.debug(f"{log_prefix}💡 复用说明:")
                    logger.debug(f"{log_prefix}   - 这些知识库在之前的任务中已创建（相同 robot_key + intent + language）")
                    logger.debug(f"{log_prefix}   - 本次任务将直接使用已有的知识库ID，无需重新创建")
                    logger.debug(f"{log_prefix}   - 映射关系已保存到数据库和本地JSON文件")
                    logger.debug("="*80)
                elif total_intents > 0:
                    logger.debug(f"{log_prefix}   ℹ️  未找到可复用的知识库映射，将创建 {new_count} 个新知识库")
                else:
                    logger.warning(f"{log_prefix}   ⚠️  未找到意图文件或意图文件为空，无法进行知识库复用检查")
                    logger.warning(f"{log_prefix}   意图文件路径: {intents_file}")
            except Exception as e:
                logger.warning(f"{log_prefix}   ⚠️  知识库复用检查失败: {e}")
                logger.warning(f"{log_prefix}   将跳过复用检查，继续执行 step3")
                reused_count, new_count = 0, 0
            
            # Step 3.1: 创建知识库（根据 new_count 决定是否跳过）
            if new_count == 0 and reused_count > 0:
                logger.debug("")
                logger.debug(f"{log_prefix}⏭️  所有知识库均已复用，跳过创建步骤，无需上传")
                logger.debug("")
                if not is_multi_file:
                    crud.update_step_status(db, task_id, 3, StepStatus.RUNNING, "正在更新 workflow 配置...")
            else:
                if not is_multi_file:
                    crud.update_step_status(db, task_id, 3, StepStatus.RUNNING, f"正在创建 {new_count} 个新知识库...")
            
            # 从文件名提取 agent 名称（用于知识库命名）
            exported_flow_basename = os.path.basename(file_path)
            agent_name = ""
            
            if exported_flow_basename.startswith("exported_flow_"):
                agent_name = exported_flow_basename[len("exported_flow_"):]
            else:
                agent_name = exported_flow_basename
            
            if agent_name.endswith(".json"):
                agent_name = agent_name[:-5]
            
            if " (" in agent_name:
                agent_name = agent_name.split(" (")[0]
            
            logger.info(f"{log_prefix}📋 使用 agent 名称作为知识库后缀: {agent_name}")
            
            # 设置环境变量
            os.environ['ROBOT_KEY'] = request.cybertron_robot_key
            os.environ['ROBOT_TOKEN'] = request.cybertron_robot_token
            os.environ['USERNAME'] = request.username
            os.environ['STEP_3_LANGUAGE'] = lang
            os.environ['STEP_3_STEP_1_CREATE_KB'] = 'True' if new_count > 0 else 'False'
            os.environ['STEP_3_STEP_2_UPDATE_WORKFLOW'] = 'True'
            os.environ['STEP_3_UPLOAD_WORKERS'] = os.getenv('STEP_3_UPLOAD_WORKERS', '10') or '10'
            os.environ['STEP_3_INTENTS_DIR'] = os.path.join(output_dir, 'step1_processed')
            os.environ['STEP_3_QA_OUTPUT_DIR'] = os.path.join(output_dir, 'qa_knowledge_bases')
            os.environ['STEP_3_QA_TEMP_DIR'] = os.path.join(output_dir, 'qa_knowledge_bases', 'temp')
            os.environ['STEP_3_WORKFLOW_CONFIG_DIR'] = os.path.join(output_dir, 'step2_workflow_config', lang)
            os.environ['STEP_3_LANGUAGE'] = lang
            os.environ['STEP_3_SOURCE_FILE_TAG'] = agent_name
            
            try:
                import step3_kb_workflow
                from importlib import reload
                reload(step3_kb_workflow)
                skip_workflow_update = os.getenv("SKIP_STEP3_WORKFLOW_UPDATE", "True").lower() == "true"
                if skip_workflow_update:
                    logger.warning(f"{log_prefix}⚠️  检测到 SKIP_STEP3_WORKFLOW_UPDATE=True，跳过 step3 workflow 更新")
                    logger.info(f"{log_prefix}🔄 调用 step3_kb_workflow.step1_create_knowledge_bases...")
                    success = step3_kb_workflow.step1_create_knowledge_bases(task_id=task_id, db_session=db)
                    logger.info(f"{log_prefix}📊 Step 3 返回值: success={success}")
                else:
                    success = step3_kb_workflow.main(task_id=task_id, db_session=db)
                
                if success:
                    total_kb = reused_count + new_count
                    if not is_multi_file:
                        crud.update_task_status(db, task_id, TaskStatus.RUNNING, "", 
                                            total_kb_created=total_kb)
                    
                    completion_msg = f"知识库创建完成 (复用: {reused_count}, 新建: {new_count})"
                    if not is_multi_file:
                        crud.update_step_status(db, task_id, 3, StepStatus.COMPLETED, completion_msg)
                else:
                    error_msg = f"知识库创建失败"
                    if not is_multi_file:
                        crud.update_step_status(db, task_id, 3, StepStatus.FAILED, error_msg)
                        crud.create_error(db, task_id, error_msg, step_number=3)
                    logger.error(f"{log_prefix}❌ Step 3 失败，终止流程")
                    raise Exception(error_msg)
            except Exception as e:
                error_msg = f"知识库创建失败: {str(e)}"
                if not is_multi_file:
                    crud.update_step_status(db, task_id, 3, StepStatus.FAILED, error_msg)
                    crud.create_error(db, task_id, str(e), step_number=3, error_type=type(e).__name__)
                logger.error(f"{log_prefix}❌ Step 3 异常: {e}")
                if os.getenv("CONTINUE_ON_STEP3_ERROR", "False").lower() == "true":
                    logger.warning(f"{log_prefix}⚠️  检测到 CONTINUE_ON_STEP3_ERROR=True，即使step3失败也继续执行")
                    if not is_multi_file:
                        crud.update_step_status(db, task_id, 3, StepStatus.COMPLETED, "知识库创建完成（跳过workflow更新）")
                else:
                    raise
        else:
            logger.info(f"{log_prefix}🔄 Step 3: 语义判断节点版本，无需创建知识库，直接跳过")
        
        # ========================================
        # Step 4: 提取变量
        # ========================================
        if 'generated_workflows' not in locals() and 'generated_workflows' not in globals():
            logger.error(f"{log_prefix}❌ generated_workflows 变量未定义，无法继续 Step 4")
            raise ValueError("generated_workflows 变量未定义")
        
        logger.info(f"{log_prefix}🔄 开始 Step 4: 变量提取 ({len(generated_workflows)} 个 workflow)")
        logger.info(f"{log_prefix}📁 Step 2目录: {step2_dir}")
        logger.info(f"{log_prefix}📁 Step 4目录: {os.path.join(output_dir, 'step4_variables', lang)}")
        
        logger.info(f"{log_prefix}💾 正在更新数据库状态...")
        try:
            if not is_multi_file:
                crud.update_step_status(db, task_id, 4, StepStatus.RUNNING, "正在提取变量...")
            logger.info(f"{log_prefix}✅ 数据库状态更新完成")
        except Exception as e:
            logger.warning(f"{log_prefix}⚠️  更新数据库状态失败，继续执行: {e}")
        
        logger.info(f"{log_prefix}📦 正在导入 step4_extract_variables 模块...")
        try:
            import importlib
            import step4_extract_variables
            importlib.reload(step4_extract_variables)
            extract_variables_from_nodes = step4_extract_variables.extract_variables_from_nodes
            logger.info(f"{log_prefix}✅ 模块导入成功")
        except Exception as e:
            logger.error(f"{log_prefix}❌ 导入模块失败: {e}")
            raise
        
        step4_dir = os.path.join(output_dir, 'step4_variables', lang)
        logger.info(f"{log_prefix}📁 正在创建 Step 4 输出目录: {step4_dir}")
        os.makedirs(step4_dir, exist_ok=True)
        logger.info(f"{log_prefix}✅ Step 4 输出目录已创建")
        
        logger.info(f"{log_prefix}📋 Generated workflows 列表: {generated_workflows}")
        logger.info(f"{log_prefix}📊 共 {len(generated_workflows)} 个 workflow 需要处理")
        logger.info(f"{log_prefix}🔄 开始处理 {len(generated_workflows)} 个 workflow...")
        processed_count = 0
        for wf_name in generated_workflows:
            logger.info(f"{log_prefix}🔄 处理 workflow: {wf_name}")
            nodes_file = os.path.join(step2_dir, f'nodes_config_{wf_name}.json')
            variables_file = os.path.join(step4_dir, f'variables_{wf_name}.json')
            logger.info(f"{log_prefix}🔍 检查文件: {nodes_file}")
            
            if os.path.exists(nodes_file):
                logger.info(f"{log_prefix}✅ 文件存在，开始处理: {wf_name}")
                try:
                    with open(nodes_file, 'r', encoding='utf-8') as f:
                        nodes_config = json.load(f)
                    nodes_list = nodes_config.get("nodes", [])
                    if not nodes_list:
                        logger.warning(f"{log_prefix}⚠️  {wf_name} 的nodes_config中没有nodes字段或为空")
                        continue
                    logger.info(f"{log_prefix}📊 找到 {len(nodes_list)} 个节点")
                    variables_data = extract_variables_from_nodes(nodes_list, lang)
                    with open(variables_file, 'w', encoding='utf-8') as f:
                        json.dump(variables_data, f, indent=2, ensure_ascii=False)
                    processed_count += 1
                    logger.info(f"{log_prefix}✅ 处理完成: {wf_name} (提取了 {len(variables_data.get('variables', {}))} 个变量)")
                except Exception as e:
                    logger.error(f"{log_prefix}❌ 处理 {wf_name} 时出错: {e}")
                    raise
            else:
                logger.warning(f"{log_prefix}⚠️  节点文件不存在: {nodes_file}")
                logger.warning(f"{log_prefix}   工作流名称: {wf_name}")
                logger.warning(f"{log_prefix}   目录: {step2_dir}")
                if os.path.exists(step2_dir):
                    files = os.listdir(step2_dir)
                    logger.warning(f"{log_prefix}   目录中的文件: {files}")
                else:
                    logger.warning(f"{log_prefix}   目录不存在: {step2_dir}")
                alt_step2_dir = os.path.join(output_dir, 'step2_workflow_config')
                if os.path.exists(alt_step2_dir):
                    logger.info(f"{log_prefix}🔍 尝试查找替代目录: {alt_step2_dir}")
                    for subdir in os.listdir(alt_step2_dir):
                        subdir_path = os.path.join(alt_step2_dir, subdir)
                        if os.path.isdir(subdir_path):
                            alt_nodes_file = os.path.join(subdir_path, f'nodes_config_{wf_name}.json')
                            if os.path.exists(alt_nodes_file):
                                logger.warning(f"{log_prefix}⚠️  找到替代文件: {alt_nodes_file}")
                                break
        
        logger.info(f"{log_prefix}✅ Step 4 完成: 处理了 {processed_count}/{len(generated_workflows)} 个 workflow")
        if not is_multi_file:
            crud.update_step_status(db, task_id, 4, StepStatus.COMPLETED, f"变量提取完成 ({processed_count} 个文件)")
        
        # ========================================
        # Step 5: 提取配置
        # ========================================
        logger.info(f"{log_prefix}🔄 开始 Step 5: 提取配置 ({len(generated_workflows)} 个 workflow)")
        if not is_multi_file:
            crud.update_step_status(db, task_id, 5, StepStatus.RUNNING, "正在提取配置...")
        
        from step5_extract_workflow_config import extract_workflow_config_from_flow
        
        step5_dir = os.path.join(output_dir, 'step5_workflow_meta', lang)
        os.makedirs(step5_dir, exist_ok=True)
        
        with open(file_path, 'r', encoding='utf-8') as f:
            flow_data = json.load(f)
        
        for wf_name in generated_workflows:
            workflow_config = extract_workflow_config_from_flow(flow_data, file_path)
            workflow_config['workflow_name'] = wf_name
            workflow_config['workflow_info'] = {
                'agent_name': request.name or "Migrated Agent",
                'agent_description': request.description or ""
            }
            
            config_file = os.path.join(step5_dir, f'workflow_config_{wf_name}.json')
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(workflow_config, f, indent=2, ensure_ascii=False)
        
        if not is_multi_file:
            crud.update_step_status(db, task_id, 5, StepStatus.COMPLETED, "配置提取完成")
        
        # ========================================
        # Step 6: 生成 Workflow
        # ========================================
        if not is_multi_file:
            crud.update_step_status(db, task_id, 6, StepStatus.RUNNING, "正在生成 workflow...")
        
        from step6_workflow_generator import main as generate_single_workflow
        
        step6_dir = os.path.join(output_dir, 'step6_final', lang)
        os.makedirs(step6_dir, exist_ok=True)
        
        for wf_name in generated_workflows:
            workflow_config_file = os.path.join(step5_dir, f'workflow_config_{wf_name}.json')
            nodes_config_file = os.path.join(step2_dir, f'nodes_config_{wf_name}.json')
            variables_config_file = os.path.join(step4_dir, f'variables_{wf_name}.json')
            edge_config_file = os.path.join(step2_dir, f'edge_config_{wf_name}.json')
            output_file = os.path.join(step6_dir, f'generated_workflow_{wf_name}.json')
            
            if all(os.path.exists(f) for f in [nodes_config_file, variables_config_file, edge_config_file]):
                if not os.path.exists(workflow_config_file):
                    default_config = {
                        "workflow_name": wf_name,
                        "workflow_info": {"description": f"{wf_name} 工作流"}
                    }
                    with open(workflow_config_file, 'w', encoding='utf-8') as f:
                        json.dump(default_config, f, indent=2, ensure_ascii=False)
                
                try:
                    generate_single_workflow(
                        workflow_config=workflow_config_file,
                        nodes_config=nodes_config_file,
                        variables_config=variables_config_file,
                        edge_config=edge_config_file,
                        output_file=output_file,
                        language=lang,
                        global_configs=global_config
                    )
                except Exception as e:
                    if not is_multi_file:
                        crud.create_error(db, task_id, f"生成 {wf_name} 失败: {str(e)}", step_number=6)
        
        if not is_multi_file:
            crud.update_step_status(db, task_id, 6, StepStatus.COMPLETED, "Workflow 生成完成")
        
        # ========================================
        # Step 7: 清理优化
        # ========================================
        if not is_multi_file:
            crud.update_step_status(db, task_id, 7, StepStatus.RUNNING, "正在清理优化...")
        
        from step7_clean_isolated_nodes import main as clean_isolated_nodes
        
        step7_dir = os.path.join(output_dir, 'step7_final', lang)
        os.makedirs(step7_dir, exist_ok=True)
        
        remove_condition_edges = False
        if intent_recognition_version == 2:
            remove_condition_edges = True
        
        step6_files_list = [f for f in os.listdir(step6_dir) if f.endswith('.json') and f.startswith('generated_workflow_')]
        if step6_files_list:
            clean_isolated_nodes(dry_run=False, input_dir=step6_dir, output_dir=step7_dir, optimize=True, layout_direction='LR', remove_condition_edges=remove_condition_edges)
        
        if not is_multi_file:
            crud.update_step_status(db, task_id, 7, StepStatus.COMPLETED, "清理优化完成")
        
        # ========================================
        # Step 8: 合并 JSON（包含 Agent 配置）
        # ========================================
        if not is_multi_file:
            crud.update_step_status(db, task_id, 8, StepStatus.RUNNING, "正在合并 JSON...")
        
        import step8_merge_to_planning
        from importlib import reload
        reload(step8_merge_to_planning)
        merge_to_planning = step8_merge_to_planning.main
        
        step8_dir = os.path.join(output_dir, 'step8_final')
        os.makedirs(step8_dir, exist_ok=True)
        
        project_name = os.path.basename(file_path).replace('exported_flow_', '').replace('.json', '')
        project_name_safe = project_name.replace(' ', '_').replace('(', '_').replace(')', '_').replace('/', '_').replace('\\', '_')
        project_name_safe = re.sub(r'_+', '_', project_name_safe).strip('_')
        if len(project_name_safe) > 50:
            project_name_safe = project_name_safe[:50]
        agent_name_safe = (request.name or "agent").replace(' ', '_').replace('/', '_').replace('\\', '_')
        if agent_name_safe.startswith('exported_flow_'):
            agent_name_safe = agent_name_safe.replace('exported_flow_', '')
        if len(agent_name_safe) > 30:
            agent_name_safe = agent_name_safe[:30]
        final_agent_file = os.path.join(step8_dir, f"{agent_name_safe}_{project_name_safe}_{lang}_merged.json")
        logger.debug(f"{log_prefix}📁 生成的文件名: {os.path.basename(final_agent_file)}")
        logger.debug(f"{log_prefix}   完整路径长度: {len(final_agent_file)} 字符")
        
        # 根据语言选择对应的 agent 模板文件
        agent_template_path = None
        lang_lower = lang.lower()
        if lang_lower in ['en', 'english']:
            en_template = os.path.join('input', 'agent_EN.json')
            if os.path.exists(en_template):
                agent_template_path = en_template
                logger.debug(f"{log_prefix}   使用英文 Agent 模板: {en_template}")
            else:
                logger.warning(f"{log_prefix}   英文模板不存在: {en_template}，使用默认模板")
        elif lang_lower in ['zh', 'chinese']:
            zh_template = os.path.join('input', 'agent-zh.json')
            if os.path.exists(zh_template):
                agent_template_path = zh_template
                logger.debug(f"{log_prefix}   使用简体中文 Agent 模板: {zh_template}")
            else:
                logger.warning(f"{log_prefix}   简体中文模板不存在: {zh_template}，使用默认模板")
                default_template = os.path.join('input', 'agent.json')
                if os.path.exists(default_template):
                    agent_template_path = default_template
        elif lang_lower in ['zh-hant', 'cantonese']:
            zh_hant_template = os.path.join('input', 'agent-zh-hant.json')
            if os.path.exists(zh_hant_template):
                agent_template_path = zh_hant_template
                logger.debug(f"{log_prefix}   使用繁体中文/粤语 Agent 模板: {zh_hant_template}")
            else:
                logger.warning(f"{log_prefix}   繁体中文模板不存在: {zh_hant_template}，使用默认模板")
                default_template = os.path.join('input', 'agent.json')
                if os.path.exists(default_template):
                    agent_template_path = default_template
        else:
            default_template = os.path.join('input', 'agent.json')
            if os.path.exists(default_template):
                agent_template_path = default_template
                logger.debug(f"{log_prefix}   使用默认 Agent 模板: {default_template}")
        
        if agent_template_path is None:
            logger.error(f"{log_prefix}❌ 未找到合适的 Agent 模板文件 (语言: {lang})")
            logger.error(f"{log_prefix}   请检查 input/ 目录下是否存在以下文件:")
            logger.error(f"{log_prefix}   - agent-zh-hant.json (繁体中文)")
            logger.error(f"{log_prefix}   - agent-zh.json (简体中文)")
            logger.error(f"{log_prefix}   - agent_EN.json (英文)")
            logger.error(f"{log_prefix}   - agent.json (默认)")
            raise FileNotFoundError(f"Agent template not found for language: {lang}")
        
        step7_files_list = [f for f in os.listdir(step7_dir) if f.endswith('.json') and f.startswith('generated_workflow_')]
        if step7_files_list:
            merge_to_planning(
                template_json_path=agent_template_path,
                step7_dir=step7_dir,
                output_path=final_agent_file,
                exported_flow_file=file_path,
                task_output_dir=output_dir,
                global_config=global_config
            )
        
        # 更新 Agent 配置
        if os.path.exists(final_agent_file):
            with open(final_agent_file, 'r', encoding='utf-8') as f:
                agent_data = json.load(f)
            
            if 'planning' not in agent_data:
                agent_data['planning'] = {}
            if 'agent_info' not in agent_data['planning']:
                agent_data['planning']['agent_info'] = {}
            if 'basic_config' not in agent_data['planning']:
                agent_data['planning']['basic_config'] = {}
            
            agent_info = agent_data['planning']['agent_info']
            basic_config = agent_data['planning']['basic_config']
            
            if request.name:
                basic_config['robot_name'] = request.name
                logger.debug(f"{log_prefix}  🔧 更新 robot_name: {request.name}")
            
            if request.avatar_name:
                agent_info['avatar_name'] = request.avatar_name
                basic_config['avatar_name'] = request.avatar_name
                logger.debug(f"{log_prefix}  🔧 更新 avatar_name: {request.avatar_name}")
            if request.avatar_color:
                agent_info['avatar_color'] = request.avatar_color
                basic_config['avatar_color'] = request.avatar_color
                logger.debug(f"{log_prefix}  🔧 更新 avatar_color: {request.avatar_color}")
            
            if request.description:
                agent_info['description'] = request.description
                logger.debug(f"{log_prefix}  🔧 更新 description: {request.description}")
            
            emb_lang_to_use = None
            if request.emb_language:
                emb_lang_to_use = request.emb_language
                basic_config['emb_language'] = emb_lang_to_use
                logger.debug(f"{log_prefix}  🔧 更新 emb_language: {emb_lang_to_use}")
            else:
                lang_lower = lang.lower()
                if lang_lower in ['en', 'english']:
                    emb_lang_to_use = 'en'
                elif lang_lower in ['zh-hant']:
                    emb_lang_to_use = 'zh-hant'
                elif lang_lower == 'zh':
                    emb_lang_to_use = 'zh'
                
                if emb_lang_to_use:
                    old_emb = basic_config.get('emb_language')
                    if old_emb != emb_lang_to_use:
                        basic_config['emb_language'] = emb_lang_to_use
                        logger.debug(f"{log_prefix}  🔧 自动设置 emb_language: {old_emb} -> {emb_lang_to_use} (language={lang})")
            
            if emb_lang_to_use:
                knowledge_cfg = agent_data.get('planning', {}).get('resource', {}).get('knowledge', {})
                kb_list = knowledge_cfg.get('knowledge_base_list', [])
                if isinstance(kb_list, list) and kb_list:
                    for kb in kb_list:
                        if isinstance(kb, dict):
                            old_kb_emb = kb.get('emb_language')
                            if old_kb_emb != emb_lang_to_use:
                                kb['emb_language'] = emb_lang_to_use
                    logger.debug(f"{log_prefix}  🔧 已同步 knowledge_base_list 中的 emb_language 为: {emb_lang_to_use}")
            
            if request.emb_model:
                basic_config['emb_model'] = request.emb_model
                logger.debug(f"{log_prefix}  🔧 更新 emb_model: {request.emb_model}")
            
            tip_message_file = None
            if lang.lower() in ['en', 'english']:
                tip_message_file = os.path.join('input', 'tip_message_en.txt')
            elif lang.lower() in ['zh-hant', 'zh_hant', 'hant', 'cantonese']:
                tip_message_file = os.path.join('input', 'tip_message_zh_hant.txt')
            else:
                tip_message_file = os.path.join('input', 'tip_message_zh_cn.txt')
            
            if tip_message_file and os.path.exists(tip_message_file):
                try:
                    with open(tip_message_file, 'r', encoding='utf-8') as f:
                        tip_message_content = f.read().strip()
                    basic_config['tip_message'] = tip_message_content
                    logger.debug(f"{log_prefix}  🔧 更新 tip_message: 从 {os.path.basename(tip_message_file)} 读取")
                except Exception as e:
                    logger.warning(f"{log_prefix}  ⚠️ 读取 tip_message 文件失败: {e}")
            else:
                logger.warning(f"{log_prefix}  ⚠️ tip_message 文件不存在: {tip_message_file}")
            
            with open(final_agent_file, 'w', encoding='utf-8') as f:
                json.dump(agent_data, f, indent=2, ensure_ascii=False)
        
        if not is_multi_file:
            crud.update_step_status(db, task_id, 8, StepStatus.COMPLETED, "合并完成", output_files=[final_agent_file])
        
        result["final_agent_file"] = final_agent_file
        
        # ========================================
        # Step 8.1: 启用 SFT 模型配置
        # ========================================
        if not is_multi_file and request.use_sft_model:
            crud.update_step_status(db, task_id, 8.1, StepStatus.RUNNING, "正在启用SFT模型...")
        
        if request.use_sft_model:
            try:
                from step8_1_enable_sft_model import (
                    load_sft_config,
                    process_file
                )
                sft_model_name = request.sft_model_name
                if os.path.exists(final_agent_file):
                    name_to_label = load_sft_config(Path("sft_model_config.json"))
                    updated, missing, wrote = process_file(
                        Path(final_agent_file), name_to_label, sft_model_name
                    )
                    if wrote:
                        logger.info(
                            f"{log_prefix}  ✅ SFT模型配置完成: 启用={updated}, 未映射={missing}"
                        )
                    else:
                        logger.warning(f"{log_prefix}⚠️ 最终文件无 intention_list，跳过Step 8.1")
                else:
                    logger.warning(f"{log_prefix}⚠️ 最终文件不存在，跳过Step 8.1: {final_agent_file}")
            except Exception as e:
                logger.warning(f"{log_prefix}⚠️ Step 8.1 启用SFT模型失败: {e}")

        # ========================================
        # Step 8.5: 更新最终文件中的知识库IDs
        # ========================================
        if not is_multi_file:
            crud.update_step_status(db, task_id, 8.5, StepStatus.RUNNING, "正在更新知识库IDs...")
        
        from step8_5_update_final_kb_ids import update_final_kb_ids
        
        kb_mapping_file = os.path.join(output_dir, 'qa_knowledge_bases', f'kb_per_intent_results_{lang}.json')
        intents_file = os.path.join(output_dir, 'step1_processed', f'intents_{lang}.json')
        
        if os.path.exists(kb_mapping_file) and os.path.exists(final_agent_file):
            success = update_final_kb_ids(
                final_file=final_agent_file,
                kb_mapping_file=kb_mapping_file,
                intents_file=intents_file if os.path.exists(intents_file) else None,
                output_file=None
            )
            
            if success:
                if not is_multi_file:
                    crud.update_step_status(db, task_id, 8.5, StepStatus.COMPLETED, "知识库IDs更新完成")
            else:
                if not is_multi_file:
                    crud.update_step_status(db, task_id, 8.5, StepStatus.FAILED, "知识库IDs更新失败")
                    crud.create_error(db, task_id, "Step 8.5 知识库IDs更新失败", step_number=8.5)
        else:
            if not os.path.exists(kb_mapping_file):
                logger.warning(f"{log_prefix}⚠️ 知识库映射文件不存在，跳过Step 8.5: {kb_mapping_file}")
            if not os.path.exists(final_agent_file):
                logger.warning(f"{log_prefix}⚠️ 最终文件不存在，跳过Step 8.5: {final_agent_file}")
            
            if not is_multi_file:
                crud.update_step_status(db, task_id, 8.5, StepStatus.SKIPPED, "跳过知识库IDs更新（文件不存在）")
        
        result["success"] = True
        return result
        
    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        logger.error(f"{log_prefix}❌ 处理失败: {e}")
        return result


def run_migration_with_callback(
    db: Session,
    task_id: str,
    request: GoogleConvertRequest
) -> Dict[str, Any]:
    """
    执行迁移流程
    
    支持单文件和多文件处理：
    - 单文件：直接执行 Step 0-9
    - 多文件：循环执行 Step 0-8，然后step8.6合并，最后执行 Step 9
    """
    
    result = {
        "success": False,
        "error": None,
        "final_agent_file": None
    }
    
    # 语言映射
    lang = request.language
    
    # 获取文件列表
    file_paths = request.file_path
    is_multi_file = len(file_paths) > 1

    # 可配置参数
    global_config = {
        "llmcodemodel": request.llmcodemodel,
        "enable_short_memory": request.enable_short_memory,
        "short_chat_count": request.short_chat_count,
        "enable_global_intent": request.enable_global_intent,
        "semantic_confidence": int(request.semantic_confidence * 100),
        "use_sft_model": request.use_sft_model,
        "sft_model_name": request.sft_model_name,
        "ner_version": request.ner_version,  # NER版本: llm, semantic
    }
    
    # 使用 task_id 作为输出目录
    output_dir = os.path.join("output", task_id)
    os.makedirs(output_dir, exist_ok=True)
    logger.debug(f"任务输出目录: {output_dir}")

    if is_multi_file:
        logger.info("="*80)
        logger.info(f"🔄 多文件模式: 共 {len(file_paths)} 个文件")
        logger.info("="*80)
        for i, fp in enumerate(file_paths):
            logger.info(f"   [{i+1}] {os.path.basename(fp)}")
        logger.info("="*80)
    
    try:
        all_final_files = []
        
        if is_multi_file:
            # ========================================
            # 多文件模式：循环处理每个文件
            # ========================================
            crud.update_step_status(db, task_id, 0, StepStatus.RUNNING, f"开始处理 {len(file_paths)} 个文件...")
            
            for idx, file_path in enumerate(file_paths):
                logger.info("")
                logger.info("="*80)
                logger.info(f"📁 开始处理文件 [{idx + 1}/{len(file_paths)}]: {os.path.basename(file_path)}")
                logger.info("="*80)
                
                # 每个文件使用独立的子目录
                file_output_dir = os.path.join(output_dir, f"file_{idx}")
                os.makedirs(file_output_dir, exist_ok=True)
                
                # 处理单个文件
                file_result = _process_single_file_steps(
                    db=db,
                    task_id=task_id,
                    request=request,
                    file_path=file_path,
                    output_dir=file_output_dir,
                    lang=lang,
                    file_index=idx,
                    global_config=global_config,
                    is_multi_file=True
                )
                
                if file_result["success"] and file_result["final_agent_file"]:
                    all_final_files.append(file_result["final_agent_file"])
                    logger.info(f"✅ 文件 [{idx + 1}] 处理成功: {os.path.basename(file_result['final_agent_file'])}")
                else:
                    logger.error(f"❌ 文件 [{idx + 1}] 处理失败: {file_result.get('error', '未知错误')}")
                    # 继续处理其他文件，不中断流程
            
            if not all_final_files:
                error_msg = "所有文件处理失败，没有可合并的结果"
                crud.update_step_status(db, task_id, 8, StepStatus.FAILED, error_msg)
                raise Exception(error_msg)
            
            # ========================================
            # Step 8.6: 合并所有 Agent 文件
            # ========================================
            logger.info("")
            logger.info("="*80)
            logger.info(f"🔀 Step 8.9: 合并 {len(all_final_files)} 个 Agent 文件")
            logger.info("="*80)
            
            crud.update_step_status(db, task_id, 8, StepStatus.RUNNING, f"正在合并 {len(all_final_files)} 个 Agent 文件...")
            
            from step8_6_merge_agents import AgentMerger
            
            merged_output_dir = os.path.join(output_dir, "merged_final")
            os.makedirs(merged_output_dir, exist_ok=True)
            
            # 生成合并后的文件名
            agent_name_safe = (request.name or "Combined_Agent").replace(' ', '_').replace('/', '_').replace('\\', '_')
            if len(agent_name_safe) > 60:
                agent_name_safe = agent_name_safe[:60]
            final_merged_file = os.path.join(merged_output_dir, f"{agent_name_safe}_merged.json")
            
            merger = AgentMerger(global_config=global_config)
            merged_result = merger.merge(
                input_files=all_final_files,
                new_name=request.name or "Combined_Agent",
                new_description=request.description or f"Merged from {len(all_final_files)} agents",
                output_file=final_merged_file
            )
            
            if merged_result and os.path.exists(final_merged_file):
                logger.info(f"✅ 合并成功: {os.path.basename(final_merged_file)}")
                crud.update_step_status(db, task_id, 8, StepStatus.COMPLETED, f"合并完成 ({len(all_final_files)} 个文件)", output_files=[final_merged_file])
                result["final_agent_file"] = final_merged_file
            else:
                error_msg = "Agent 文件合并失败"
                crud.update_step_status(db, task_id, 8, StepStatus.FAILED, error_msg)
                raise Exception(error_msg)
            
        else:
            # ========================================
            # 单文件模式：直接处理
            # ========================================
            file_path = file_paths[0]
            
            file_result = _process_single_file_steps(
                db=db,
                task_id=task_id,
                request=request,
                file_path=file_path,
                output_dir=output_dir,
                lang=lang,
                file_index=0,
                global_config=global_config,
                is_multi_file=False
            )
            
            if not file_result["success"]:
                raise Exception(file_result.get("error", "处理失败"))
            
            result["final_agent_file"] = file_result["final_agent_file"]
        
        final_agent_file = result["final_agent_file"]
        
        # ========================================
        # Step 8 完成，标记任务成功
        # ========================================
        logger.info("="*80)
        logger.info("✅ 工作流转换完成（输出文件已就绪，可手动导入目标平台）")
        logger.info("="*80)
        
        if final_agent_file and os.path.exists(final_agent_file):
            logger.info(f"📁 最终输出文件: {os.path.basename(final_agent_file)}")
            result["success"] = True
        else:
            logger.error(f"❌ 最终文件不存在 - {final_agent_file}")
            result["error"] = "没有生成最终文件"
        
        return result
        
    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        try:
            error_detail = traceback.format_exc()
        except Exception:
            error_detail = str(e)
        crud.create_error(db, task_id, str(e), error_type=type(e).__name__, error_detail=error_detail)
        return result


# ========================================
# 查询接口
# ========================================

@app.get("/google_convert/task/{record_id}", tags=["查询"])
async def get_task_by_record_id(record_id: int, db: Session = Depends(get_db)):
    """
    根据 record_id 查询任务状态
    """
    # 查找包含该 record_id 的任务
    tasks = db.query(MigrationTask).all()
    
    for task in tasks:
        try:
            if task.message:
                extra_info = json.loads(task.message)
                if isinstance(extra_info, dict) and extra_info.get("record_id") == record_id:
                    return {
                        "code": "0",
                        "data": {
                            "task_id": task.task_id,
                            "record_id": record_id,
                            "status": task.status.value,
                            "language": task.language,
                            "current_step": task.current_step,
                            "current_step_name": task.current_step_name,
                            "progress_percent": task.progress_percent,
                            "final_agent_file": task.final_agent_file,
                            "created_at": task.created_at.isoformat() if task.created_at else None,
                            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                            "duration_seconds": task.duration_seconds
                        }
                    }
        except (json.JSONDecodeError, TypeError):
            continue
    
    return {
        "code": "404",
        "message": f"任务不存在: record_id={record_id}"
    }


@app.get("/google_convert/tasks", tags=["查询"])
async def list_convert_tasks(
    status: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    列出所有转换任务
    """
    status_enum = TaskStatus(status) if status else None
    tasks = crud.list_tasks(db, status=status_enum, language=language, limit=limit)
    
    return {
        "code": "0",
        "data": {
            "total": len(tasks),
            "tasks": [
                {
                    "task_id": t.task_id,
                    "status": t.status.value,
                    "agent_name": t.agent_name,
                    "language": t.language,
                    "progress_percent": t.progress_percent,
                    "current_step": t.current_step,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "duration_seconds": t.duration_seconds
                }
                for t in tasks
            ]
        }
    }


@app.get("/google_convert/queue/status", tags=["查询"])
async def get_queue_status():
    """
    查询任务队列状态
    """
    import threading
    
    # 统计运行中的任务
    running_tasks = []
    for thread in threading.enumerate():
        if thread.name.startswith("convert-worker-"):
            running_tasks.append({
                "name": thread.name,
                "is_alive": thread.is_alive(),
                "daemon": thread.daemon
            })
    
    return {
        "code": "0",
        "data": {
            "queue_size": task_queue.qsize(),
            "max_queue_size": MAX_QUEUE_SIZE,
            "max_workers": MAX_WORKERS,
            "workers_started": workers_started,
            "workers": running_tasks,
            "queue_usage_percent": round((task_queue.qsize() / MAX_QUEUE_SIZE * 100) if MAX_QUEUE_SIZE > 0 else 0, 2)
        }
    }


@app.get("/google_convert/task/{task_id}/detail", tags=["查询"])
async def get_task_detail(task_id: str, db: Session = Depends(get_db)):
    """
    获取任务详情（包含步骤、知识库映射、错误）
    """
    details = crud.get_task_with_details(db, task_id)
    
    if not details:
        return {
            "code": "404",
            "message": f"任务不存在: {task_id}"
        }
    
    task = details["task"]
    
    return {
        "code": "0",
        "data": {
            "task_id": task.task_id,
            "status": task.status.value,
            "agent_name": task.agent_name,
            "language": task.language,
            "final_agent_file": task.final_agent_file,
            "statistics": {
                "total_workflows": task.total_workflows,
                "total_intents": task.total_intents,
                "total_kb_created": task.total_kb_created,
                "total_kb_failed": task.total_kb_failed
            },
            "steps": [
                {
                    "step_number": s.step_number,
                    "step_name": s.step_name,
                    "status": s.status.value,
                    "message": s.message,
                    "duration_seconds": s.duration_seconds
                }
                for s in details["steps"]
            ],
            "kb_mappings": [
                {
                    "intent_name": kb.intent_name,
                    "kb_id": kb.kb_id,
                    "status": kb.status.value
                }
                for kb in details["kb_mappings"]
            ],
            "errors": [
                {
                    "step_number": e.step_number,
                    "error_message": e.error_message,
                    "created_at": e.created_at.isoformat() if e.created_at else None
                }
                for e in details["errors"]
            ]
        }
    }


# ========================================
# 系统信息
# ========================================

@app.get("/google_convert/version", tags=["系统"])
async def get_version():
    """获取系统版本信息"""
    version = "unknown"
    try:
        with open("VERSION", "r", encoding="utf-8") as f:
            version = f.read().strip()
    except:
        pass
    
    return {
        "code": "0",
        "data": {
            "version": version,
            "callback_url": CALLBACK_URL or "",
            "features": {
                "database": True,
                "kb_reuse": True,
                "multi_language": True,
                "verbose_mode": os.getenv('VERBOSE_MODE', 'false')
            }
        }
    }


# ========================================
# 文件下载
# ========================================

@app.get("/google_convert/download/{file_path:path}", tags=["文件"])
async def download_file(file_path: str):
    """下载生成的文件"""
    if not file_path.startswith("output"):
        return {"code": "403", "message": "只能下载 output 目录下的文件"}
    
    if not os.path.exists(file_path):
        return {"code": "404", "message": f"文件不存在: {file_path}"}
    
    return FileResponse(path=file_path, filename=os.path.basename(file_path), media_type="application/json")


# ========================================
# 启动入口
# ========================================

if __name__ == "__main__":
    import uvicorn
    import signal
    import sys
    
    def signal_handler(sig, frame):
        """处理中断信号"""
        logger.info("\n🛑 收到中断信号，正在优雅关闭...")
        sys.exit(0)
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 读取版本号
    version = "unknown"
    try:
        with open("VERSION", "r", encoding="utf-8") as f:
            version = f.read().strip()
    except:
        pass
    
    logger.info("="*70)
    logger.info("🚀 Dialogflow CX 工作流迁移工具 - FastAPI 服务")
    logger.info(f"📌 版本: v{version}")
    logger.info("="*70)
    logger.debug("")
    logger.debug("配置信息（从 .env 文件加载）:")
    logger.debug(f"  - 服务地址: {SERVER_HOST}:{SERVER_PORT}")
    logger.debug(f"  - 数据库: {os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '3306')}/{os.getenv('DB_NAME', 'migration_db')}")
    logger.debug(f"  - 回调地址: {CALLBACK_URL if CALLBACK_URL else '（未配置，跳过回调）'}")
    logger.debug(f"  - 日志级别: {os.getenv('LOG_LEVEL', 'INFO')}")
    logger.debug(f"  - 详细模式: {os.getenv('VERBOSE_MODE', 'false')}")
    logger.debug("")
    logger.debug("API 文档:")
    logger.debug(f"  - Swagger UI: http://localhost:{SERVER_PORT}/docs")
    logger.debug(f"  - ReDoc:      http://localhost:{SERVER_PORT}/redoc")
    logger.debug("")
    logger.info("="*70)
    
    try:
        uvicorn.run(
            "run_all_steps_server:app",
            host=SERVER_HOST,
            port=SERVER_PORT,
            log_level="info"
        )
    except KeyboardInterrupt:
        logger.info("\n🛑 服务器已停止（用户中断）")
    except Exception as e:
        logger.error(f"❌ 服务器启动失败: {e}", exc_info=True)
        sys.exit(1)

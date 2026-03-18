"""
日志配置模块
===========

统一的日志配置和处理

作者：chenyu.zhu
日期：2025-12-17
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler


# 全局日志文件路径，供外部获取
_current_log_file = None

# 日志文件大小限制（30MB）
LOG_FILE_MAX_BYTES = 30 * 1024 * 1024  # 30MB
LOG_FILE_BACKUP_COUNT = 10  # 保留10个备份文件


def get_current_log_file() -> str:
    """获取当前日志文件路径"""
    global _current_log_file
    return _current_log_file


def get_logger(name: str, log_to_file: bool = True) -> logging.Logger:
    """
    Create and return a module-level logger with a consistent format.

    - Reads log level from env `LOG_LEVEL` (default: INFO)
    - Logs to stdout AND file (by default)
    - Avoids adding duplicate handlers if called multiple times
    - 日志文件按 30MB 进行切分
    
    Log Levels:
        - DEBUG: 详细的内部运行细节（所有print输出）
        - INFO: 关键步骤和重要信息（默认）
        - WARNING: 警告信息
        - ERROR: 错误信息
    
    Environment Variables:
        - LOG_LEVEL: 设置日志级别 (DEBUG, INFO, WARNING, ERROR)
        - VERBOSE_MODE: 设置为 "true" 等同于 LOG_LEVEL=DEBUG
        - LOG_TO_FILE: 设置为 "false" 禁用文件日志
    
    Args:
        name: Logger 名称
        log_to_file: 是否同时写入文件（默认 True）
    
    Returns:
        logging.Logger 实例
    """
    global _current_log_file
    
    logger = logging.getLogger(name)

    # If handlers already exist, just return (avoid duplicate logs)
    if logger.handlers:
        return logger

    # 检查 VERBOSE_MODE 环境变量
    verbose_mode = os.getenv("VERBOSE_MODE", "false").lower() == "true"
    
    if verbose_mode:
        level_str = "DEBUG"
    else:
        level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    
    level = getattr(logging, level_str, logging.INFO)
    logger.setLevel(level)

    # 格式化器
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 文件处理器
    env_log_to_file = os.getenv("LOG_TO_FILE", "true").lower() != "false"
    if log_to_file and env_log_to_file:
        try:
            # 创建日志目录（在项目根目录下的 logs 文件夹）
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            os.makedirs(log_dir, exist_ok=True)
            
            # 生成带时间戳的日志文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"run_{timestamp}.log"
            log_filepath = os.path.join(log_dir, log_filename)
            
            # 如果是第一次创建日志文件，记录路径
            if _current_log_file is None:
                _current_log_file = log_filepath
            else:
                # 复用已有的日志文件
                log_filepath = _current_log_file
            
            # 使用 RotatingFileHandler 进行日志切分（30MB）
            file_handler = RotatingFileHandler(
                log_filepath,
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUP_COUNT,
                encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            
        except Exception as e:
            # 如果文件日志创建失败，只打印警告，不影响程序运行
            print(f"Warning: Could not create file logger: {e}")

    logger.propagate = False

    return logger


def setup_file_logger(log_filepath: str = None) -> str:
    """
    设置全局日志文件路径（在程序启动时调用一次）
    
    Args:
        log_filepath: 可选的指定日志文件路径，如果不指定则自动生成
        
    Returns:
        日志文件的完整路径
    """
    global _current_log_file
    
    if log_filepath:
        _current_log_file = log_filepath
    else:
        # 创建日志目录（在项目根目录下的 logs 文件夹）
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        
        # 生成带时间戳的日志文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"run_{timestamp}.log"
        _current_log_file = os.path.join(log_dir, log_filename)
    
    return _current_log_file


def reset_log_file():
    """重置日志文件路径（下次 get_logger 时会创建新文件）"""
    global _current_log_file
    _current_log_file = None


def is_verbose() -> bool:
    """
    检查是否启用详细模式
    
    Returns:
        True if VERBOSE_MODE=true or LOG_LEVEL=DEBUG
    """
    verbose_mode = os.getenv("VERBOSE_MODE", "false").lower() == "true"
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    return verbose_mode or log_level == "DEBUG"


class TeeLogger:
    """
    同时输出到控制台和文件的辅助类
    用于捕获 print 语句的输出
    支持日志文件按 30MB 切分
    """
    def __init__(self, log_filepath: str):
        self.terminal = sys.stdout
        self.log_filepath = log_filepath
        self.log_file = open(log_filepath, 'a', encoding='utf-8')
        self.current_size = os.path.getsize(log_filepath) if os.path.exists(log_filepath) else 0
        self.file_index = 0
    
    def _rotate_if_needed(self):
        """检查是否需要切分日志文件"""
        if self.current_size >= LOG_FILE_MAX_BYTES:
            self.log_file.close()
            self.file_index += 1
            # 创建新的日志文件（添加序号）
            base, ext = os.path.splitext(self.log_filepath)
            new_filepath = f"{base}.{self.file_index}{ext}"
            self.log_file = open(new_filepath, 'a', encoding='utf-8')
            self.current_size = 0
    
    def write(self, message):
        self.terminal.write(message)
        self._rotate_if_needed()
        self.log_file.write(message)
        self.current_size += len(message.encode('utf-8'))
        self.log_file.flush()
    
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()
    
    def close(self):
        self.log_file.close()


def enable_print_capture(log_filepath: str = None) -> str:
    """
    启用 print 语句捕获，将所有 print 输出同时写入日志文件
    
    Args:
        log_filepath: 日志文件路径，如果不指定则自动生成
        
    Returns:
        日志文件路径
    """
    global _current_log_file
    
    if log_filepath is None:
        if _current_log_file is None:
            setup_file_logger()
        log_filepath = _current_log_file
    else:
        _current_log_file = log_filepath
    
    # 确保目录存在
    os.makedirs(os.path.dirname(log_filepath), exist_ok=True)
    
    # 替换 stdout
    sys.stdout = TeeLogger(log_filepath)
    
    return log_filepath


def disable_print_capture():
    """禁用 print 语句捕获，恢复标准输出"""
    if isinstance(sys.stdout, TeeLogger):
        sys.stdout.close()
        sys.stdout = sys.stdout.terminal

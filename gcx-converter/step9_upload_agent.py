# -*- coding: utf-8 -*-
"""
Step 9: 上传 Agent 到 Dyna.ai 平台
==================================
功能：
1. 读取 step8_final 目录中生成的 merged JSON 文件
2. 通过 API 将 Agent 配置上传到 Dyna.ai 平台

作者：chenyu.zhu
日期：2025-12-17
"""

import json
import os
import sys
import time
import requests
from typing import Optional, Dict, Any
from datetime import datetime

from run_all_steps_server import GoogleConvertRequest, callback_result
from logger_config import get_logger
logger = get_logger(__name__)


def _parse_env_file(env_path: str = ".env") -> Dict[str, str]:
    """
    手动解析 .env 文件（不依赖 python-dotenv）
    """
    env_values = {}
    try:
        # 尝试多种编码
        for encoding in ['utf-8', 'utf-8-sig', 'gbk', 'latin-1']:
            try:
                with open(env_path, 'r', encoding=encoding) as f:
                    for line in f:
                        line = line.strip()
                        # 跳过空行和注释
                        if not line or line.startswith('#'):
                            continue
                        # 解析 key=value
                        if '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            # 移除可能的引号
                            if (value.startswith('"') and value.endswith('"')) or \
                               (value.startswith("'") and value.endswith("'")):
                                value = value[1:-1]
                            env_values[key] = value
                break  # 成功读取则退出循环
            except UnicodeDecodeError:
                continue
    except FileNotFoundError:
        logger.debug(f"  ⚠️  .env 文件不存在: {env_path}")
    except Exception as e:
        logger.debug(f"  ⚠️  读取 .env 文件失败: {e}")
    
    return env_values


def load_api_config() -> Dict[str, str]:
    """
    加载 API 配置
    手动解析 .env 文件（更可靠）
    
    Returns:
        API 配置字典，包含 BASE_URL, ROBOT_KEY, ROBOT_TOKEN, USERNAME
    """
    # 手动解析 .env 文件
    env_values = _parse_env_file()
    
    # 获取 API 基础 URL 并拼接完整路径
    dyna_api_base = env_values.get("DYNA_API_BASE", "https://agents.dyna.ai")
    
    config = {
        'BASE_URL': f"{dyna_api_base}/openapi/v1/agent/",
        'ROBOT_KEY': env_values.get("ROBOT_KEY", ""),
        'ROBOT_TOKEN': env_values.get("ROBOT_TOKEN", ""),
        'USERNAME': env_values.get("USERNAME", "")
    }
    
    if config['ROBOT_KEY'] and config['ROBOT_TOKEN'] and config['USERNAME']:
        logger.debug("✅ API 配置已加载")
    else:
        logger.warning("API 配置不完整，请检查 .env 文件")
    
    return config


def upload_agent(file_path: str, api_config: Dict[str, str], dry_run: bool = False, 
                 request: Optional[GoogleConvertRequest] = None, task_id: str = None) -> bool:
    """
    上传 Agent 配置到 Dyna.ai 平台
    （完全参照 agent_import_request.py 的实现）
    
    Args:
        file_path: Agent JSON 文件路径
        api_config: API 配置字典
        dry_run: 如果为 True，只检查不上传
        
    Returns:
        是否上传成功
    """
    if not os.path.exists(file_path):
        logger.error(f"  ❌ 文件不存在: {file_path}")
        return False
    
    # 检查 API 配置
    if not api_config.get('ROBOT_KEY') or not api_config.get('ROBOT_TOKEN') or not api_config.get('USERNAME'):
        logger.error("  ❌ API 配置不完整，请检查 ROBOT_KEY, ROBOT_TOKEN, USERNAME")
        return False
    
    if dry_run:
        logger.debug(f"[检查模式] 将上传文件: {file_path}, 大小: {os.path.getsize(file_path) / 1024:.2f} KB")
        return True
    
    # 从 api_config 获取 BASE_URL 并构建上传 URL
    base_url = api_config.get('BASE_URL', 'https://agents.dyna.ai/openapi/v1/agent/')
    url = base_url.rstrip('/') + '/import/'
    
    # 从 URL 提取 host_name
    from urllib.parse import urlparse
    parsed_url = urlparse(url)
    host_name = parsed_url.netloc
    
    # 请求头
    headers = {
        "cybertron-robot-key": api_config['ROBOT_KEY'],
        "cybertron-robot-token": api_config['ROBOT_TOKEN'],
        "username": api_config['USERNAME'],
        "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
        "Accept": "*/*",
        "Host": host_name,
        "Connection": "keep-alive"
    }
    
    # 获取文件名
    file_name = os.path.basename(file_path)
    
    # 获取文件大小
    file_size = os.path.getsize(file_path)
    file_size_mb = file_size / (1024 * 1024)
    
    logger.info(f"  📤 上传 Agent: {file_name} ({file_size_mb:.2f} MB) -> {url}")

    if not os.path.exists(file_path):
        logger.error(f"  ❌ 文件不存在: {file_path}")
        return False
    # 检查文件是否为空
    if os.path.getsize(file_path) == 0:
        logger.error(f"  ❌ 文件为空，无法上传: {file_path}")
        return False
    
    # 从环境变量读取重试次数，默认1次（初次 + 1次重试）
    max_attempts = int(os.getenv("STEP_9_MAX_UPLOAD_ATTEMPTS", "1"))
    attempt = 1
    
    # 根据文件大小动态设置超时时间
    # write by senlin.deng 2026-02-02
    # 使用元组形式的 timeout: (connect_timeout, read_timeout)
    # - connect_timeout: 建立连接的超时时间（如果服务器停止，连接会立即失败）
    # - read_timeout: 等待服务器响应的超时时间（包括上传+服务器处理）
    connect_timeout = 20  # 连接超时：30秒（如果服务器停止，应该很快失败）
    read_timeout = max(600, int(file_size_mb * 40))  # 读取超时：至少 600 秒，大文件更长
    
    while attempt <= max_attempts:
        if attempt > 1:
            # 重试前等待，使用指数退避策略（5s, 10s, 20s, 40s...）
            wait_time = 5 * (2 ** (attempt - 2))
            logger.warning(f"  🔁 重试第 {attempt} 次（共 {max_attempts} 次），等待 {wait_time}s...")
            time.sleep(wait_time)
        try:
            with open(file_path, "rb") as f, requests.Session() as session:
                # 上传文件格式（与 agent_import_request.py 完全一致）
                # 注意：不要手动设置 Content-Type，requests 会自动设置正确的 multipart/form-data
                files = {
                    "file": (file_name, f, "application/json")
                }

                # 发送请求（使用元组形式的timeout，分别设置连接和读取超时）
                # 这样可以在连接阶段快速检测服务器是否停止
                logger.info(
                    f"     上传Agent文件中... (连接超时: {connect_timeout}s, 读取超时: {read_timeout}s)"
                )
                start_time = time.time()
                response = session.post(
                    url,
                    headers=headers,
                    files=files,
                    timeout=(connect_timeout, read_timeout)  # 元组形式：分别设置连接和读取超时
                )
                elapsed_time = time.time() - start_time
            logger.info(
                f"     上传请求返回: HTTP {response.status_code}, 总耗时 {elapsed_time:.2f}s"
            )
            # 检查业务错误码（与 agent_import_request.py 一致）
            try:
                result = response.json()
                response_text = json.dumps(result, ensure_ascii=False, indent=2)
                error_code = result.get("code")
                error_message = result.get("message", "")

                if error_code == "000000":
                    logger.info(f"  ✅ Agent 上传成功！")
                    logger.debug(f"     响应详情: {response_text}")
                    return True  # Success, exit loop and function
                else:
                    # 某些错误码可能是临时性的，应该重试
                    # 400000: Import failed - 可能是临时性错误，应该重试
                    # 500000: 服务器内部错误 - 应该重试
                    # 其他 5xx 错误码 - 应该重试
                    should_retry = False
                    if error_code in ["400000", "500000"]:
                        should_retry = True
                    elif error_code and error_code.startswith("5"):
                        should_retry = True
                    elif response.status_code >= 500:
                        should_retry = True
                    
                    if should_retry and attempt < max_attempts:
                        logger.warning(f"  ⚠️  调用接口：{url} 错误 (code={error_code})，可能是临时性错误，将重试...")
                        logger.warning(f"     错误信息: {error_message}")
                        logger.warning(f"     完整响应: {response_text}")
                        attempt += 1
                        continue  # 重试
                    else:
                        logger.error(f"  ❌ 业务错误: code={error_code}, message={error_message}")
                        logger.error(f"     完整响应: {response_text}")
                        if attempt >= max_attempts:
                            logger.error(f"  ⚠️  已达到最大重试次数 ({max_attempts})，停止重试")
                        return False  # Business error, no retry or max attempts reached, exit
            except Exception as e:
                logger.warning(f"  ⚠️  调用接口：{url} 响应解析失败: {str(e)}")
                logger.warning(f"     响应内容: {response.text[:500]}")
                # 针对 500 且响应不可解析的情况重试
                if response.status_code == 500 and attempt < max_attempts:
                    logger.warning("  🔄 检测到 500 Internal Server Error，准备重试...")
                    attempt += 1
                    continue  # Continue to next while loop iteration
                return False  # Not a 500 or max attempts reached, exit

        except requests.exceptions.ConnectTimeout as e:
            # 连接超时：服务器可能已停止或无法连接
            logger.error(
                f"  ❌ 连接超时：无法连接到服务器 {url}，服务器可能已停止: {str(e)}"
            )
            if attempt < max_attempts:
                attempt += 1
                continue
            return False
        except requests.exceptions.ReadTimeout as e:
            # 读取超时：连接已建立，但等待响应超时（可能是上传过程中服务器停止）
            logger.error(
                f"  ❌ 读取超时：等待服务器响应超时{url}，可能在上传过程中服务器停止: {str(e)}"
            )
            if attempt < max_attempts:
                attempt += 1
                continue
            return False
        except requests.exceptions.Timeout as e:
            # 通用超时（兼容旧版本 requests）
            logger.error(
                f"  ❌ 请求接口：{url} 超时，请检查网络连接或文件大小: {str(e)}"
            )
            if attempt < max_attempts:
                attempt += 1
                continue
            return False
        except requests.exceptions.ConnectionError as e:
            # 连接错误：服务器拒绝连接或已停止
            logger.error(f"  ❌ 连接错误：无法连接到服务器 {url}，服务器可能已停止: {str(e)}")
            logger.error(f"     💡 请检查服务器是否正在运行")
            if attempt < max_attempts:
                attempt += 1
                continue
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"  ❌ 请求接口：{url} 失败: {str(e)}")
            if attempt < max_attempts:
                attempt += 1
                continue
            return False
        except Exception as e:
            logger.error(f"  ❌ 请求接口：{url} 发生错误: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False  # General error, no retry, exit

    # If the loop finishes without returning, it means all attempts failed.
    return False


def find_merged_json_files(step8_dir: str) -> list:
    """
    查找 step8_final 目录中的所有 merged JSON 文件
    
    Args:
        step8_dir: step8_final 目录路径
        
    Returns:
        文件路径列表
    """
    if not os.path.exists(step8_dir):
        return []
    
    merged_files = []
    for file in os.listdir(step8_dir):
        if file.endswith('_merged.json'):
            merged_files.append(os.path.join(step8_dir, file))
    
    return sorted(merged_files)


def main(
    step8_dir: str = None,
    api_config: Dict[str, str] = None,
    dry_run: bool = False,
    file_path: str = None
):
    """
    主函数：上传 Agent 到 Dyna.ai 平台
    
    Args:
        step8_dir: step8_final 目录路径
        api_config: API 配置字典（如果为 None，则自动加载）
        dry_run: 如果为 True，只检查不上传
        file_path: 指定要上传的文件路径（如果指定，则忽略 step8_dir）
    """
    logger.info("Step 9: 上传 Agent 到 Dyna.ai 平台")
    
    # 加载 API 配置
    if api_config is None:
        api_config = load_api_config()
    
    # 检查 API 配置
    if not api_config.get('ROBOT_KEY') or not api_config.get('ROBOT_TOKEN') or not api_config.get('USERNAME'):
        logger.error("\n❌ API 配置不完整")
        logger.info("💡 请在 .env 文件中配置 ROBOT_KEY, ROBOT_TOKEN, USERNAME")
        logger.info("💡 可以参考 env.example 文件创建 .env 文件")
        return
    
    # 确定要上传的文件
    if file_path:
        # 如果指定了文件路径，直接使用
        files_to_upload = [file_path]
    else:
        # 否则从 step8_dir 查找
        if step8_dir is None:
            step8_dir = "output/step8_final"
        
        files_to_upload = find_merged_json_files(step8_dir)
    
    if not files_to_upload:
        logger.warning(f"\n⚠️  未找到要上传的文件")
        if step8_dir:
            logger.debug(f"   在 {step8_dir} 中未找到 *_merged.json 文件")
        return
    
    logger.info(f"\n📂 找到 {len(files_to_upload)} 个文件待上传")
    for f in files_to_upload:
        logger.debug(f"   - {os.path.basename(f)}")
    
    if dry_run:
        logger.debug(f"\n🔍 [检查模式] 不会实际上传")
    
    # 上传文件
    success_count = 0
    failed_count = 0
    upload_results = []
    
    for file_path in files_to_upload:
        logger.debug(f"\n{'='*70}")
        logger.debug(f"📄 处理: {os.path.basename(file_path)}")
        logger.debug(f"{'='*70}")
        
        file_result = {
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "error": None,
            "response": None
        }
        
        success = upload_agent(file_path, api_config, dry_run)
        
        if success:
            success_count += 1
            file_result["success"] = True
        else:
            failed_count += 1
            file_result["error"] = "上传失败"
        
        upload_results.append(file_result)
    
    # 总结
    logger.info(f"\n{'='*70}")
    logger.info("📊 上传总结")
    logger.info(f"{'='*70}")
    logger.info(f"   ✅ 成功: {success_count} 个文件")
    logger.info(f"   ❌ 失败: {failed_count} 个文件")
    logger.info(f"   📋 总计: {len(files_to_upload)} 个文件")
    
    if dry_run:
        logger.debug(f"\n💡 这是检查模式，没有实际上传")
        logger.debug(f"   要实际上传，请运行: python step9_upload_agent.py")
    
    # 保存日志文件
    log_dir = "output/step9_upload_logs"
    os.makedirs(log_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"upload_log_{timestamp}.json")
    
    log_data = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total": len(files_to_upload),
            "success": success_count,
            "failed": failed_count
        },
        "files": upload_results
    }
    
    # 保存日志
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        logger.debug(f"\n💾 日志已保存: {log_file}")
    except Exception as e:
        logger.error(f"\n⚠️  保存日志失败: {e}")
    
    logger.info("=" * 70)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='上传 Agent 到 Dyna.ai 平台')
    parser.add_argument('--input', '-i', type=str, default='output/step8_final',
                        help='step8_final 目录路径')
    parser.add_argument('--file', '-f', type=str, default=None,
                        help='指定要上传的文件路径（如果指定，则忽略 --input）')
    parser.add_argument('--dry-run', '-d', action='store_true',
                        help='检查模式，只检查不上传')
    
    args = parser.parse_args()
    
    try:
        main(
            step8_dir=args.input,
            file_path=args.file,
            dry_run=args.dry_run
        )
    except Exception as e:
        logger.error(f'❌ 错误: {str(e)}')
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

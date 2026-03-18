"""
创建知识库和导入Q&A的API工具
- 创建知识库
- 从Excel文件批量导入Q&A

API 文档: https://agents.dyna.ai/

作者：chenyu.zhu
日期：2025-12-17
"""

import requests
import json
import os
from typing import Dict, Any, Optional

# 从 .env 文件读取配置
from dotenv import load_dotenv
load_dotenv()

from logger_config import get_logger
logger = get_logger(__name__)


class KnowledgeBaseAPI:
    """知识库 API 调用类"""
    
    def __init__(self, 
                 robot_key: str,
                 robot_token: str,
                 username: str,
                 base_url: str = None):
        """
        初始化 API 客户端
        
        Args:
            robot_key: robot标识（在赛博坦平台申请）
            robot_token: robot口令（在赛博坦平台申请）
            username: 赛博坦用户账号
            base_url: API 基础地址（如果为 None，则从 .env 文件的 STEP_3_BASE_URL 读取，如果也没有则使用默认值）
        """
        if base_url is None:
            # 从 .env 文件读取，优先使用 DYNA_API_BASE
            base_url = os.getenv("DYNA_API_BASE", os.getenv("DYNA_KB_BASE_URL", "https://agents.dyna.ai"))
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "cybertron-robot-key": robot_key,
            "cybertron-robot-token": robot_token,
            "username": username,
            "Content-Type": "application/json"
        }
    
    def create_knowledge_base(self,
                            name: str,
                            description: str,
                            emb_language: str = "zh",
                            emb_model: str = "bge-large-zh",
                            max_retries: int = 3) -> Dict[str, Any]:
        """
        创建知识库（带重试机制）
        
        Args:
            name: 知识库名称（必传）
            description: 知识库描述（必传）
            emb_language: 语种（必传），默认 "zh"（中文）
            emb_model: embedding模型（必传，且与语种匹配），默认 "bge-large-zh"
            max_retries: 最大重试次数（默认3次）
            
        Returns:
            响应数据字典
            {
                "code": "000000",  # 000000表示成功，其他表示异常
                "message": "success",
                "data": {
                    "nickname": "所有者",
                    "id": 1882,
                    "knowledge_base_name": "知识库名",
                    "description": "知识库的描述",
                    "emb_language": "zh"
                }
            }
        """
        url = f"{self.base_url}/openapi/v2/knowledge/info/"
        
        payload = {
            "name": name,
            "description": description,
            "emb_language": emb_language,
            "emb_model": emb_model
        }
        
        # 🔄 重试机制
        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                import time
                wait_time = min(2 ** (attempt - 1), 10)  # 指数退避，最多等待10秒
                logger.warning(f"   🔁 重试第 {attempt}/{max_retries} 次，等待 {wait_time} 秒...")
                time.sleep(wait_time)
            
            try:
                response = requests.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=30
                )
                
                # 检查响应状态码
                if response.status_code != 200:
                    error_msg = f"HTTP {response.status_code}: {response.text[:500]}"
                    # 如果是5xx错误且还有重试机会，继续重试
                    if 500 <= response.status_code < 600 and attempt < max_retries:
                        logger.warning(f"服务器错误 ({response.status_code})，将重试")
                        continue
                    logger.error(f"请求失败: {error_msg}")
                    return {"code": "ERROR", "message": error_msg}
                
                # 检查响应内容是否为空
                if not response.text or not response.text.strip():
                    error_msg = "响应为空，服务器可能未返回任何内容"
                    if attempt < max_retries:
                        logger.warning(f"响应为空，将重试")
                        continue
                    logger.error(f"请求失败: {error_msg}")
                    return {"code": "ERROR", "message": error_msg}
                
                # 解析响应
                try:
                    result = response.json()
                except json.JSONDecodeError as e:
                    error_msg = f"响应解析失败，返回内容不是有效的JSON: {str(e)}"
                    if attempt < max_retries:
                        logger.warning(f"JSON解析失败，将重试")
                        continue
                    logger.error(f"请求失败: {error_msg}")
                    logger.error(f"响应状态码: {response.status_code}")
                    logger.error(f"响应内容（前500字符）: {response.text[:500]}")
                    logger.error(f"响应头: {dict(response.headers)}")
                    return {"code": "ERROR", "message": error_msg}
                
                # 检查业务状态码
                if result.get("code") == "000000":
                    if attempt > 1:
                        logger.info(f"   ✅ 重试成功！（第 {attempt} 次尝试）")
                    logger.info("✅ 知识库创建成功！")
                    return result
                
                # 业务错误（如重名）不需要重试，直接返回
                error_code = result.get('code', '')
                if error_code == "400001":  # 名称重复
                    # 改为 debug 级别，避免误导（调用方会尝试复用已存在的知识库）
                    logger.debug(f"ℹ️  知识库名称已存在: {result.get('message')}")
                    return result
                
                # 其他业务错误，如果还有重试机会则重试
                if attempt < max_retries:
                    logger.warning(f"业务错误 ({error_code})，将重试")
                    continue
                
                logger.error(f"❌ 创建失败: {result.get('message')}")
                return result
                
            except requests.exceptions.Timeout as e:
                error_msg = f"请求超时，请检查网络连接, {str(e)}"
                if attempt < max_retries:
                    logger.warning(f"请求超时，将重试")
                    continue
                logger.error(f"❌ 错误: {error_msg}")
                return {"code": "ERROR", "message": error_msg}
            except requests.exceptions.ConnectionError as e:
                error_msg = "连接失败，请检查网络连接或 API 地址，{str(e)}"
                if attempt < max_retries:
                    logger.warning(f"连接失败，将重试")
                    continue
                logger.error(f"❌ 错误: {error_msg}")
                return {"code": "ERROR", "message": error_msg}
            except requests.exceptions.RequestException as e:
                error_msg = f"请求失败: {str(e)}"
                if attempt < max_retries:
                    logger.warning(f"请求异常，将重试: {error_msg}")
                    continue
                logger.error(f"❌ 错误: {error_msg}")
                return {"code": "ERROR", "message": error_msg}
            except Exception as e:
                error_msg = f"未知错误: {str(e)}"
                if attempt < max_retries:
                    logger.warning(f"发生异常，将重试: {error_msg}")
                    continue
                logger.error(f"❌ 错误: {error_msg}", exc_info=True)
                return {"code": "ERROR", "message": error_msg}
        
        # 所有重试都失败
        error_msg = f"创建知识库失败，已重试 {max_retries} 次"
        logger.error(error_msg)
        return {"code": "ERROR", "message": error_msg}
    
    def list_knowledge_bases(self, page: int = 1, size: int = 100) -> Dict[str, Any]:
        """
        查询知识库列表
        
        Args:
            page: 页码（默认 1）
            size: 每页数量（默认 100）
            
        Returns:
            响应数据字典
            {
                "code": "000000",
                "message": "success",
                "data": {
                    "list": [
                        {
                            "id": 1882,
                            "knowledge_base_name": "...",
                            "description": "...",
                            ...
                        }
                    ],
                    "total": 10
                }
            }
        """
        url = f"{self.base_url}/openapi/v2/knowledge/list"
        
        params = {
            "page": page,
            "size": size
        }
        
        try:
            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=30
            )
            
            result = response.json()
            return result
            
        except Exception as e:
            return {"code": "ERROR", "message": str(e), "data": {"list": [], "total": 0}}
    
    def check_kb_exists_by_name(self, kb_name: str) -> Optional[Dict[str, Any]]:
        """
        检查是否已存在同名知识库
        
        Args:
            kb_name: 知识库名称
            
        Returns:
            如果存在，返回知识库信息字典；否则返回 None
        """
        try:
            # 查询所有知识库（可能需要分页，这里先查询前100个）
            result = self.list_knowledge_bases(page=1, size=100)
            
            if result.get("code") == "000000":
                kb_list = result.get("data", {}).get("list", [])
                for kb in kb_list:
                    if kb.get("knowledge_base_name") == kb_name:
                        return kb
            
            # 如果总数超过100，继续查询后续页
            total = result.get("data", {}).get("total", 0)
            if total > 100:
                for page in range(2, (total // 100) + 2):
                    result = self.list_knowledge_bases(page=page, size=100)
                    if result.get("code") == "000000":
                        kb_list = result.get("data", {}).get("list", [])
                        for kb in kb_list:
                            if kb.get("knowledge_base_name") == kb_name:
                                return kb
            
            return None
            
        except Exception as e:
            logger.warning(f"检查知识库是否存在时出错: \n{str(e)}")
            return None
    
    def import_qa_from_file(self,
                           username: str,
                           knowledge_base_id: str,
                           folder_id: str = "root",
                           file_path: str = None,
                           api_endpoint: str = "/openapi/v2/knowledge/qa-batch/") -> Dict[str, Any]:
        """
        从 Excel 文件批量导入知识库 Q&A
        
        Args:
            username: 用户账号（必传）
            knowledge_base_id: 知识库ID（必传）
            folder_id: 文件夹ID（默认 "root"）
            file_path: Excel 文件路径（如 "question-answer template.xlsx"）
            api_endpoint: API 端点路径（可选，默认 "/openapi/v1/knowledge/qa-batch/"）
            
        Returns:
            响应数据字典
            {
                "code": "000000",  # 000000表示成功，其他表示异常
                "message": "success",
                "data": {...}
            }
        """
        url = f"{self.base_url}{api_endpoint}"
        
        # 准备文件上传
        try:
            # 打开文件
            with open(file_path, 'rb') as f:
                files = {
                    'file': (file_path.split('/')[-1].split('\\')[-1], f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                }
                
                # 准备表单数据（只包含 knowledge_base_id 和 folder_id）
                data = {
                    'knowledge_base_id': knowledge_base_id,
                    'folder_id': folder_id
                }
                
                # 准备 headers（username 放在 header 中，移除 Content-Type 让 requests 自动设置）
                headers = {
                    "cybertron-robot-key": self.headers["cybertron-robot-key"],
                    "cybertron-robot-token": self.headers["cybertron-robot-token"],
                    "username": username
                }
                
                logger.debug(f"正在从文件导入 Q&A 到知识库: {knowledge_base_id}")
                logger.debug(f"请求地址: {url}")
                logger.debug(f"文件路径: {file_path}")
                
                response = requests.post(
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=60  # 文件上传可能需要更长时间
                )
            
            # 检查响应状态码
            logger.debug(f"响应状态码: {response.status_code}")
            
            # 检查响应内容是否为空
            if not response.text or not response.text.strip():
                error_msg = "响应为空，服务器可能未返回任何内容"
                logger.error(f"❌ 错误: {error_msg}")
                logger.error(f"响应状态码: {response.status_code}")
                logger.error(f"响应头: {dict(response.headers)}")
                return {"code": "ERROR", "message": error_msg}
            
            # 解析响应
            try:
                result = response.json()
            except json.JSONDecodeError as e:
                error_msg = f"响应解析失败，返回内容不是有效的JSON: {str(e)}"
                logger.error(f"❌ 错误: {error_msg}")
                logger.error(f"响应状态码: {response.status_code}")
                logger.error(f"响应内容（前500字符）: {response.text[:500]}")
                logger.error(f"响应头: {dict(response.headers)}")
                return {"code": "ERROR", "message": error_msg}
            
            logger.debug(f"响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
            
            # 检查业务状态码
            if result.get("code") == "000000":
                logger.debug("✅ Q&A 文件导入成功！")
            else:
                error_msg = result.get('message', '未知错误')
                logger.error(f"❌ 导入失败: {error_msg}")
            
            return result
            
        except FileNotFoundError as e:
            error_msg = f"文件未找到: {file_path}: {str(e)}"
            logger.error(f"❌ 错误: {error_msg}")
            return {"code": "ERROR", "message": error_msg}
            
        except requests.exceptions.Timeout as e:
            error_msg = f"请求超时，请检查网络连接或文件大小: {str(e)}"
            logger.error(f"❌ 错误: {error_msg}")
            return {"code": "ERROR", "message": error_msg}
            
        except requests.exceptions.RequestException as e:
            error_msg = f"请求失败:"
            logger.error(f"❌ 错误: {error_msg}")
            import traceback
            logger.error(f"错误详情:\n{traceback.format_exc()}")
            return {"code": "ERROR", "message": error_msg}


def create_kb_and_import_qa(name: str,
                            description: str,
                            excel_file: str,
                            robot_key: str,
                            robot_token: str,
                            username: str,
                            emb_language: str = "zh",
                            emb_model: str = "bge-large-zh") -> Optional[str]:
    """
    便捷函数：创建知识库并导入Q&A Excel文件
    
    Args:
        name: 知识库名称
        description: 知识库描述
        excel_file: Excel文件路径
        robot_key: robot标识
        robot_token: robot口令
        username: 用户账号
        emb_language: 语种
        emb_model: embedding模型
        
    Returns:
        成功返回知识库ID，失败返回None
    """
    api = KnowledgeBaseAPI(robot_key, robot_token, username)
    
    # 步骤1：创建知识库
    logger.info("="*80)
    logger.info("【步骤 1/2】创建知识库")
    logger.info("="*80)
    
    result = api.create_knowledge_base(name, description, emb_language, emb_model)
    
    if result.get("code") != "000000":
        logger.error("❌ 知识库创建失败，无法继续")
        return None
    
    kb_id = str(result.get("data", {}).get("id"))
    
    # 步骤2：导入Q&A
    logger.info("\\n" + "="*80)
    logger.info("【步骤 2/2】导入Q&A Excel文件")
    logger.info("="*80)
    
    result = api.import_qa_from_file(
        username=username,
        knowledge_base_id=kb_id,
        folder_id="root",
        file_path=excel_file
    )
    
    if result.get("code") == "000000":
        logger.info("✅ Q&A导入成功！")
    else:
        logger.warning(f"⚠️  知识库已创建（ID: {kb_id}），但Q&A导入失败")
    
    return kb_id


if __name__ == "__main__":
    # 示例：创建知识库并导入Q&A
    logger.info("="*80)
    logger.info("🚀 创建知识库 + 导入 Q&A Excel 文件")
    logger.info("="*80)
    logger.info("\\n请修改下方的配置信息后运行\\n")
    logger.info("="*80)
    
    # 配置信息（从 .env 文件读取）
    ROBOT_KEY = os.getenv("ROBOT_KEY", "your_robot_key")
    ROBOT_TOKEN = os.getenv("ROBOT_TOKEN", "your_robot_token")
    USERNAME = os.getenv("USERNAME", "your_username")
    
    # 创建知识库并导入Q&A
    # kb_id = create_kb_and_import_qa(
    #     name="测试知识库",
    #     description="测试知识库描述",
    #     excel_file="question-answer template.xlsx",
    #     robot_key=ROBOT_KEY,
    #     robot_token=ROBOT_TOKEN,
    #     username=USERNAME
    # )
    #
    # if kb_id:
    #     logger.info(f"\\n✅ 完成！知识库ID: {kb_id}")
    
    logger.info("\\n💡 提示：请使用 kb_and_q-import.py 脚本运行完整流程")

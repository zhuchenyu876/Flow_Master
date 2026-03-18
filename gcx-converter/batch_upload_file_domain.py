# -*- coding: utf-8 -*-
"""
批量上传脚本 - 通过 API 请求方式
================================
功能：
1. 读取 PoC Flow Data By Journey - 23 目录中的所有 exported_flow JSON 文件
2. 依次调用 API 创建转换任务
3. 支持断点续传（跳过已成功的任务）

使用方法：
    python batch_upload_file_new.py

作者：senlin.deng
日期：2026-01-30
"""

import requests
import json
import os
from pathlib import Path
from datetime import datetime
import pandas as pd
# ========================================
# 配置区域
# ========================================

# API 地址
API_URL = "http://localhost:8000/google_convert/task/create"

# 输入目录
INPUT_DIR = r"D:\上线业务\dialogflow迁移\汇丰对话流迁移_HSBC_debug\googlecx-clean_update_version\input\PoC_Flow_Data_By_Journey_41"

# 设置具体空间：Domain跑批测试
USERNAME = "1000_owen.z.li@hsbc.com.hk"
ROBOT_KEY = r"23ZO2FQGC1HU%2Fa%2F40Cv8su9fm7g%3D"
ROBOT_TOKEN = r"MTc2OTc2NjE5NDU5NApGWjFmTzk0Snp4bHdJZ0NWUDRWbHpPbWRKMjg9"

# 任务配置
LANGUAGE = "zh-hant"
EMB_LANGUAGE = "zh-hant"
EMB_MODEL = "bge-m3"
FAQ_VERSION = "Semantic Judgement Version"
IS_DEBUG = True

# LLMCODEMODEL = "azure-prefix0-gpt-5.1-chat"  # qwen3-30b-a3b
LLMCODEMODEL = "qwen3-30b-a3b"
# LLMCODEMODEL = "azure-gpt-5.1-chat"
USE_SFT_MODEL = True
SFT_MODEL_NAME = "internal0-br-llm-hsbcllm-v1-20250105"

# 基础 record_id（每个任务会递增）
RECORD_ID_BASE = 1235656

# ========================================
# 工具函数
# ========================================

def load_domain_file():
    file = r'D:\上线业务\dialogflow迁移\汇丰对话流迁移_HSBC_debug\41个domain_new.xlsx'
    
    df = pd.read_excel(file, sheet_name='Sheet2')
    data = {}
    for _, row in df.iterrows():
        if row['Domain'] not in data:
            data[row['Domain']] = []
        if '\n' in row['DFCX_Flow']:
            dfcx_flows = row['DFCX_Flow'].split('\n')
            for dfcx_flow in dfcx_flows:
                data[row['Domain']].append(os.path.join(INPUT_DIR, f'exported_flow_{dfcx_flow.strip()}.json'))
        else:
            data[row['Domain']].append(os.path.join(INPUT_DIR, f'exported_flow_{row["DFCX_Flow"].strip()}.json'))
    return data

def create_task(file_paths: list[str], domain: str, record_id: int) -> dict:
    """
    调用 API 创建单个任务
    
    Args:
        file_path: JSON 文件的完整路径
        record_id: 任务记录 ID
        
    Returns:
        API 响应结果
    """
    global LANGUAGE
    lan2lan = {
        "en": "",
        "zh": "_SC",
        "zh-hant": "_TC",
    }
    domain = f"{domain}{lan2lan[LANGUAGE]}"
    payload = {
        "record_id": record_id,
        "file_path": file_paths,
        "username": USERNAME,
        "language": LANGUAGE,
        "cybertron-robot-key": ROBOT_KEY,
        "cybertron-robot-token": ROBOT_TOKEN,
        "name": domain,
        "description": domain,
        "emb_language": EMB_LANGUAGE,
        "emb_model": EMB_MODEL,
        "faq_version": FAQ_VERSION,
        "is_debug": IS_DEBUG,
        "use_sft_model": USE_SFT_MODEL,
        "llmcodemodel": LLMCODEMODEL,
        "sft_model_name": SFT_MODEL_NAME
    }
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    response = requests.post(API_URL, headers=headers, data=json.dumps(payload), timeout=30)
    
    return {
        "status_code": response.status_code,
        "response": response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
    }


# ========================================
# 主函数
# ========================================

def main():
    global RECORD_ID_BASE
    print(f"\n📁 输入目录: {INPUT_DIR}")
    print(f"🌐 语言: {LANGUAGE}")
    print(f"🔗 API: {API_URL}")
    
    domain_data = load_domain_file()
    # print(domain_data)
    # exit()

    for domain, file_paths in domain_data.items():
        record_id = RECORD_ID_BASE
        RECORD_ID_BASE += 1
        print(f"Convert: {domain}")
        
        try:
            result = create_task(file_paths, domain, record_id)
            
            # 判断是否成功（根据 API 响应）
            is_success = result["status_code"] == 200
            if isinstance(result["response"], dict):
                # 检查响应中的 code 字段（可能是字符串或整数）
                api_code = result["response"].get("code", -1)
                is_success = is_success and (str(api_code) == "0" or str(api_code) == "200")
            
            if is_success:
                print(f"  ✅ 成功！")
            else:
                print(f"  ❌ 失败！")
                
        except requests.exceptions.Timeout:
            print(f"  ❌ 请求超时")
            
        except requests.exceptions.ConnectionError:
            print(f"  ❌ 连接失败，请检查 API 服务是否运行")
            
        except Exception as e:
            print(f"  ❌ 异常: {str(e)}")

    print("\n" + "="*80)
    print("批量上传完成")
    print("="*80)


if __name__ == "__main__":
    main()

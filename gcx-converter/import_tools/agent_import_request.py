#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Agent 导入工具
从 .env 读取 API 凭证
"""
import sys
import os

# 第一行输出
print("=" * 80, flush=True)
print("*" * 30 + "开始导入Agent" + "*" * 30, flush=True)
print("=" * 80, flush=True)
print(flush=True)

# 添加上级目录到路径（用于加载 .env）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入其他模块
try:
    print("正在加载依赖库...", flush=True)
    import requests
    import logging
    from dotenv import load_dotenv
    print("✅ 依赖库加载成功", flush=True)
except ImportError as e:
    print(f"❌ 缺少依赖库: {e}", flush=True)
    print("请运行: pip install requests python-dotenv", flush=True)
    sys.exit(1)

# 加载 .env 配置（从上级目录）
print("正在加载 .env 配置...", flush=True)
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
print(f"   .env 文件路径: {env_path}", flush=True)
print(f"   .env 文件存在: {os.path.exists(env_path)}", flush=True)
load_dotenv(dotenv_path=env_path)
print("✅ .env 配置加载完成", flush=True)
print(flush=True)

# 配置 logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# 文件路径（可以通过命令行参数指定，或使用默认值）
if len(sys.argv) > 1:
    file_path = sys.argv[1]
    print(f"✅ 使用命令行参数指定的文件: {file_path}", flush=True)
else:
    # file_path = rf"C:\Users\zhefei.lv\Desktop\谷歌flow迁移调研\code_git\googlecx-migrationtool\output\TXNAndSTMT_Deeplink\step8_final\agent_CardServicing_Fulfillment_en_merged.json"
    file_path = rf"C:\Users\zhefei.lv\Desktop\谷歌flow迁移调研\code_git\code_stable\googlecx-migrationtool\output\step8_final\agent_CardServicing_Fulfillment_en_merged.json"
    print(f"✅ 使用默认文件: {file_path}", flush=True)
print(flush=True)

host_name = "agents.dyna.ai"

# 请求 URL（参考 curl 示例使用 https）
url = f"https://{host_name}/openapi/v1/agent/import/"

# 从 .env 读取配置
token = os.getenv("ROBOT_TOKEN", "")
key = os.getenv("ROBOT_KEY", "")
# 注意：不使用 USERNAME（Windows 系统保留变量），改用 ROBOT_USERNAME
username = os.getenv("ROBOT_USERNAME", "") or os.getenv("API_USERNAME", "")

# 检查配置是否存在
if not token or not key or not username:
    print("=" * 80, flush=True)
    print("❌ 错误: 请在 .env 文件中配置以下变量:", flush=True)
    if not token:
        print("   - ROBOT_TOKEN", flush=True)
    if not key:
        print("   - ROBOT_KEY", flush=True)
    if not username:
        print("   - ROBOT_USERNAME (或 API_USERNAME)", flush=True)
    print("=" * 80, flush=True)
    sys.exit(1)

print("✅ 从 .env 读取配置", flush=True)
# print(f"   - Username: {username}", flush=True)
# print(f"   - Robot Key: {key[:20]}..." if len(key) > 20 else f"   - Robot Key: {key}", flush=True)
# print(f"   - Robot Token: {token[:20]}..." if len(token) > 20 else f"   - Robot Token: {token}", flush=True)
print(flush=True)

# 请求头（参考 curl 示例，不要手动设置 Content-Type，让 requests 自动处理）
headers = {
    "cybertron-robot-key": key,
    "cybertron-robot-token": token,
    "username": username,
    "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
    "Accept": "*/*",
    "Host": host_name,
    "Connection": "keep-alive"
    # 注意：不要手动设置 Content-Type，requests 在使用 files 参数时会自动设置正确的 multipart/form-data boundary
}

# 获取文件名（只使用文件名，不使用完整路径）
file_name = os.path.basename(file_path)

session = requests.Session()

def logged_request(method, url, **kwargs):
    logging.info(f"➡️ 请求方法: {method.upper()}")
    # logging.info(f"➡️ 请求URL: {url}")
    # if 'headers' in kwargs:
        # logging.info(f"➡️ Header: {kwargs['headers']}")
    if 'data' in kwargs:
        logging.info(f"➡️ Data: {kwargs['data']}")
    if 'json' in kwargs:
        logging.info(f"➡️ JSON: {kwargs['json']}")
    if 'files' in kwargs:
        file_names = []
        for f in kwargs['files'].values():
            if hasattr(f, "name"):
                file_names.append(f.name)
            elif isinstance(f, tuple) and len(f) > 0:
                file_names.append(f[0])
        logging.info(f"➡️ 文件: {file_names}")
    
    response = session.request(method, url, **kwargs)
    logging.info(f"✅ 响应状态: {response.status_code}")
    logging.info(f"✅ 响应内容: {response.text[:500]}")  # 防止太长
    return response

# 使用 with 语句确保文件正确关闭（参考 curl 的 --form 'file=@"文件路径"' 格式）
try:
    with open(file_path, "rb") as f:
        # 上传文件（参考 curl 的 --form 格式）
        files = {
            "file": (file_name, f, "application/json")
        }
        
        # 发送 POST 请求
        print("=" * 80, flush=True)
        # print(f"📤 发送请求到: {url}", flush=True)
        print(f"📁 上传文件: {file_name}", flush=True)
        print("=" * 80, flush=True)
        
        response = logged_request('POST', url, headers=headers, files=files)
        
        # 输出返回结果
        print("\n" + "=" * 80, flush=True)
        print(f"📥 响应状态码: {response.status_code}", flush=True)
        print(f"📥 响应内容: {response.text[:500]}", flush=True)
        print("=" * 80, flush=True)
        
        # 检查业务错误码
        try:
            result = response.json()
            if result.get("code") == "000000":
                print("\n✅ 请求成功！", flush=True)
                print(f"✅ 结果: {result}", flush=True)
            else:
                print(f"\n❌ 业务错误: code={result.get('code')}, message={result.get('message')}", flush=True)
        except Exception as e:
            print(f"\n⚠️  响应解析失败: {str(e)}", flush=True)
            
except FileNotFoundError:
    print(f"❌ 文件未找到: {file_path}", flush=True)
    print("请检查文件路径是否正确", flush=True)
except requests.exceptions.Timeout:
    print("❌ 请求超时，请检查网络连接或文件大小", flush=True)
except requests.exceptions.RequestException as e:
    print(f"❌ 请求失败: {str(e)}", flush=True)
except Exception as e:
    print(f"❌ 发生错误: {str(e)}", flush=True)
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80, flush=True)
print("程序执行完毕", flush=True)
print("=" * 80, flush=True)

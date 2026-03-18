import requests
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

file_path = rf"D:\2025 Dyna.ai\google dialog migration\GoogleCX-Migration-tool-add_group_jump (1)\GoogleCX-clean - 副本(佛脚)\googlecx-migrationtool\output\step8_final\CardServicing_Agent_CardServicing_Fulfillment_en_merged.json"

host_name = "agents.dyna.ai"

# 请求 URL（参考 curl 示例使用 https）
url = f"https://{host_name}/openapi/v1/agent/import/"

token = "MTc2NDIyMTUyMTQwNgp6bEs0dnB0WkQrdWZ3d1ZYN2RFQjQwMkgxMlE9"
key = "lXvKMXVQ%2BebdDtLO0TuYl93oMTk%3D"
username = "hsbc_migration@dyna.ai"

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
    #logging.info(f"➡️ 请求URL: {url}")
    if 'headers' in kwargs:
        logging.info(f"➡️ Header: {kwargs['headers']}")
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
        #print(f"请求 URL: {url}")
        response = logged_request('POST', url, headers=headers, files=files)
        
        # 输出返回结果
        print(f"\n响应状态码: {response.status_code}")
        print(f"响应内容: {response.text}")
        
        # 检查业务错误码
        try:
            result = response.json()
            if result.get("code") == "000000":
                print("\n✅ 请求成功！")
            else:
                print(f"\n❌ 业务错误: code={result.get('code')}, message={result.get('message')}")
        except Exception as e:
            print(f"\n⚠️  响应解析失败: {str(e)}")
            
except FileNotFoundError:
    print(f"❌ 文件未找到: {file_path}")
except requests.exceptions.Timeout:
    print("❌ 请求超时，请检查网络连接或文件大小")
except requests.exceptions.RequestException as e:
    print(f"❌ 请求失败: {str(e)}")
except Exception as e:
    print(f"❌ 发生错误: {str(e)}")

# -*- coding: utf-8 -*-
"""
Flow 导入工具
=============
功能：将生成的 workflow JSON 文件导入到 Dyna.ai 平台

使用方法：
1. 在 .env 文件中配置以下变量：
   - FLOW_IMPORT_BASE_URL: API 基础地址（默认：https://saibotan-pre5.100credit.cn/openapi/v1/chatflow）
   - ROBOT_KEY: 机器人密钥
   - ROBOT_TOKEN: 机器人令牌
   - USERNAME: 用户名
   - FLOW_IMPORT_HOMELAND_ID: 家园ID（默认：221）

2. 运行脚本：
   python flow_import.py <workflow_json_file>

作者：AI Assistant
日期：2025-01-XX
"""

import json
import os
import sys
import requests
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime


def load_config() -> Dict[str, str]:
    """
    加载配置
    从 .env 文件读取配置
    
    Returns:
        配置字典
    """
    from dotenv import load_dotenv
    # 确保从项目根目录加载 .env 文件
    # 脚本在 导入工具/flow导入 目录，需要向上两级到项目根目录
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent
    env_file = project_root / '.env'
    
    # 使用 override=True 确保 .env 文件中的值优先于系统环境变量
    # 这对于 Windows 系统中的 USERNAME 变量特别重要
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=True)
        print(f"  📄 从项目根目录加载 .env 文件: {env_file}")
    else:
        # 如果项目根目录没有 .env，尝试当前目录
        load_dotenv(override=True)
        print(f"  ⚠️  项目根目录未找到 .env 文件，尝试从当前目录加载")
    
    # 优先从 .env 文件读取，如果不存在则使用空字符串
    # 注意：Windows 系统中 USERNAME 是系统环境变量，所以必须使用 override=True
    username_from_env = os.getenv("USERNAME", "")
    
    # 调试：检查是否从 .env 文件读取到了 USERNAME
    # 如果 username_from_env 是系统用户名（通常是短名称），可能是系统环境变量
    # 如果 .env 文件中设置了 USERNAME，应该会覆盖系统值
    
    config = {
        'BASE_URL': os.getenv("FLOW_IMPORT_BASE_URL", "https://saibotan-pre5.100credit.cn/openapi/v1/chatflow"),
        'ROBOT_KEY': os.getenv("ROBOT_KEY", "").strip(),  # 去除前后空格
        'ROBOT_TOKEN': os.getenv("ROBOT_TOKEN", "").strip(),  # 去除前后空格
        'USERNAME': username_from_env.strip() if username_from_env else "",  # 去除前后空格
        'HOMELAND_ID': os.getenv("FLOW_IMPORT_HOMELAND_ID", "221").strip()
    }
    
    # 检查必要的配置
    missing_configs = []
    if not config['ROBOT_KEY']:
        missing_configs.append('ROBOT_KEY')
    if not config['ROBOT_TOKEN']:
        missing_configs.append('ROBOT_TOKEN')
    if not config['USERNAME']:
        missing_configs.append('USERNAME')
    
    if missing_configs:
        print(f"  ⚠️  缺少配置项: {', '.join(missing_configs)}")
        print("     请在 .env 文件中配置这些变量")
        # 如果 USERNAME 存在但可能是系统值，给出提示
        if config['USERNAME'] and '@' not in config['USERNAME']:
            print(f"  ⚠️  警告: USERNAME 值 '{config['USERNAME']}' 看起来像是系统用户名")
            print("     请确保 .env 文件中设置了正确的 USERNAME（通常是邮箱地址）")
    else:
        print("  ✅ 配置加载成功")
        # 如果 USERNAME 看起来不像邮箱，给出提示
        if '@' not in config['USERNAME']:
            print(f"  ⚠️  提示: USERNAME 值 '{config['USERNAME']}' 不包含 '@'，请确认是否正确")
    
    return config


def import_flow(file_path: str, config: Dict[str, str], dry_run: bool = False) -> bool:
    """
    导入 Flow 到 Dyna.ai 平台
    
    Args:
        file_path: Workflow JSON 文件路径
        config: 配置字典
        dry_run: 如果为 True，只检查不上传
        
    Returns:
        是否导入成功
    """
    if not os.path.exists(file_path):
        print(f"  ❌ 文件不存在: {file_path}")
        return False
    
    # 检查 API 配置
    if not config.get('ROBOT_KEY') or not config.get('ROBOT_TOKEN') or not config.get('USERNAME'):
        print("  ❌ API 配置不完整，请检查 ROBOT_KEY, ROBOT_TOKEN, USERNAME")
        return False
    else:
        print("  ✅ API 配置完整")
        print(f"  ✅ API 配置: \n \
              Key: {config['ROBOT_KEY']}, \n \
              Token: {config['ROBOT_TOKEN']}, \n \
              Username: {config['USERNAME']}")
    
    # 验证 JSON 文件格式
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json.load(f)
        print(f"  ✅ JSON 文件格式验证通过")
    except json.JSONDecodeError as e:
        print(f"  ❌ JSON 文件格式错误: {e}")
        return False
    
    if dry_run:
        print(f"  🔍 [检查模式] 将导入文件: {file_path}")
        print(f"     文件大小: {os.path.getsize(file_path) / 1024:.2f} KB")
        print(f"     家园ID: {config['HOMELAND_ID']}")
        print(f"     用户名: {config['USERNAME']}")
        return True
    
    # ========================================
    # curl 参数对应 requests 字段说明：
    # --location 'URL'        → url (第一个位置参数)
    # --header 'Key: Value'   → headers 参数
    # --form 'key=value'       → data 参数（普通表单字段）
    # --form 'file=@path'     → files 参数（文件上传）
    # ========================================
    
    # 1. URL (对应 curl 的 --location)
    # BASE_URL 应该是完整的 URL（用户已在 .env 中配置完整路径）
    url = config['BASE_URL']
    
    # 2. Headers (对应 curl 的 --header)
    # 根据 curl 示例，使用首字母大写的格式
    headers = {
        "Cybertron-Robot-Key": config['ROBOT_KEY'],
        "Cybertron-Robot-Token": config['ROBOT_TOKEN']
    }
    
    # 3. Form Data (对应 curl 的 --form，普通表单字段)
    # username 在 form data 中，不在 headers 中
    data = {
        "homeland_id": config['HOMELAND_ID'],
        "username": config['USERNAME'],
        "_import_kb": "false"
    }
    
    # 4. Files (对应 curl 的 --form 'file=@path')
    # 文件上传使用 files 参数
    
    print(f"\n  📤 导入文件: {os.path.basename(file_path)}")
    print(f"     目标URL: {url}")
    print(f"     请求头:")
    print(f"       Cybertron-Robot-Key: {config['ROBOT_KEY']}")
    print(f"       Cybertron-Robot-Token: {config['ROBOT_TOKEN'][:20]}...")  # 只显示前20个字符
    print(f"     表单数据:")
    print(f"       homeland_id: {config['HOMELAND_ID']}")
    print(f"       username: {config['USERNAME']}")
    
    try:
        # 从 .env 读取超时时间
        from dotenv import load_dotenv
        load_dotenv()
        upload_timeout = int(os.getenv("FLOW_IMPORT_TIMEOUT", "120"))
        
        # 打开文件并上传
        # with open(file_path, 'rb') as f:
        files = {'file': open(file_path, 'rb')}
        
        # requests.post() 参数对应关系：
        # url      → curl 的 --location
        # headers  → curl 的 --header
        # data     → curl 的 --form (普通字段)
        # files    → curl 的 --form (文件字段)
        response = requests.post(
            url,              # --location
            headers=headers,   # --header
            data=data,         # --form (普通字段)
            files=files,       # --form (文件字段)
            timeout=upload_timeout
        )
        
        # 处理响应
        print(f"\n  📥 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            print("  ✅ 导入成功！")
            try:
                response_json = response.json()
                print(f"     响应内容: {json.dumps(response_json, ensure_ascii=False, indent=2)}")
            except:
                print(f"     响应内容: {response.text}")
            return True
        else:
            print(f"  ❌ 导入失败")
            print(f"     错误信息: {response.text}")
            
            # 尝试解析错误响应
            try:
                error_json = response.json()
                error_code = error_json.get("code", "")
                error_message = error_json.get("message", "")
                
                if error_code == "400000" and "Robot does not exist" in error_message:
                    print(f"\n  💡 可能的原因:")
                    print(f"     1. ROBOT_KEY 或 ROBOT_TOKEN 不正确")
                    print(f"     2. ROBOT_KEY 或 ROBOT_TOKEN 值前后可能有空格")
                    print(f"     3. 请确认从平台复制的值是否完整")
                    print(f"     4. 请检查 .env 文件中的配置是否正确")
            except:
                pass
            
            return False
            
    except requests.exceptions.Timeout:
        print(f"  ❌ 请求超时（{upload_timeout}秒）")
        return False
    except requests.exceptions.RequestException as e:
        print(f"  ❌ 请求失败: {e}")
        return False
    except Exception as e:
        print(f"  ❌ 发生错误: {e}")
        return False


def main():
    """主函数"""
    print("="*80)
    print("🚀 Flow 导入工具")
    print("="*80)
    
    # 加载配置
    config = load_config()
    
    # 检查命令行参数
    if len(sys.argv) < 2:
        print("\n使用方法:")
        print("  python flow_import.py <workflow_json_file> [--dry-run]")
        print("\n示例:")
        print("  python flow_import.py ../output/step7_final/en/generated_workflow_transactionservicing_downloadestatement.json")
        print("  python flow_import.py ../output/step7_final/en/generated_workflow_transactionservicing_downloadestatement.json --dry-run")
        sys.exit(1)
    
    file_path = sys.argv[1]
    dry_run = '--dry-run' in sys.argv or '-d' in sys.argv
    
    # 转换为绝对路径
    original_path = file_path
    if not os.path.isabs(file_path):
        # 首先尝试相对于当前工作目录
        if os.path.exists(file_path):
            file_path = os.path.abspath(file_path)
            print(f"  📍 使用当前工作目录下的文件: {file_path}")
        else:
            # 如果不存在，尝试相对于项目根目录
            script_dir = Path(__file__).parent
            project_root = script_dir.parent.parent  # 从 导入工具/flow导入 向上两级到项目根目录
            project_path = os.path.join(project_root, file_path)
            if os.path.exists(project_path):
                file_path = os.path.abspath(project_path)
                print(f"  📍 使用项目根目录下的文件: {file_path}")
            else:
                # 最后尝试相对于脚本目录
                script_path = os.path.join(script_dir, file_path)
                if os.path.exists(script_path):
                    file_path = os.path.abspath(script_path)
                    print(f"  📍 使用脚本目录下的文件: {file_path}")
                else:
                    # 如果都不存在，使用项目根目录的路径（让后续的错误处理显示）
                    file_path = os.path.abspath(project_path)
                    print(f"  ⚠️  尝试的路径: {file_path}")
    else:
        print(f"  📍 使用绝对路径: {file_path}")
    
    # 执行导入
    success = import_flow(file_path, config, dry_run=dry_run)
    
    if success:
        print("\n" + "="*80)
        print("✅ 操作完成")
        print("="*80)
        sys.exit(0)
    else:
        print("\n" + "="*80)
        print("❌ 操作失败")
        print("="*80)
        sys.exit(1)


if __name__ == "__main__":
    main()


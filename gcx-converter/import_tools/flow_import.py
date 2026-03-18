import requests

url = "https://saibotan-pre5.100credit.cn/openapi/v1/chatflow/import/"

headers = {
    "Cybertron-Robot-Key": rf"71v6anWHwMZ1RhTCZ7PMyA%2FzEKk%3D",
    "Cybertron-Robot-Token": rf"MTc2MjkyOTQ0MTk3Ngpqa0t3a1VQV3Vmd1pnS3FFdlE3VWNEZGZHOFU9"
}

data = {
    "homeland_id": "7003",
    "username": "chenyu.zhu@brgroup.com",
    "_import_kb": "true",
}

files = {
    "file": open(rf"C:\Users\zhefei.lv\Desktop\谷歌flow迁移调研\code_git\googlecx-migrationtool\output\step7_final\en\generated_workflow_transactionservicing_downloadestatement.json", "rb"),
}

resp = requests.request("POST", url, headers=headers, files=files, data=data)
print("状态码:", resp.status_code)
print("响应:", resp.text)


# curl --location 'https://saibotan-pre5.100credit.cn/openapi/v1/chatflow/import/' \
# --header 'cybertron-robot-key: 71v6anWHwMZ1RhTCZ7PMyA%2FzEKk%3D' \
# --header 'cybertron-robot-token: MTc2MjkyOTQ0MTk3Ngpqa0t3a1VQV3Vmd1pnS3FFdlE3VWNEZGZHOFU9' \
# --form 'homeland_id="221"' \
# --form 'file=@"C:\Users\zhefei.lv\Desktop\谷歌flow迁移调研\code_git\googlecx-migrationtool\output\step7_final\en\generated_workflow_transactionservicing_downloadestatement.json"' \
# --form 'username="zhefei.lv@brgroup.com"' \
# --form '_import_kb="true"'
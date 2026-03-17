# 上传到 Git 远程仓库的步骤

本地仓库已初始化并完成首次提交。要推送到 GitHub / Gitee / GitLab，请按以下步骤操作。

## 您需要准备的信息

1. **Git 托管平台账号**（任选其一）  
   - [GitHub](https://github.com)  
   - [Gitee 码云](https://gitee.com)  
   - [GitLab](https://gitlab.com)

2. **远程仓库地址**  
   在平台上新建一个**空仓库**（不要勾选「使用 README 初始化」），复制仓库的 URL，例如：  
   - HTTPS: `https://github.com/你的用户名/project_demo.git`  
   - SSH: `git@github.com:你的用户名/project_demo.git`

## 操作步骤

### 1. 在平台上新建仓库

- 登录 GitHub / Gitee / GitLab  
- 点击「New repository」/「新建仓库」  
- 仓库名可填：`project_demo`（或任意名称）  
- **不要**勾选「Add a README file」或「使用 README 初始化仓库」  
- 创建后复制仓库的 HTTPS 或 SSH 地址

### 2. 在本地添加远程并推送

在项目根目录 `d:\Emma\project_demo` 下打开终端，执行（把下面的 URL 换成你的仓库地址）：

```powershell
# 添加远程仓库（请替换为你的实际 URL）
git remote add origin https://github.com/你的用户名/project_demo.git

# 推送到远程（当前分支为 master，若远程默认是 main 可改为 git push -u origin master:main）
git push -u origin master
```

若使用 **Gitee**：

```powershell
git remote add origin https://gitee.com/你的用户名/project_demo.git
git push -u origin master
```

### 3. 认证方式

- **HTTPS**：首次 push 时会提示输入用户名和密码。  
  - GitHub 已不再支持账号密码，需使用 **Personal Access Token (PAT)** 作为密码。  
  - 在 GitHub → Settings → Developer settings → Personal access tokens 中生成 Token。
- **SSH**：若已配置 SSH 公钥，可使用 `git@github.com:用户名/project_demo.git` 形式，无需每次输入密码。

## 常用命令

```powershell
# 查看远程仓库
git remote -v

# 之后日常提交并推送
git add -A
git commit -m "描述本次修改"
git push
```

---

*本地已完成：`git init`、首次 `git add`、首次 `git commit`。*

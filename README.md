# Operations-Workflow

面向本地部署的 AI 内容运营工作台，使用 FastAPI、Vue 3、PostgreSQL、pgvector 和 LangGraph。

当前支持小红书：产品资料检索、案例采集与分析、文案与图片生成、规则风险提示、人工审核、Chrome 辅助发布，以及发布后的数据反馈与经验检索。其他平台保留适配入口，尚未接入完整流程。

本文从空白环境开始说明安装与启动。所有命令均以仓库根目录为起点，示例账号、密码和路径需要替换，不依赖开发机器上的盘符。

## 1. 部署范围与环境

当前主要验证环境为 Windows、本机 Chrome 和 Python 3.13。macOS、Linux 可按下列步骤准备后端和前端，但 Chrome 页面自动化需要在目标系统上另行验证。当前说明是本机部署方案，不是公网多租户部署方案。

| 组件 | 要求或用途 |
| --- | --- |
| Python | 建议使用已验证的 3.13；其他版本需验证锁定依赖是否支持 |
| Node.js | 建议 24.15 或更高的 24.x；项目声明至少 22.22.2 |
| npm | 项目声明版本为 12.2.0，前端依赖通过 `npm ci` 安装 |
| PostgreSQL | 必须能安装并启用与服务器版本匹配的 pgvector 扩展 |
| Google Chrome | 采集、自动发布和浏览器数据采集需要，本机交互登录 |
| 模型 API | 文本、Embedding、生图分别配置，按使用功能准备 |
| Git | 用于克隆与后续更新；也可从 GitHub 下载 ZIP 并解压 |

不要求安装 Redis。连接现有 Chrome 时，无需执行 `playwright install` 下载另一份浏览器；Python 的 Playwright 包已包含在依赖中。

完整流程使用两个独立 PostgreSQL 数据库：业务库与经验库。它们可以位于同一 PostgreSQL 实例中。

## 2. 获取代码与安装依赖

从本仓库 GitHub 页面复制 HTTPS 克隆地址，将下方占位地址替换为实际地址：

```sh
git clone <本仓库的HTTPS地址> Operations-Workflow
cd Operations-Workflow
```

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

如果系统没有 `py` 命令，确认 `python --version` 后使用 `python -m venv .venv`。若 PowerShell 禁止激活脚本，可直接用 `.\.venv\Scripts\python.exe` 替代后续命令中的 `python`，无需修改系统执行策略。

macOS / Linux：

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

前端依赖在单独终端安装：

```sh
cd frontend
npm ci
```

`npm ci` 使用仓库中的锁文件，不需要自行替换依赖版本。

## 3. 准备 PostgreSQL 与 pgvector

安装并启动 PostgreSQL，准备数据库账号。新部署建议使用独立项目账号，不与其他应用共用数据库。

pgvector 有两部分：

- `pip install -r requirements.txt` 安装 Python 客户端。
- PostgreSQL 服务器还必须安装 pgvector 扩展文件；仅安装 Python 包无法提供 `vector` 数据类型。

服务器扩展的安装方式取决于操作系统、PostgreSQL 主版本及安装渠道，请按 [pgvector 官方安装说明](https://github.com/pgvector/pgvector#installation) 安装到实际运行的 PostgreSQL 实例。使用托管 PostgreSQL 时，需要确认服务商支持此扩展。

使用 pgAdmin 查询工具或 `psql`，以有建库权限的账号执行以下 SQL。`CREATE DATABASE` 需要单独执行，不能放在事务块内：

```sql
CREATE DATABASE "Operations";
CREATE DATABASE "OperationsMemory";
```

切换连接到 **Operations** 业务库后执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

查询应返回扩展版本。若使用独立应用账号，建库时指定该账号为数据库所有者，或由管理员授予表、序列及 schema 初始化所需权限。创建扩展可能需要管理员权限，可由管理员提前完成。

## 4. 配置后端 `.env`

编辑项目根目录 `.env`，至少完成：

```dotenv
DATABASE_URL=postgresql+psycopg://数据库账号:URL编码后的密码@127.0.0.1:5432/Operations
MEMORY_DATABASE_URL=postgresql+psycopg://数据库账号:URL编码后的密码@127.0.0.1:5432/OperationsMemory
JWT_SECRET_KEY=替换为随机生成的密钥
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
UPLOAD_DIR=storage/uploads
CHROME_CDP_URL=http://127.0.0.1:9222
```

这些是格式示例，不可原样使用。密码包含 `@`、`:`、`/`、`#`、`%` 等字符时，需要对连接 URL 中的密码做百分号编码。数据库名称区分实际建库名称，示例使用双引号保留大写。

生成 JWT 密钥：

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

将输出复制到 `JWT_SECRET_KEY`，至少 32 字节。不要将示例文字作为密钥，不要提交到 Git。更换 JWT 密钥会导致现有登录令牌失效。

配置从项目文件所在目录加载，不依赖终端启动目录；系统环境变量优先于 `.env`。修改 `.env` 后需要重启后端。

### 模型连接：优先按用户配置

注册登录后，在前端「设置 → 模型连接」分别添加文本、Embedding、生图连接，并设置各类型的默认连接：

| 类型 | 接口与配置 |
| --- | --- |
| 文本 | Chat Completions 兼容接口：Base URL、API Key、模型 ID |
| Embedding | Embeddings 兼容接口：Base URL、API Key、模型 ID、向量维度 |
| 生图 | 同步 Images 兼容接口或已适配的千问原生协议，填写模型和支持尺寸 |

供应商名称可以自定义，实际接口必须符合支持的协议。当前自定义接口要求公网 HTTPS，不支持直接填写本机 HTTP 模型地址；任意供应商的私有协议、异步生图任务不自动兼容。

API URL 填接口基础地址，不追加 `/chat/completions`、`/embeddings` 或 `/images/generations`。千问原生生图按该协议填写服务基础地址。密钥必须与服务区域、账号及接口权限相匹配。

用户连接按登录账号隔离，密钥加密保存在数据库，不由前端写入共享 `.env`。保存配置不等于已验证远程调用成功；连接测试及实际生成可能产生 API 费用。

未配置用户默认连接时，系统兼容 `.env.example` 中的 `DEEPSEEK_*`、`EMBEDDING_*`、`QWEN_IMAGE_*` 等部署端默认配置。它们是回退连接，不要求所有使用者都使用相同供应商。完整说明见 [模型连接](docs/model_connections.md)。

**Embedding 入库与检索必须使用一致的连接、模型和维度。** 更换后，应在产品资料页面重新入库，不能直接使用另一套模型生成的历史向量。

### 模型凭据加密密钥

`MODEL_CREDENTIALS_KEY` 留空时，首次保存模型凭据会自动创建：

```text
storage/secrets/model-credentials.key
```

也可生成一个 Fernet 密钥，安全填写到 `.env` 的 `MODEL_CREDENTIALS_KEY`：

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

一旦开始保存凭据，应保留原密钥。数据库备份必须与此密钥一起安全备份，否则恢复后的模型 API Key 无法解密。多实例需要使用相同部署密钥。

## 5. 初始化全部数据库结构

回到项目根目录，使用已安装依赖的 Python，依次执行：

```sh
python -m alembic upgrade head
python -m alembic -c memory/alembic.ini upgrade head
python -m workflows.setup
```

三条命令分别初始化：业务表、经验库表、业务库 `workflow_checkpoints` schema 下的 LangGraph 持久化表。

经验库尚未创建且数据库账号有建库权限时，也可以用 `python -m memory.setup` 替代第二条；该命令会尝试创建配置指定的经验库，再执行经验库迁移。没有建库权限时，应由管理员先建库，再使用上面的标准迁移命令。

首次部署不需要运行 `alembic revision --autogenerate`，也不要用 `stamp head` 代替创建表。

## 6. 启动后端和前端

Windows 完成首次安装、配置和数据库初始化后，可以直接双击根目录脚本，无需打开编程软件：

| 脚本 | 功能 |
| --- | --- |
| `start_all.cmd` | 同时启动前后端，准备就绪后打开前端网址 |
| `start_backend.cmd` | 单独启动后端，优先使用项目 `.venv` 或 `venv` 中的 Python，否则使用 PATH 中的 Python |
| `start_frontend.cmd` | 单独启动前端，使用已安装的 Node.js 和前端依赖 |
| `start_chrome.cmd` | 需要采集或发布时，另行打开专用 Chrome |

启动后保留前后端服务窗口。按 `Ctrl+C` 或关闭对应窗口停止服务。一键启动入口完成后会退出，服务窗口继续运行。重复启动时，已识别的服务会复用；端口被其他程序占用时会报错，不会强制关闭其他程序。

脚本不负责安装软件、启动 PostgreSQL 服务或自动执行数据库迁移。缺少依赖或配置时，会保留错误信息；按本文先完成首次部署。单独检查环境可以在根目录执行 `start_all.cmd -CheckOnly`。指定安装路径可使用：

```powershell
.\start_backend.cmd -PythonPath "D:\Python\python.exe"
.\start_frontend.cmd -NodePath "D:\Node\node.exe"
```

macOS / Linux，或需要手动运行时，使用以下命令。

终端 A：项目根目录，激活 Python 虚拟环境后运行：

```sh
python run_local.py
```

终端 B：

```sh
cd frontend
npm run dev
```

| 地址 | 用途 |
| --- | --- |
| http://127.0.0.1:5173 | 前端工作台 |
| http://127.0.0.1:8000/docs | 后端 API 文档 |
| http://127.0.0.1:8000/health | 后端进程健康检查 |
| http://127.0.0.1:8000/health/db | 业务数据库连接检查 |

后端和前端终端需要保持运行，按 `Ctrl+C` 停止。电脑重启后，重新启动 PostgreSQL、后端和前端；数据库迁移无需每次重做。

默认无需额外前端环境文件。修改后端地址时，将 `frontend/.env.example` 复制为 `frontend/.env.local`，设置 `VITE_API_BASE_URL` 后重启 Vite。改变前端地址或端口，还需同步修改后端 `CORS_ORIGINS`。`VITE_*` 会进入浏览器，不能放 API Key 或数据库密码。

## 7. 启动专用 Chrome

仅使用产品管理和模型 API 时可以暂不启动 Chrome。采集、浏览器自动发布或浏览器指标读取时再启动。

Windows 可双击根目录 `start_chrome.cmd`，或在 PowerShell 执行：

```powershell
.\start_chrome.cmd
```

自定义 Chrome 安装位置：

```powershell
.\start_chrome.cmd -ChromePath "D:\Applications\Chrome\chrome.exe"
```

脚本使用 `storage/chrome-profile/` 作为独立用户目录，保留本机登录状态。首次使用，在新开的 Chrome 中登录小红书；发布还需要登录创作服务平台并确认实际账号。

macOS 示例，在项目根目录执行：

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="$PWD/storage/chrome-profile"
```

Linux 示例，Chrome 命令名按实际安装调整：

```sh
google-chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="$PWD/storage/chrome-profile"
```

访问 http://127.0.0.1:9222/json/version 检查，应返回包含 `webSocketDebuggerUrl` 的 JSON。普通 Chrome 窗口即使已登录，也不代表已开启远程调试。

本机采集和发布接口要求本机直连、JWT 和允许的来源。CDP 端口不要开放到公网，不要提交 Chrome 用户目录或共享登录数据。当前方案不支持其他电脑打开网页后直接控制其本机 Chrome，也不提供每个项目用户独立的浏览器容器。

## 8. 首次使用验证

1. 注册并登录项目账号；项目账号与小红书账号是两种身份。
2. 添加并测试模型连接，设置文本、Embedding 和生图默认连接；仅使用文案时可暂不配置生图。
3. 创建产品，上传 PDF、TXT、MD 或 DOCX 资料，再执行资料入库。扫描 PDF 需要先做 OCR，当前解析不自动执行 OCR。
4. 在案例库添加或采集案例，按需分析并选择分析版本；也可维护自己的 Prompt 与规则。
5. 选择产品、创作要求及素材类型，启动生成，检查文案和图片是否已保存。
6. 在审核页人工检查。修改内容后保存新版本，再提交审核，确认通过后才能发布。
7. 自动发布前确认实际登录账号和已审核版本；也可自行发布后登记作品链接。
8. 发布后采集或补录真实指标，保留观察时间与来源，再提炼经验供下次生成检索。

规则检查只提供风险提示，不能保证平台审核通过。案例参考及生成结果不保证成为爆款，文案、图片与产品事实仍需人工核实。经验库属于检索增强，不直接训练或修改模型参数。

## 9. 数据位置、备份与更新

| 数据 | 默认位置 |
| --- | --- |
| 业务、用户、内容与向量 | `DATABASE_URL` 指定的 PostgreSQL 数据库 |
| 真实指标与运营经验 | `MEMORY_DATABASE_URL` 指定的独立数据库 |
| 工作流检查点 | 业务库的 `workflow_checkpoints` schema |
| 上传资料 | `storage/uploads/`，可用 `UPLOAD_DIR` 改为其他目录 |
| 生成图片 | `storage/generated_images/` |
| Chrome 登录与缓存 | `storage/chrome-profile/` |
| 自动创建的凭据加密密钥 | `storage/secrets/model-credentials.key` |

数据库物理文件位置由 PostgreSQL 自身配置决定，不在项目目录内。仅备份代码无法恢复业务数据；应同时备份两个数据库、上传资料、生成图片及凭据加密密钥。敏感备份不要上传到公开仓库。

更新代码前先备份并停止后端、前端，在根目录执行：

```sh
git pull
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m alembic -c memory/alembic.ini upgrade head
python -m workflows.setup
```

然后在 `frontend` 目录执行 `npm ci`，重新启动前后端。存在本地代码修改时，先处理 Git 冲突，不要直接覆盖。

`.gitignore` 已排除 `.env`、`storage/`、日志、虚拟环境、前端依赖及构建输出。该规则不能撤回已经提交过的秘密；公开仓库前仍需检查待提交文件与历史记录。

## 10. 常见问题

| 问题 | 排查方式 |
| --- | --- |
| 缺少 `DATABASE_URL` | 检查根目录 `.env` 是否由示例复制，连接串是否填写 |
| 数据库连接失败 | 检查 PostgreSQL 服务、端口、账号、密码、数据库名称及连接权限 |
| `vector` 扩展不存在 | 为实际 PostgreSQL 实例安装服务器扩展；Python 包不能替代扩展 |
| 找不到 Alembic revision | 确认连接到本项目数据库、迁移文件完整；不要随意删版本表或 `stamp` |
| 工作流存储不可用 | 在根目录执行 `python -m workflows.setup`，确认 schema 权限 |
| 经验库表不存在 | 检查独立经验库地址，并执行其专用 Alembic 迁移 |
| 前端打不开 | 检查 `npm run dev` 终端；端口 5173 被占用时 Vite 不会自动换端口 |
| 前端请求失败或 CORS 报错 | 先检查后端健康地址，再对照前端 API 地址与 `CORS_ORIGINS` |
| Chrome 连接失败 | 使用专用脚本启动，检查 9222 的 `/json/version` 和 `.env` 中的端口 |
| Embedding 连接与向量不一致 | 确认当前默认连接正确，对产品资料重新入库 |
| 模型超时或 API 错误 | 核对 API Key、模型 ID、区域、额度、网络及接口协议；不要用聊天模型替代 Embedding 模型 |
| 更换电脑后模型 Key 无法解密 | 恢复原 `MODEL_CREDENTIALS_KEY` 或原密钥文件；缺失时重新填写各模型凭据 |
| 发布超时但网页可能已发布 | 先核对实际作品和发布记录，避免直接重试造成重复发布 |

## 11. 开发与模块说明

前端检查：在 `frontend` 目录执行 `npm test`、`npm run build`。后端普通测试可在根目录执行 `python -m unittest discover -s tests`；数据库与浏览器集成测试另有环境要求，不能用普通单元测试通过替代真实平台验证。

当前默认启动的是本地开发服务，`npm run build` 只产生前端静态文件，并不会自动部署公网服务。公网部署、反向代理、HTTPS、多机浏览器隔离与运维配置需要单独设计。

- [前端开发与 UI 替换](frontend/README.md)
- [模型连接](docs/model_connections.md)
- [产品 RAG](docs/rag.md)
- [Chrome 与案例采集](docs/collectors.md)
- [内容生成](docs/content_generation.md)
- [图片生成](docs/image_generation.md)
- [LangGraph 工作流](docs/workflows.md)
- [审核与发布](docs/publishing.md)
- [运营反馈与经验库](docs/memory.md)

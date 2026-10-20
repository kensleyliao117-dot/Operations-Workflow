# 小红书本地采集与案例分析

本地采集通过 CDP 连接已启动的 Chrome，读取单篇帖子并保存到 JWT 用户的案例库。
项目用户可以共用同一 Chrome 采集会话，不自动切换小红书账号。

## 启动

在项目根目录运行本地后端：

```powershell
python run_local.py
```

该入口绑定 `127.0.0.1:8000`，并关闭代理头信任。
也可以执行 `python -m uvicorn app:app --host 127.0.0.1 --port 8000 --no-proxy-headers`。
网络设置参见 [Uvicorn 文档](https://uvicorn.dev/settings/)。

开启采集专用 Chrome：双击项目根目录的 `start_chrome.cmd`，或在终端运行：

```powershell
.\start_chrome.cmd
```

在该窗口手工登录小红书。独立用户目录保留登录状态、缓存和历史记录，属于本机运行数据。
脚本自动查找 Chrome，读取 `CHROME_CDP_URL` 的本机端口；目录基于脚本所在位置，不依赖终端工作目录。双击后显示启动进度，并打开专用 Chrome 新窗口；已有调试会话时复用同一用户目录和登录状态。自定义安装位置可运行 `start_chrome.cmd -ChromePath "D:\Apps\Chrome\chrome.exe"`；仅检查连接使用 `start_chrome.cmd -CheckOnly`，不会打开窗口。启动后最多等待约 20 秒检查调试连接，失败时保留错误提示。脚本不安装程序，不修改系统执行策略，不强行关闭已有 Chrome；PowerShell 启动参数只对该次脚本进程生效。
`storage/` 已被 `.gitignore` 忽略，不能手工上传或强制添加浏览器目录。
Chrome 的独立目录要求见 [官方说明](https://developer.chrome.com/blog/remote-debugging-port)。

```dotenv
CHROME_CDP_URL=http://127.0.0.1:9222
CHROME_CDP_TIMEOUT_MS=10000
CHROME_PAGE_TIMEOUT_MS=30000
```

采集代码不启动浏览器、不导出 Cookie，不需要另行下载 Playwright 浏览器。
每次请求在同一线程中创建并清理连接，仅关闭本次创建的标签页。

## 采集并自动保存

`POST /rednote/collect`，请求头提供 `Authorization: Bearer <登录 token>`。

```json
{
  "url": "https://www.xiaohongshu.com/explore/帖子ID?原有链接参数",
  "analyze": true
}
```

不提供 `url` 时读取调试 Chrome 中已打开的一篇详情页；请求体 `{}` 表示只采集并保存。
指定链接时保留原参数用于打开页面，入库链接去掉分享参数。
多个详情页同时打开时需要指定链接，不自动选择帖子。
响应还包含 `case_already_saved`、`analysis_count`、`latest_analysis_id`、`already_analyzed` 和 `history_hint`。
案例已有分析时，`analyze=true` 默认复用最近版本，返回 `analysis_status="existing"`；
需要重新调用模型时显式传入 `reanalyze=true`，可通过 `model_id` 选择服务器登记的模型。
`analysis_source_changed` 提示历史来源是否无法确认一致，不会自动刷新历史分析。

只采集时的响应示例（HTTP 201）：

```json
{
  "case": {"id": 1, "name": "帖子标题", "body": "原始正文", "link": "规范化帖子链接",
           "image_urls": ["https://图片地址"], "metrics": {"likes": 12000, "favorites": 0, "comments": null},
           "metrics_raw": {"likes": "1.2万", "favorites": "0", "comments": null},
           "author_platform_id": "页面公开作者ID", "collected_at": "采集时间"},
  "analysis": null,
  "analysis_status": "not_requested",
  "analysis_error": null
}
```

字段说明：

- `name`、`body`、`link` 保存标题、原文和帖子链接。
- `image_urls` 保存详情页 DOM 中读取到的图片链接，不下载图片文件；懒加载未提供的图片可能不在结果中。
- `metrics` 保存公开点赞、收藏和评论显示值的数值解析结果，读取不到时为 `null`，不推断为零。
- 互动指标仅从可见的帖子互动栏读取，排除评论区的点赞按钮；详情页缺少明确计数时，仅使用同一笔记对应的页面状态补充，不把评论点赞混入帖子指标。
- `metrics_raw` 保留原始显示文本。`1.2万`、`1.2万+` 等属于近似显示，解析结果不代表精确后台统计。
- `author_platform_id` 为公开作者标识，不代表采集浏览器的登录账号，也不绑定为发布账号。
- `collected_at` 为 UTC 采集时间，当前数据库按无时区 UTC 保存。

标题和正文保留首次采集快照；同一用户重复采集相同规范化链接会复用案例，更新图片、指标和作者标识。
旧案例缺失正文时补充正文。不同项目用户可分别保存相同帖子。
未采集到有效正文或标题、浏览器未开启、页面跳转或加载失败时，不创建新案例。
平台页面结构变化需要更新选择器；登录和验证提示由浏览器中手工处理。

查询使用 `GET /rednote/cases`、`GET /rednote/cases/{id}`，均按 JWT 用户隔离。
手工创建接口 `POST /rednote/cases` 仍保留，手工创建重复链接返回 409。
修改时省略可选字段保留旧值，显式传 `body: null` 清除原文。

## AI 爆款模式分析

`POST /rednote/cases/{id}/analyze` 对已保存的当前用户案例调用指定模型，保存新的分析版本，默认参考最近版本。
也可在采集请求中提供 `analyze: true`，完成采集后立即分析。

默认分析使用当前账号的默认文本模型，也可通过 `model_id` 指定所属模型配置。未设置用户默认模型时兼容服务端系统模型。用户可填写供应商名称、URL、API Key 和模型 ID，详见 [模型连接](model_connections.md)。聊天模型不复用 Embedding 模型配置。
输出包括内容结构、Hook、情绪点、痛点、CTA、图片与视频信息限制、话题、可复用模式及指标观察。
结果经过严格 JSON 和字段校验后保存到 `rednote_analysis.result`；`content` 保留对应 JSON 文本。
每条分析保存版本号、父版本、反馈 ID、生成模型、来源快照指纹和创建时间；手工分析记录的结构化结果允许为空。
搜索历史提示、用户反馈、重新分析和模型版本对比见 [分析版本与用户反馈](analysis_versions.md)。

模型只使用文本和图片链接元数据，不执行图片或视频识别。资料不足时必须明确无法判断，
不能猜测图片风格、视频内容、产品功效、转化或指标因果关系，也不能仅凭互动显示值确认爆款。
帖子内容作为待分析数据，不允许覆盖系统指令。

远程模型调用前释放查询事务；保存前重新校验案例归属和来源指纹，拒绝保存过期分析。
单独分析失败返回相应错误，不保存分析记录。
采集并分析时，分析失败仍保留已入库案例，返回 `analysis_status="failed"` 和安全的 `analysis_error`；
修复原因后可通过单独分析接口重试，避免重新采集。
分析历史通过 `GET /rednote/analyses?cases_id=1` 查询。

## 将分析用于文案生成

选择产品并创建任务后，调用已有的 `POST /tasks/{task_id}/generate`：

```json
{"prompt_id": 1, "analysis_ids": [2, 3]}
```

`analysis_ids` 填写已保存的分析 ID，最多 5 个且不能重复；分析须属于当前 JWT 用户，
并具有有效的结构化结果。案例原文和产品总结不传给生成模型，只提取结构、Hook、情绪点、
CTA 和可复用模式。案例参考与人工 Prompt、RAG 产品资料分开传入，产品事实只以 RAG 资料为准。
没有检索到产品资料时停止生成，不能把案例当作产品资料。未选案例时省略 `analysis_ids`。

此步骤复用数据库中的分析，不再连接 Chrome；成功后保存 Content 新版本并进入人工审核，
不会自动发布。接口参数、返回值与失败处理见 [内容生成服务](content_generation.md)。

## 本机访问与用户权限

采集和自动分析接口同时要求：

- 有效且未退出的 JWT 登录会话，客户端不能指定归属 `user_id`。
- 实际客户端地址为回环地址，Host 为本机地址。
- 不携带 `Forwarded`、`X-Real-IP` 或 `X-Forwarded-*` 代理头。
- 浏览器请求的 Origin 同时属于本机地址和配置允许的前端来源。

判断客户端地址的接口见 [Starlette Request 文档](https://www.starlette.io/requests/)。
即使携带有效 JWT，远程来源也无法调用这两个本机入口。CORS 本身不能替代上述访问检查。
本地接口不能配置公网反向代理或端口隧道；对外部署时应移除本地路由或在网关明确拒绝 `/rednote/collect`
、`/rednote/cases/*/analyze` 和 `/rednote/analyses/*/revise`，不能由代理去掉来源头后转为回环请求。
目前仅授权本机可信项目用户共用采集会话，不实现自动小红书账号切换。

## 发布账号与运营反馈

采集会话与发布账号分别管理。采集不要求项目用户登记发布账号。
发布需要先通过 JWT 接口登记实际平台账号：

```http
POST /platform-accounts
```

```json
{"platform": "rednote", "platform_user_id": "实际小红书账号ID", "display_name": "账号备注"}
```

`platform_user_id` 必须填写真实平台账号标识，不使用昵称、项目用户 ID 或浏览器目录名代替。
当前为人工登记，响应标记 `registration_method="manual"`；尚未接入 OAuth 或远程账号所有权验证。
账号标识没有修改接口，避免历史发布和反馈随账号资料修改而重新归属。
同一项目用户的同一平台账号只需登记一次；重复登记会提示直接选择已有账号。

自动发布打开的创作中心标签页会保留，超时或连接中断后不会主动关闭编辑现场。
保留标签页不代表小红书已经保存草稿，也不代表发布成功；任务、Content 和发布记录仍保存在项目数据库。
发布结果为 `unknown` 时，先在创作中心核实，再通过“核实并登记实际结果”补录，不能直接重发。
需要在平台修改图片或补充素材时，使用“自行发布并登记结果”；自动发布执行期间不要同时编辑发布页。

`POST /publish-records` 必须提供 `account_id`，后台检查账号归属、启用状态及平台一致性：

```json
{"task_id": 1, "content_id": 1, "platform": "rednote", "account_id": 1}
```

仍需人工审核通过且使用最新内容版本；不会自动发布或根据 Chrome 会话选择账号。
人工发布或后续发布适配器确认实际账号后，通过 `POST /publish-records/{id}/result` 登记：

```json
{"publish_status": "published", "platform_post_id": "真实作品ID", "account_id": 1}
```

结果中的 `account_id` 必须与发布记录一致，重试保留原账号。
现有发布记录允许缺失账号以保留历史，但缺失账号的记录不能发布确认、重试或登记反馈；
不得自动从当前 Chrome 登录态推断历史账号。

作品发布成功后，`POST /publish-records/{id}/metrics` 登记人工确认的运营指标：

```json
{"metrics": {"impressions": 1000, "clicks": 30, "likes": 20, "comments": 2, "conversions": 1}}
```

反馈账号由已发布记录确定，客户端不能指定 `account_id` 或 `user_id`。
反馈按项目用户隔离，数据库复合外键保证作品和账号一致；来源标记为 `manual`。
`GET /publish-records/{id}/metrics` 查询历史快照。公开采集案例的互动指标不会自动混入已发布作品反馈。
自动发布、作品指标采集和经验积累的接口说明见项目内发布与运营反馈文档。

## 迁移与验证

迁移 `bc73e51a4d08` 添加采集字段及发布账号；`cd84f62b5e19` 添加分析版本和用户反馈，保留旧数据：

```powershell
python -m alembic upgrade head
python -m unittest discover -s tests -v
```

测试使用模拟 Chrome 页面和模型 HTTP 响应；设置 `PGVECTOR_INTEGRATION_TEST=1` 后，
还会在 PostgreSQL 的独立 schema 事务内验证入库、分析和发布账号约束，测试结束后回滚。

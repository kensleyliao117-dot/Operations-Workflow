# 当前业务 API

本地启动：`python run_local.py`，在 `http://127.0.0.1:8000/docs` 查看字段和交互测试。
本地入口绑定回环地址并关闭代理头信任，采集接口不配置公网反向代理。

| 模块 | 路径 | 操作 |
| --- | --- | --- |
| 身份认证 | `/auth/register`、`/auth/login`、`/auth/logout` | 注册、登录、退出当前会话 |
| 产品资料 | `/products`、`/products/{id}/documents` | 产品创建、查询、修改、删除，资料上传、下载、删除 |
| 产品知识库 | `/products/{product_id}/rag` | 一键上传入库、重新入库、自然语言检索及来源引用 |
| 任务 | `/tasks`、`/tasks/{id}`、`/tasks/{id}/generate` | 创建、查询、筛选、彻底删除关联数据、状态修改、进度保存、失败恢复、AI 生成并保存新版本 |
| 工作流 | `/tasks/{id}/workflow`、`.../start`、`.../resume`、`.../retry` | LangGraph 生成保存、暂停等待人工审核、修改重审、故障恢复 |
| 内容 | `/contents` | 创建、查询、历史列表、修改为新版本 |
| AI 配图 | `/contents/{id}/images/generate`、`.../submit-review`、`.../{index}` | 生成并保存新版本、重试审核提交、JWT 认证读取图片；见 [配图说明](image_generation.md) |
| 审核 | `/reviews` | 提交审核决定、查询记录、按任务或内容筛选 |
| 发布记录 | `/publish-records`、`.../{id}/execute` | 固定审核版本、Chrome 本地发布、结果核实、失败重试、查询历史；见 [发布说明](publishing.md) |
| 平台账号 | `/platform-accounts` | 登记实际平台账号，按 JWT 用户查询 |
| 运营反馈 | `/publish-records/{id}/metrics` | 登记、查询绑定发布作品和实际账号的指标快照 |
| 独立经验库 | `/publish-records/{id}/memory`、`/memory/experiences` | 作品同步、真实指标采集、AI 提炼与版本查询；见 [经验库说明](memory.md) |
| Prompt | `/prompts` | 创建、查询、筛选、PUT 全量替换可编辑字段 |
| 规则 | `/rules/custom`、`/rules/platform` | 规则类型、关键词/长度配置、启用状态、版本及来源维护 |
| 风险检查 | `/rules/checks`、`/rules/notices` | 检查已保存版本、风险与修改建议、历史记录及用户提示 |
| 案例 | `/rednote/cases`、`/rednote/cases/search` | 创建、查询、关键词或链接搜索、历史分析提示、分页、PUT 全量替换 |
| 案例分析 | `/rednote/analyses`、`/rednote/analyses/compare` | 保存新版本、查询历史、按案例筛选、两个版本字段对比 |
| 分析反馈 | `/rednote/analyses/{id}/feedback`、`/rednote/analyses/{id}/revise` | 单独保存用户补充、AI 参考反馈生成新版本 |
| 分析模型 | `/rednote/analysis-models` | 查询服务器配置的分析模型白名单 |
| 本地采集 | `/rednote/collect` | 采集标题、正文、图片链接和互动指标，自动保存，可选 AI 分析 |
| 自动分析 | `/rednote/cases/{id}/analyze` | 选择模型、参考历史重新分析并保存新版本，仅本机 JWT 请求可用 |

## 审核到发布流程

`DELETE /tasks/{id}` 需要 JWT，只能彻底删除所属任务。成功返回 204，清理全部内容版本、生成图片、审核、风险检查、发布记录、指标、经验及工作流检查点，不操作平台真实帖子。产品资料和共用案例、Prompt、模型连接保留。生成中、发布中或发布结果未知时返回 409。删除中断时阻止继续执行，保留可重试记录；任务历史会读取 `include_deleted=true`，允许清理旧版已移除的任务。数据库或文件清理失败后可再次删除，产品在关联未清空前仍禁止删除。

反馈支持 `DELETE /publish-records/{id}/memory`（整篇反馈与经验）、`DELETE /publish-records/{id}/memory/metrics/{metric_id}`（单次观测及其派生分析、经验）及 `DELETE /memory/experiences/{id}`（经验及对应分析）。整篇删除保留发布记录，并阻止自动同步重建；审核页可明确重新启用反馈，已删除数据不会恢复。产品在任务、资料、向量和经验关联清空后可删除。部署升级执行 `python -m alembic upgrade head`。

1. 注册并登录，创建自己的产品，再提供 `product_id` 创建任务；归属由 JWT 身份确定。
2. 调用 `POST /tasks/{id}/generate`（请求体可为 `{}`），AI 生成并保存 Content，任务自动进入 `pending_review`；也可手工创建内容，再切换 `running` 并提交待审核。
3. `POST /reviews` 提交 `task_id`、`content_id`、`status` 和可选 `comment`；审核人由 JWT 身份确定。
   只能审核当前任务最新版本；驳回必须带意见。审核决定和任务状态在同一事务保存。
4. 驳回后可再次调用生成接口，自动保存新版本并进入待审核；手工修改则先切换 `running`，修改后重新提交审核。
5. 通过 `POST /platform-accounts` 登记实际平台账号；审核通过后 `POST /publish-records`，提供任务 ID、内容 ID、平台和 `account_id`，登记开始发布。
6. 实际人工或平台发布后，`POST /publish-records/{id}/result` 登记结果：
   成功传 `publish_status=published`，提供与发布记录一致的 `account_id`，以及 `platform_post_id` 或 `platform_post_url` 至少一项；失败传 `publish_status=failed` 和 `error_message`。
   建议成功时同时保存帖子 ID 和完整链接，后续采集可据此定位作品。
7. 失败后可 `POST /publish-records/{id}/retry` 新建尝试，旧失败记录和账号关联保留。
   远程请求超时时应先核实平台实际结果，避免重复发布。相同结果重复提交幂等，冲突结果拒绝覆盖。

内容在待审核期间被修改，任务会回到 `running`，需要重新提交审核。
通用任务状态接口不能替代审核决定或登记发布结果；内容任务不能直接改为完成。

## 当前实现范围

- 发布默认保持人工登记模式；`publisher_backend=chrome` 可通过本机执行接口操作创作中心。提交后结果不明时禁止自动重发。
- 自动分析支持 DeepSeek 和已配置的通义千问聊天模型，手工分析接口仍支持保存文本。
- Prompt 更新原记录并增加版本计数，暂不保留 Prompt 历史正文；内容则保留每个历史版本。
- 当前内容版本按任务统一编号，一个任务只管理一个平台内容流。多平台/多语言独立版本分组还需调整数据模型。
- 产品、任务、内容、审核、发布、Prompt、自定义规则和案例均按登录用户隔离。
- 前端不提交归属用户 ID 或审核人员 ID，后端从已验证的 JWT 会话获取。
- RAG 已接通上传、读取、切分、千问 Embedding、pgvector 入库和检索，返回带来源引用的上下文；案例采集、分析及 LangGraph 内容编排已接入，自动运营指标采集尚未接入。
- [LangGraph 工作流](workflows.md) 复用生成 Service，支持文案、图片分别生成或组合生成，以及持久化审核中断和恢复；风险仅作提示，素材保存后可进入人工审核，审核通过后按用户选择自动发布或登记人工发布结果。
- [平台风险检查](rules.md) 组合平台规则、当前用户自定义规则、确定性匹配和 DeepSeek 提示；风险未消除或检查失败时暂停修改/复查，`notices` 明确提示不能保证过审或成为爆款。
- [内容生成服务](content_generation.md) 已接通权限、人工 Prompt、RAG、已保存的案例分析、DeepSeek 及生成保存接口；生成请求可传 `analysis_ids`，案例仅提供创作思路，产品事实以 RAG 为准。成功创建内容新版本并进入人工审核，失败回滚本次内容并记录任务错误。
- [小红书采集与分析](collectors.md) 已提供本机 JWT 采集接口和自动分析接口，可共用 Chrome 会话；案例按项目用户隔离。
- [分析版本与用户反馈](analysis_versions.md) 支持搜索历史提示、重复采集复用分析、反馈持久化、AI 修订、多模型选择和版本对比；每次修订保留旧版，后续默认参考最近版本。
- 小红书案例支持保存原始正文 `body`，查询列表和详情会返回；修改时省略 `body` 保留旧正文，显式传 `null` 清除。采集结果可通过 `services.rednote.save_collected_case` 按当前用户和链接去重保存。
- 发布必须显式关联当前用户的实际平台账号；反馈账号从成功发布记录继承，客户端不能覆盖。账号目前由人工登记，尚未接入 OAuth 所有权验证；历史缺失账号的发布记录不会自动绑定到 Chrome 会话。

运行隔离集成测试：`python -m unittest discover -s tests -v`。

## Vue 3 开发配置

默认允许 `http://localhost:5173` 和 `http://127.0.0.1:5173` 跨域访问。
如 Vite 使用其他端口，在 `.env` 设置 `CORS_ORIGINS`，用逗号分隔完整来源地址：

```dotenv
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

`.env` 固定从 `config.py` 所在目录加载，已有环境变量优先。
缺失或格式错误的 `DATABASE_URL` 会在导入配置时终止启动并给出明确错误，错误不包含连接密码。
CORS 只控制浏览器跨域请求，不提供身份验证。

## 创建任务的用户归属

前端只提交业务数据，例如：

```json
{
  "product_id": 1,
  "platform_name": "rednote",
  "task_type": "generate",
  "input_text": "为保温杯写一篇小红书文案"
}
```

任务归属使用 JWT 登录用户，`product_id` 必须是自己的产品。
`TaskRead.user_id` 保留以显示归属，`TaskCreate` 不接受 `user_id`（传入返回 422）。
已移除本地用户自动创建和 `LOCAL_USER_ID` 配置。认证与前端接入详见 [auth.md](auth.md)。

## 图片引用与作品链接

内容请求支持 `image_assets` 数组，按内容版本保存图片引用。例子：

```json
{
  "image_assets": [
    {
      "storage_key": "generated_images/product-1.png",
      "url": "https://assets.example/product-1.png",
      "prompt": "产品展示图"
    }
  ]
}
```

`storage_key` 是相对于 `storage` 的永久存储标识，`url` 和 `prompt` 可省略。
普通内容接口只保存引用，不下载外部 URL。生成和保存文件使用 [AI 配图接口](image_generation.md)，
该接口返回经过 JWT 认证的本地图片读取地址。
修改时不传 `image_assets` 则沿用旧版，传 `[]` 清空新版图片，历史版本不变。

发布成功可登记 `platform_post_url`（HTTP/HTTPS）。旧记录可重复提交原成功结果和相同帖子 ID，
补充原本缺失的链接；已存在的链接不允许被覆盖。

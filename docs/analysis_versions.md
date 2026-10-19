# 案例搜索、用户反馈与分析版本

案例与分析按 JWT 用户隔离。相同公开链接可分别进入不同用户的案例库，
不会显示其他用户的收藏、反馈或历史分析。

## 搜索与重复提示

```http
GET /rednote/cases/search?q=耳机&offset=0&limit=20
GET /rednote/cases/search?url=<完整帖子链接>
Authorization: Bearer <access_token>
```

关键词匹配标题、正文及链接；`%`、`_` 按普通字符处理，不作为通配符。
链接查询会去掉分享参数，与采集时保存的规范化链接匹配。
返回案例基本字段及 `analysis_count`、`latest_analysis_id`、`latest_analysis_version`、
`already_analyzed`、`history_hint`。未找到当前用户案例时返回空数组。
这是数据库精确查询与文本搜索，不进行跨案例的语义相似度去重。

`POST /rednote/collect` 保持按用户和规范化链接去重。
响应新增 `case_already_saved`、分析历史字段及 `analysis_source_changed`。
已有分析时，`analyze=true` 默认返回 `analysis_status="existing"` 并复用最近版本，
不再次调用模型。显式重新分析使用：

```json
{"analyze": true, "reanalyze": true, "model_id": "deepseek"}
```

只采集时仍返回 `analysis_status="not_requested"`；自动分析失败时保留案例。
旧分析缺少来源指纹或采集指标变化时，`analysis_source_changed=true` 提示历史来源无法确认一致。
复用历史分析不代表已经基于新指标重新分析。

## 用户反馈与 AI 修订

首次分析生成 v1；反馈修订生成 v2，后续重新分析生成 v3。所有版本保留，不能覆盖修改。
版本按单个案例递增，不按模型分别计数。反馈记录与分析版本分别保存。

先保存反馈，不调用 AI：

```http
POST /rednote/analyses/10/feedback
```

```json
{"content": "标题与正文的情绪转折没有说明，请补充；公开点赞数不足以证明转化效果。"}
```

返回反馈 `id`、`analysis_id`、`content`、`created_at`。
每条反馈最多 5000 字符。通过 `GET /rednote/analyses/10/feedback` 分页查询。

再基于该版本及反馈生成新版本：

```http
POST /rednote/analyses/10/revise
```

```json
{"feedback_ids": [7], "model_id": "deepseek"}
```

反馈必须属于当前用户、且指向正在修订的分析版本；每次选择 1–20 条，不能重复。
修订失败时反馈和旧分析保留，重试沿用反馈 ID，不占用版本号。
成功记录 `revision_type="feedback"`、`parent_analysis_id` 和实际使用的 `feedback_ids`。

用户反馈是需要核对的补充，不自动作为已验证事实，更不能证明产品功效或指标因果关系。

## 重新分析与历史参考

```http
POST /rednote/cases/1/analyze
```

```json
{"model_id": "deepseek"}
```

该接口每次显式调用都创建新版本；请求体可省略。
默认将最近的分析版本和当前案例最近 20 条反馈分开传入模型，并要求保留有效观察、纠正不足、提出新增观察。
超过 20 条的早期反馈不逐条发送，前次已合并的结论通过基础版本继续提供。
输出在原有结构化结果外增加 `new_insights`，记录新增、修正或仍无法确认的内容。
没有可靠新发现时应明确说明，不强制编造新观点。结构校验不能保证分析质量或结论正确。

指定基础版本或不参考历史：

```json
{"model_id": "deepseek", "base_analysis_id": 10}
```

```json
{"model_id": "deepseek", "use_history": false}
```

`base_analysis_id` 必须属于当前案例。`use_history=false` 时不提供基础分析或反馈，
也不能同时指定基础版本，适合分别观察不同模型的独立判断。

分析和修订远程调用前释放查询事务。保存时锁定案例，检查来源指纹及最新版本是否变化；
并发生成新版本或来源改变时返回 409，不覆盖已保存结果。
数据库唯一约束保证同一案例的版本号不能重复。
案例快照最多 60000 字符，案例、历史及反馈合计最多 100000 字符，超出时停止调用。

## 模型选择

```http
GET /rednote/analysis-models
```

返回系统模型及当前用户的自定义模型，包含 `id`、`name`、`provider`、`model`、`ready`、`source`、`config_id`、`is_active`，不返回密钥或接口地址。
`ready` 仅表示密钥已配置，不代表额度和远程权限已验证。
请求中的 `model_id` 选择模型配置，不能提交任意 URL、密钥或未登记的模型名。

默认配置提供 `deepseek`，沿用 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_URL`。
通义千问聊天使用独立配置，不能把 Embedding 的 `text-embedding-v3` 用于分析：

```dotenv
QWEN_CHAT_MODEL=<有权限调用的千问聊天模型名>
QWEN_CHAT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_API_KEY=<百炼密钥>
```

设置 `QWEN_CHAT_MODEL` 后，默认列表增加 `qwen`。
聊天地址和模型独立于 Embedding 配置；已有 `DASHSCOPE_MODEL` 不作为分析模型默认值。

也可通过 `.env` 的白名单配置多个模型档位：

```dotenv
ANALYSIS_MODELS_JSON='[{"id":"fast","provider":"deepseek","model":"<已开通模型名A>"},{"id":"qwen","provider":"qwen","model":"<已开通模型名B>"}]'
```

设置该项时替换默认列表，首项作为默认模型；支持 `deepseek`、`qwen` 两种适配器，最多 20 项。
同一供应商的不同模型复用对应服务器密钥，不修改全局生成模型或 Embedding 模型。
配置变更后重启后端。接口协议参考 [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
和 [百炼 OpenAI 兼容接口](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)。

### 当前用户的模型管理

`GET /analysis-models` 与原列表入口返回同一目录。Vue 3 的“分析模型设置”可直接新增、编辑、停用或删除个人配置，无需为每个模型修改 `.env`：

```http
POST /analysis-models
Content-Type: application/json

{"name":"自定义分析模型","provider":"qwen","model":"<已开通的聊天模型ID>","is_active":true}
```

`PUT /analysis-models/{config_id}` 更新完整配置，`DELETE /analysis-models/{config_id}` 删除配置。
返回的可选择标识为 `user:{config_id}`，在分析及修订的 `model_id` 中使用。系统白名单不能使用 `user:` 前缀。
每个用户最多保存 20 个配置，配置名称在用户内唯一，所有操作都由 JWT 确定归属，不接收 `user_id`。
仅支持现有 `deepseek` 和 `qwen` 供应商，复用服务端对应密钥；不接收 URL 或密钥，Embedding 模型不能用于分析。系统项只读。
停用或删除后不能再选择该配置调用模型。历史分析保存供应商、模型名与配置标识的快照，因此仍可查询和比较，编辑配置也不会改动旧版本。
新增表由迁移 `f316a42d7b80` 创建。

## 版本对比

```http
GET /rednote/analyses/compare?left_id=10&right_id=12
```

仅支持当前用户同一案例的两个版本。响应包含：

- `left`、`right`：两个版本的完整结果和模型、版本、参考版本、反馈来源。
- `differences`：发生变化的分析字段，以及对应的左右原值；数组保持原始顺序。
- `same_source`：来源指纹是否相同；任一旧版本缺少指纹时为 `null`。
- `same_parent`、`same_feedback`：基础版本和反馈 ID 列表是否相同。
- `comparison_hint`：字段差异不等于质量高低，不能直接归因于模型。

比较不同模型时，可以分别使用 `use_history=false`，或对两个模型指定相同 `base_analysis_id`。
同时核对来源和反馈是否一致；该接口只对比保存结果，不重新调用 AI 或自动评定优劣。

## 与文案生成衔接

`POST /tasks/{id}/generate` 的 `analysis_ids` 可选择任何属于当前用户的有效结构化版本。
返回的 `analysis_references` 同时包含分析 ID 和 `analysis_version`。
案例分析仅提供创作思路，产品事实仍只依据 RAG 检索资料。

## 权限、迁移与验证

所有接口需要 JWT；采集、自动分析和反馈 AI 修订还要求现有本机访问检查。
公网反向代理应明确拒绝 `/rednote/collect`、`/rednote/cases/*/analyze`
及 `/rednote/analyses/*/revise`，不能通过剥除来源头转为回环请求。
反馈保存、搜索、历史查询和版本比较不连接 Chrome、不调用模型。

迁移 `cd84f62b5e19` 为已有分析按案例及 ID 顺序补版本号，保留正文、结果、模型和来源指纹，
新增分析元数据和独立反馈表。旧记录没有可还原的父版本或反馈关系，保留空值。

```powershell
python -m alembic upgrade head
python -m unittest discover -s tests -v
```

设置 `PGVECTOR_INTEGRATION_TEST=1` 还会在隔离 PostgreSQL schema 中验证版本唯一性、
反馈保存、比较查询和用户隔离，结束后回滚。模型及 Chrome 测试使用模拟响应。

Vue 3 的“内容生成 → 爆款案例与分析”已接入搜索、补充反馈、模型管理、模型切换和版本对比。

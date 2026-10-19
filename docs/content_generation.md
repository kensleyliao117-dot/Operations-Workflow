# 内容生成服务

`services/content_generation.py` 组合已有产品权限、人工 Prompt、RednoteAnalysis、RAG 和文本模型模块，
支持草稿预览，以及按已有任务生成并保存 Content 新版本。生成成功后进入人工审核，不直接发布。
LangGraph 编排入口见 [工作流说明](workflows.md)，直接复用本服务处理生成和保存。
已启动工作流的任务应使用工作流的恢复接口重新生成，不能通过旧生成接口绕过编排。
文案保存后可使用 [AI 配图接口](image_generation.md) 生成图片，新版本自动重新检查并提交人工审核。
生成默认从独立 [运营经验库](memory.md) 检索当前用户、本产品的历史经验，作为独立参考区传入。
响应和保存元数据包含 `memory_references` 与 `memory_status`；经验不替代 RAG 产品事实，经验库不可用不阻断已有生成链。

生成请求可通过 `model_id` 指定当前用户的文本模型，未指定时使用账号默认模型，再兼容服务端系统默认模型。配置入口见 [模型连接](model_connections.md)。`generation_model` 与 `generation_model_profile` 保存实际模型和公开连接信息，不保存 API Key；模型归属在 RAG 和模型请求前校验。

## 生成并保存接口

先通过 `POST /tasks` 创建任务，指定自己的 `product_id`、`platform_name="rednote"`、
`task_type="generate"` 和运营需求 `input_text`，再调用：

```http
POST /tasks/1/generate
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "prompt_id": 1,
  "analysis_ids": [2, 3],
  "top_k": 5,
  "max_context_chars": 12000
}
```

`analysis_ids` 为当前用户已保存的结构化案例分析 ID，最多 5 个正整数，不能重复；
按请求中的顺序传入模型。参考内容及来源信息序列化后的总长度不能超过 12000 字符。
该限制独立于产品资料的 `max_context_chars`，超出时减少所选分析或缩短分析内容。
不选择人工 Prompt 和案例、使用默认检索参数时，请求体传 `{}`。
请求不接受 `user_id`、`product_id`、`platform` 或 `user_requirement`；身份由 JWT 提供，
产品、平台和需求从当前用户的任务读取。旧任务未指定平台时采用 `rednote` 并写回任务。

成功返回 HTTP 201，包含 `task_id`、`content_id`、`version`、`created_at`、
`task_status="pending_review"`、标题、正文、标签、模型信息、Prompt 版本、资料来源，
以及 `analysis_references`、`review_required=true`。每条案例引用包含分析 ID、分析版本号、案例 ID、
分析模型、来源指纹、分析时间和实际使用的创作字段快照。可通过 `GET /contents/{content_id}` 或
`GET /contents?task_id=1` 读取保存结果及版本历史。
生成结果和内容详情均提供 `notices`：规则检查不保证平台过审，案例参考不保证生成内容成为爆款，仍需人工调整。

服务调用方式为：

```python
from schemas.content_generation import TaskContentGenerationRequest
from services.content_generation import generate_and_save_content

saved = generate_and_save_content(
    db, task_id=1, data=TaskContentGenerationRequest(prompt_id=1, analysis_ids=[2, 3]),
)
```

## 草稿预览调用

```python
from services.content_generation import generate_content

# db 为经过 JWT 认证、当前请求专用的 Session。
draft = generate_content(
    db,
    product_id=3,
    user_requirement="根据资料写一篇耳机通勤场景的小红书文案，语气自然。",
    platform="rednote",
    prompt_id=1,  # 可选；不选择人工 Prompt 时省略。
    analysis_ids=[2, 3],  # 可选；引用已保存的结构化案例分析。
)

title = draft.title
body = draft.body
tags = draft.tags
sources = draft.sources
```

产品、人工 Prompt 和案例分析 ID 必须属于当前用户。需求为 1–2000 字符，
可选检索参数 `top_k`、`min_similarity`、`max_context_chars` 与 RAG 入口一致。
服务会结束只读事务，不要在同一个 Session 混入其他未提交修改。
`generate_content` 保留为只返回校验后草稿的内部能力，不创建 Task / Content；
HTTP 生成接口调用 `generate_and_save_content` 完成任务生成和保存。

## 处理流程

1. 从认证 Session 读取用户身份，验证输入和平台。
2. 复用 `services.product.get_product` 校验产品归属。
3. 若选择人工 Prompt，复用 `services.prompt.get_prompt` 校验归属，并检查启用状态及平台。
4. 若选择案例分析，通过案例所属用户校验归属，验证结构化结果并提取创作字段。
5. 调用 `rag.retriever.retrieve` 检索产品资料；没有可用上下文或来源时停止生成，不能用案例代替。
6. 将需求、产品信息、检索资料、人工风格 Prompt 和案例参考分别放入 JSON 字段，交给 `deepseek.generate_text`。
7. 解析并校验模型的 JSON 草稿，返回文案及来源。

任务接口在上述流程前，使用短事务锁住当前用户的任务，验证状态、产品、Prompt 及案例分析，
记录独立尝试 ID、生成参数和当前版本，并将任务切换为 `running`。
提交后释放行锁和连接，再调用 RAG / DeepSeek。

生成通过校验后，再次锁住任务，确认尝试 ID、任务信息和内容版本没有变化，
复用 `services.content.add_content_version` 分配新版本。
Content 和任务的 `pending_review` 状态在同一事务提交，任务 checkpoint 保存内容 ID、
版本和本次模型、Prompt、产品资料引用及案例参考快照。不创建审核通过记录或发布记录。
checkpoint 中的生成信息表示最近一次尝试；Content 的正文及标签仍按现有逻辑保留各版本。
新生成内容还通过 `generation_metadata` 保存该版本的资料、模型、Prompt 和案例参考快照。
工作流内部传入的生成幂等标识用于故障恢复，已提交的同一步结果直接复用，不再次调用模型。
这两个字段不接受客户端写入，旧内容对应值保留为空。
LangGraph 使用内部 `defer_review=true` 延后待审核提交：保存 Content 后保持 `running`，
由独立规则节点提供风险提示后进入 `pending_review`，风险及检查失败不会阻止人工审核。原有直接生成接口仍保留上述默认行为，
新前端应使用工作流入口完成规则检查闭环，详见 [平台风险检查](rules.md)。

## 失败、重新生成与审核

- RAG、模型调用、草稿校验或内容保存失败时，本次 Content 写入回滚。
  任务记为 `failed`，`failed_from_status="running"`，保存安全错误说明及生成参数快照。
  已有内容版本保留。数据库整体不可用时，失败记录也可能无法写入。
- 认证、归属、Prompt、案例分析或任务参数预检查失败，不启动生成，也不修改任务状态。
- 对 `pending_review` 或 `rejected` 任务再次调用生成接口，会创建新内容 ID 和递增版本，
  成功后重新进入待审核；生成阶段失败的任务也可直接调用同一接口重试。
  失败尝试不占用内容版本号。参数按本次请求读取，需要沿用 Prompt 或案例时再次传入对应 ID。
- 同一任务正在生成时，第二个生成请求返回 409；任务被取消或内容被并发修改后，
  旧生成结果不会覆盖新的业务状态或内容。
- 已通过审核、发布中、已完成或已取消的任务不能生成；发布阶段失败应走发布重试接口。
- 必须通过 `POST /reviews` 审核最新版本，审核通过后才可登记发布。
  旧版本的审核结果不能用于新版本，生成接口不会直接发布。

每次生成是同步请求。若进程被强制终止而未执行失败处理，任务可能停留在 `running`；
应核实后通过现有任务失败接口登记失败与快照，再重新生成。

权限、Prompt 与案例分析校验在任何远程模型调用前完成。Embedding、向量检索及 DeepSeek HTTP
均由已有模块负责，生成服务不重新实现这些能力。

系统消息包含用户指定的小红书固定约束，以及统一的输入层级和输出格式要求。
用户可以通过人工 Prompt 自定义文案风格、语气、结构和表达方式；产品事实必须依据提供的产品资料，不得编造产品参数、功效或使用效果。
人工 Prompt 只放入用户消息的 `custom_prompt.style_requirements` 字段，
用户需求和检索内容也分别存放，不能作为系统提示词使用。
用户需求、人工 Prompt 中的功效或参数不视为产品事实来源。

## 案例参考与产品事实

先通过 `POST /rednote/collect`（`analyze=true`）或 `POST /rednote/cases/{id}/analyze`
保存结构化分析，再将返回的分析 ID 放入生成请求的 `analysis_ids`。
也可通过 `GET /rednote/analyses` 选择已保存的分析。生成接口不重新采集或分析案例，
不要求 Chrome 保持开启。旧的纯文本分析没有 `result` 时，需先重新分析。

模型的输入分为独立字段：

| 字段 | 用途 |
| --- | --- |
| `user_requirement` | 运营目标和场景要求 |
| `product_knowledge.context` | 产品参数、功效、使用效果等事实的唯一依据 |
| `custom_prompt.style_requirements` | 用户自定义风格、语气、结构和表达方式 |
| `case_references` | 已保存分析中的创作思路，仅用于参考 |

案例只提取 `content_structure`、`hook`、`emotional_triggers`、`cta` 和 `reusable_patterns`。
案例原文、总结、产品痛点、标签、图片/视频描述和互动指标不传给生成模型，
案例内容也不会拼入 RAG 的检索问题或产品资料上下文。

系统提示词明确要求：即使创作参考包含品牌、参数、功效、使用效果、优惠或使用经历，
也不得直接移用；只有产品资料支持的事实才能写入文案，没有依据时省略。
这种分隔和提示词约束不能保证模型完全没有事实错误，仍需人工核对后审核。

## 平台区分

生产注册表 `PLATFORM_PROFILES` 当前仅包含 `rednote`。
小红书固定提示词为 `REDNOTE_SYSTEM_PROMPT`，对应 `RednoteDraft` 草稿校验器。
平台标识会去掉首尾空白并转为小写；人工 Prompt 的平台须与其一致。
其他平台会明确报错，不会套用小红书规则。
`analysis_ids` 当前仅支持 `rednote`；后续注册其他平台时，也不能直接套用小红书案例分析。

后续新增平台时，应定义专属系统提示词和草稿校验器，再注册平台配置。
不能仅修改请求中的平台名就认为已支持该平台的内容和发布要求。

## 草稿校验与返回值

模型必须只返回 JSON 对象，包含 `title`、`body`、`tags` 三个字段。
缺失、额外、重复字段或错误类型均拒绝，不从自由文本中猜测提取内容。

- 标题：非空单行文本，最多 300 字符，匹配当前 Content 字段容量。
- 正文：非空文本；标题和正文都不能包含 NUL。
- 标签：1–20 个文本词条，每个最多 100 字符；小红书校验器去掉前导 `#`、去重，并拒绝空词条和内部空白。

这些是内部结构限制，不等于小红书的实际发布限制。字段有效不代表模型陈述的事实、
功效和平台合规已经验证。返回值明确包含 `validation_scope="structure"` 和
`review_required=true`，仍需人工审核，后续再接入规则检查及工作流。

结果同时包含产品和平台、生成模型、Embedding 模型及维度、选用的 Prompt ID 和版本，
以及检索来源和 `analysis_references`。未选择案例时 `analysis_references=[]`。
Prompt 与案例参考在检索前复制成快照，避免 RAG 释放事务后访问过期 ORM 对象。
Content 的 `tags` JSON 字段独立保存标签，旧内容默认为空数组。
人工修改不传 `tags` 时沿用旧版，传 `[]` 时清空新版本标签，历史版本不变。
数据库字段由迁移 `f41a93c8b602_add_content_tags.py` 添加：

```powershell
python -m alembic upgrade head
```

参数、平台、Prompt 状态、分析结果不符或无资料时返回 `InvalidInputError`；
越权及不存在的产品、Prompt、案例分析沿用 404 语义。
模型草稿结构无效时抛出 `ContentGenerationError`，继承 `ServiceUnavailableError`；
不会回显模型原文。RAG / DeepSeek 超时等错误沿用已有异常，不自动重试或切换模型。

## 验证

```powershell
python -m unittest discover -s tests -p test_content_generation.py -v
```

测试使用独立数据库和模拟 RAG / DeepSeek，检查权限、平台、提示词分隔、案例参考白名单、
无资料时拒绝生成、草稿结构、生成保存、失败回滚、版本递增、并发中断和审核门禁。
业务 API 测试覆盖 JWT、采集分析到生成的完整接口链及跨用户引用拒绝。
真实 pgvector 测试在独立 schema 事务中验证检索与生成保存，结束后回滚；
模型 HTTP 使用模拟响应，不发送真实资料给模型供应商。

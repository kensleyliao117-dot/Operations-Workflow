# 独立运营经验库

```text
发布成功 → 业务库保存发布结果 + 同步事件
    → OperationsMemory 归档作品、Content 版本、产品和实际账号
    → 多次采集创作中心 / 公开互动指标 / 录入真实后台指标
    → 每次保留观测时间、数据来源和发布后观察窗口
    → 账号默认文本模型基于观测提炼经验 → 保存证据与经验版本
    → 下次文案生成自动检索 → 与产品 RAG、人工 Prompt 分开传入
```

运营经验是待验证的创作参考，不是产品参数、功效或使用效果的依据。
产品事实始终以本产品 RAG 资料为准，经验不保证流量、转化或爆款。
该功能属于经验库与检索增强，不直接训练或改变用户使用的大模型参数。

## 前端入口

反馈面板支持删除单次观测、经验版本或整篇反馈。删除观测会清理使用该观测的分析和经验，正在进行的分析若证据已删除则不保存结果。整篇删除会清理经验库作品、指标、分析和经验，业务库保留发布记录并标记 `feedback_deleted`，阻止自动同步重建；清理中断时保留“重试删除反馈”入口。只有明确点击重新启用反馈才会创建空白归档，不恢复已删除记录。

彻底删除任务时，还会删除其发布记录和全部经验库关联。跨库、文件和检查点按可重试步骤清理；失败时任务保留删除中标记并阻止新增操作，避免产生新的孤立关联。此操作不会删除小红书平台上的作品。

左侧“数据反馈”进入 `/feedback`，列出当前用户、当前平台已发布的作品。点击作品后可查看指标快照、观察窗口、来源和经验版本。
审核发布页面在发布成功后提供“数据反馈与运营经验”链接。前端列表使用 `GET /memory/publications?platform=rednote&product_id=1`，支持分页和产品筛选，只读取业务库中的成功发布记录。
每次采集是一个新观测，失败重试复用原请求键。选择最多 20 个历史快照并明确确认后才调用 AI，后台指标录入支持真实零值和未知留空。历史经验可查看原始分析证据、停用或重新启用。

## 双数据库

`database.py` 仍连接主业务库，`memory_database.py` 连接独立经验库，分别使用 `Base` 与 `MemoryBase`。
经验库不创建 users、tasks、contents 等业务表，不建立跨数据库外键。
经验记录通过业务 ID 和用户 ID 追溯来源，接口始终以业务库的登录身份和归属校验授权。

```dotenv
# 可省略：默认使用 DATABASE_URL 所在服务器和凭据，数据库名称改为 OperationsMemory。
MEMORY_DATABASE_URL=
```

也可配置独立 PostgreSQL 服务器的连接地址。配置不能使用与业务库相同的数据库名称。
连接地址包含凭据，不提交 `.env`，也不把连接地址写入日志。
首次部署执行：

```powershell
python -m alembic upgrade head
python -m memory.setup
```

第一条迁移业务库的 `memory_sync_events`；第二条显式创建经验数据库并运行经验库迁移。
创建数据库需要相应 PostgreSQL 权限；已有数据库可直接运行经验迁移：

```powershell
python -m alembic -c memory/alembic.ini upgrade head
```

经验库使用自己的 `memory_alembic_version`。普通 `alembic upgrade head` 不负责经验库表。
数据库在现有 PostgreSQL 服务的数据目录中创建，没有迁移项目、产品资料或安装其他程序。

经验库表：

| 表 | 内容 |
| --- | --- |
| `published_posts` | 已发布作品、主库发布记录、用户、产品、账号、Content ID 与版本的归档 |
| `post_metrics` | 指标值、来源、观测时间、原始显示文本及精度说明 |
| `feedback` | 模型分析、人工补充、所选指标证据快照、模型与来源指纹 |
| `optimization_memories` | 可检索的经验版本，支持启用/停用，保留历史 |

## 发布成功后的同步

发布状态和同步事件在业务库同一事务保存；发布成功后尝试同步作品归档，不自动运行付费 AI 分析。
经验库暂时不可用时，发布记录仍为 `published`，任务仍为 `completed`，同步事件可在恢复后重试。
跨库不依赖分布式事务：经验库以发布记录唯一键去重，镜像已保存但主库同步确认失败时可安全重试。

```http
GET /publish-records/3/memory/sync-status
POST /publish-records/3/memory/sync
POST /memory/sync-pending?limit=20
```

单条同步返回归档作品；批量补同步只处理当前用户的 pending/failed 事件，返回各事件状态。
迁移为旧的成功发布记录补建待同步事件；缺少产品、账号或作品定位信息的历史记录需要先核对补充。
当前没有常驻采集调度器；批量补同步、指标采集和经验分析均可由前端明确触发，后续可由受控后台任务调用同一 Service。

## 真实指标收集

所有接口都需 JWT。自动采集通过本机 Chrome/CDP，支持显式选择来源，旧请求默认按可见范围选择：

- 公开作品：读取详情页的互动指标，来源为 `rednote_public`。
- 私密作品：读取所属账号创作中心实际加载的作品列表和互动指标，来源为 `rednote_creator`，精度标记为 `creator_reported`。
- 创作中心采集核对实际账号、作品 ID 和页面中的目标作品；不会改变作品权限。首版仅定位当前创作中心首屏列表中的作品，未找到时停止，不用其他作品代替。
- 请求 `source=rednote_creator` 可从创作中心采集公开或私密作品，仍需核对实际账号和目标作品；前端默认选用创作中心。`source=rednote_public` 从可访问的公开详情页读取；`source=auto` 保留旧调用方式。读取失败不会悄悄切换来源。
- 作品关联以链接规范化后的笔记 ID 为准，分享参数不影响关联。作品后续修改公开或私密状态，不要求与发布时的可见范围一致，也不修改原发布记录。

```http
POST /publish-records/3/memory/collect
Authorization: Bearer <access_token>
Content-Type: application/json

{"idempotency_key": "metrics-creator-001", "source": "rednote_creator"}
```

从发布记录读取帖子 ID/链接，核对创作中心当前登录用户 ID 和记录绑定账号。公开详情页另核对作品作者 ID；私密作品通过所属账号的创作中心作品列表确认归属。
账号或作者不能确认时不保存指标；不能使用共享采集会话的其他账号代替发布账号。
采集与本机发布共用 Chrome 互斥锁，接口只允许本机直连，不接受代理转发和未许可网页来源。
若详情页需要分享参数，可传 `source_url` 完整网页链接，但必须指向该发布记录的同一作品。

自动获取真实的点赞、评论和收藏。没有读到的指标不补零；显示“1万”等缩写时保存原文并标记 `displayed_rounded`。
数字 `0` 可以保存。详情页用“赞”“收藏”“评论”等按钮文字替代数字时，从页面内同一笔记的明确计数补充，标记为 `page_reported`；没有明确计数时仍保持缺失，不能仅凭按钮文字推测为零。
创作中心接口中的 `view_count` 单独保存为 `views` 浏览量，不写成曝光量；点击、转化等未取得的指标仍保持缺失。
公开页面不能提供的曝光、点击和转化，不通过 AI 猜测，首版可从真实后台数据手工录入：

```http
POST /publish-records/3/memory/metrics
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "idempotency_key": "metrics-manual-001",
  "account_id": 2,
  "observed_at": "2026-10-18T18:00:00+08:00",
  "metrics": {"impressions": 1200, "clicks": 36, "conversions": 0}
}
```

示例中的时间和指标需要替换成真实观测，不能早于作品发布时间或晚于当前时间。
可传 views、impressions、clicks、likes、favorites、comments、conversions，至少提供一项非负整数。
缺失表示未知，显式 `0` 才表示真实观察到零；手工录入标记 `manual` / `manual_reported`，系统不能验证人工输入是否真实。
`published_at`、`observed_at` 在数据库中按 UTC 保存；输入实际发布时间和观测时间必须带时区。历史无时区响应按 UTC 解释。
`created_at` 是数据库记录创建时间，使用数据库默认值，其时区取决于部署会话配置；运营观测排序使用 `observed_at`。

同一个指标请求重试复用 `idempotency_key`。同键不能更换作品、数据或观测时间；新观测使用新键。
`GET /publish-records/3/memory/metrics?limit=20` 查询观测历史。
采集、手工录入和历史查询的指标响应均补充 `published_at`（UTC）和 `elapsed_since_publish_seconds`，例如 `7200` 表示发布后 2 小时、`86400` 表示发布后 1 天。
该值由已归档的发布时间与实际观测时间计算，不接受客户端自行指定。发布时间缺失时返回 `null`，界面显示观察窗口待核实，不用当前时间猜测。两份源时间已经持久化，因此计算值无需新增数据库字段或迁移。
原 `/publish-records/{id}/metrics` 继续保留为业务库旧版手工记录，不会自动作为经验库证据；新闭环使用 `/memory/metrics` 和 `/memory/collect`。

## 提炼与版本积累

```http
POST /publish-records/3/memory/analyze
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "metrics_ids": [1, 2],
  "user_note": "下一轮比较两种通勤场景开头，暂时没有转化数据"
}
```

`metrics_ids` 为该作品的经验库指标快照 ID，最多 20 个，不接受其他用户或其他作品的快照。
按观测时间提供原始值、来源、精度、发布时间、发布后经过秒数和文案；时间上下文也保存到分析证据中。同一作品的累计观测不能相加或当作多篇独立样本，不同发布后观察窗口不能直接当作同周期数据比较。
系统要求模型输出 summary、lessons、limitations，每条经验引用真实存在的证据 ID。
经验分类包括 Hook、标题、正文、图片、CTA、受众；缺少受众、图片像素或评论原文时不能推断相应事实。
每条结论均为 `hypothesis`，置信度只允许 low/medium，必须保留样本不足与观测边界。
`visibility=private` 的笔记必须标注私密测试前提，零互动不能作为公开传播效果、受众偏好或写法效果的证据。
结构和引用校验不保证自然语言分析完全正确，可停用不合适的经验。

相同作品、指标集合、人工补充和模型重复分析复用已保存结果；新指标或新补充生成新版本，并参考上一版本提出下一轮实验。
分析 Prompt 版本也进入证据指纹；调整分析约束后生成新版本，避免复用旧 Prompt 的缓存。
缺少分母时不能计算或直接比较互动率、点击率、转化率；`creator_reported` 不等于公开缩写数值。
分析失败或引用不存在的指标时不保存 feedback 或经验；证据和经验在经验库同一事务提交。

```http
GET /memory/experiences?product_id=1
GET /memory/experiences/4
GET /memory/feedback/2
GET /memory/publications/1
PATCH /memory/experiences/4
```

PATCH 请求 `{"is_active": false}` 可停用经验；历史证据仍可查询。
查询反馈可查看当时的指标证据、人工补充、模型和分析；归档作品可追溯主库发布记录、账号与作品链接。

## 下一次生成

已有任务生成和 LangGraph 不需要新增检索节点，仍复用 `services/content_generation.py`：

```json
{"use_memory": true, "memory_limit": 5}
```

默认启用，关闭时传 `use_memory=false`。检索按当前用户、产品和平台隔离，只使用每篇作品最新且启用的经验版本，停用最新版本不会自动回退到旧版本。
首版按中文双字词项和英文词项相关度排序，最多评估近期 200 条、返回 5 条，总注入不超过 12000 字符；这不是向量语义检索。
超长经验跳过，没有相关词项则不注入。需要按发布账号筛选时可直接调用：

```http
GET /products/1/memory/retrieve?query=通勤开头&account_id=2&limit=5
```

生成时运营经验作为独立 `operation_memories` 数据区传入，与产品 RAG、案例参考、人工 Prompt 分开组织。
生成响应和 Content 的 generation_metadata 保存 `memory_references` 的 ID、版本、证据关联和内容快照。
`memory_status` 为 used、no_matches、unavailable 或 disabled；经验库故障时允许继续使用产品 RAG 生成，并明确返回 unavailable。
在 API 请求之外直接调用生成 Service 时，可向业务 Session 的 `info['memory_session_factory']` 提供受控经验 Session 工厂；HTTP 请求已自动提供。
RAG 没有产品资料时仍拒绝生成，不能用运营经验替代事实资料。

## 验证边界

双库隔离测试验证同步、快照来源、幂等、版本、用户/产品隔离、失败恢复及生成引用。
真实 PostgreSQL 测试在两个数据库的随机 schema 中进行，回滚测试数据，不写入真实运营指标或虚构经验。
自动化模型测试使用模拟响应，PostgreSQL 测试使用独立随机 schema。
2026-10-18 已使用真实私密笔记联调创作中心读取、发布版本核对和指标入库，数据来源明确标记为 `rednote_creator`。
本机演练过程和实际记录 ID 保存在被 Git 忽略的 `storage/private-e2e/report.json`；经验提炼、持久化与检索的结果以该报告为准。
私密测试只能验证流程，不能证明公开传播效果；当前经验分析读取文案、图片 Prompt 与指标，不包含图片像素、OCR 或评论内容分析。

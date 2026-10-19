# 产品知识库 RAG

产品资料上传、读取、切分、通义千问 Embedding、PostgreSQL + pgvector 入库和检索已接通。
检索返回带引用的产品资料上下文，供[内容生成服务](content_generation.md)及后续内容 Agent 使用；
RAG 入口自身不直接生成营销文案或聊天回答。

## 完整流程

```text
JWT 登录用户
  → 上传自己的产品资料
  → 读取纯文本
  → 切分并保留来源信息
  → 通义千问 Embedding
  → pgvector 保存完整片段和向量

自然语言问题
  → 校验用户和产品归属
  → 用相同模型、相同维度生成查询向量
  → 在当前用户、指定产品的资料内检索
  → 返回 context 和 sources
```

模块职责如下。业务调用优先使用 `retriever.py`；底层模块也可独立使用。

| 模块 | 职责 |
| --- | --- |
| [services/product.py](../services/product.py) | 上传、列表、下载和删除产品资料，校验文件存储路径 |
| [rag/document_loader.py](../rag/document_loader.py) | 校验归属并读取 PDF、TXT、MD、DOCX，返回纯文本 |
| [rag/text_splitter.py](../rag/text_splitter.py) | 切分文本，生成稳定的片段 ID、文档哈希及字符偏移 |
| [rag/token_counter.py](../rag/token_counter.py) | 可选：加载与向量模型匹配的本地分词器 |
| [rag/embeddings.py](../rag/embeddings.py) | 调用通义千问向量接口，返回向量、来源元数据及可用的 Token 用量 |
| [rag/vector_store.py](../rag/vector_store.py) | 保存、删除文档向量，按用户、产品、模型和维度检索 |
| [rag/retriever.py](../rag/retriever.py) | 编排一键上传入库、已有文档入库和自然语言检索 |
| [routers/rag.py](../routers/rag.py)、[schemas/rag.py](../schemas/rag.py) | 提供并验证 RAG HTTP 接口 |

读取和切分模块只处理文本，Embedding 模块只生成向量；完整入口负责将这些步骤与向量存储串起来。

## HTTP 接口

所有产品资料和 RAG 接口都要求 JWT。先通过 `/auth/login` 登录，
在请求中携带 `Authorization: Bearer <access_token>`，也可在 `/docs` 的 Authorize 中填写令牌。
`product_id` 必须属于当前用户；前端无需提交 `user_id`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/products/{product_id}/rag/documents` | 上传文件并立即入库，成功返回 201 |
| POST | `/products/{product_id}/rag/documents/{document_id}/index` | 对已上传资料入库或重新入库，成功返回 200 |
| POST | `/products/{product_id}/rag/retrieve` | 自然语言检索，成功返回 200 |
| POST | `/products/{product_id}/documents` | 仅上传文件，成功返回 201 |
| GET | `/products/{product_id}/documents` | 查看资料列表和状态 |
| GET | `/products/{product_id}/documents/{document_id}/download` | 下载自己的资料 |
| DELETE | `/products/{product_id}/documents/{document_id}` | 删除资料及其全部向量 |

示例中的产品和文档 ID 需替换为当前用户拥有的真实 ID。

### 一键上传入库

向 `POST /products/3/rag/documents` 提交 multipart 表单，文件字段名为 `file`。
切分参数通过 query 设置，省略时使用默认值：

```text
/products/3/rag/documents?chunk_size=800&chunk_overlap=100
```

响应提供 `product_id`、`document_id`、`filename`、`status`、`chunk_count`、
`model`、`dimensions` 和 `document_hash`；成功时 `status` 为 `indexed`。

也可先用仅上传接口保存多份资料，再分别调用已有文档入库接口。

### 已有文档入库与重试

向 `POST /products/3/rag/documents/1/index` 提交 JSON。
使用默认切分参数时传 `{}`，或显式指定：

```json
{"chunk_size": 800, "chunk_overlap": 100}
```

两个入库入口的 `chunk_size` 范围为 1–2000 字符，默认 800；
`chunk_overlap` 默认 100，必须满足 `0 <= chunk_overlap < chunk_size`。
长度以 Python 字符数计算，不是 Token 数；上限用于兼容现有 Embedding 的字节保护值。

### 自然语言检索

向 `POST /products/3/rag/retrieve` 提交 JSON：

```json
{
  "query": "这款耳机有哪些适合通勤的特点？",
  "top_k": 5,
  "min_similarity": 0.3,
  "max_context_chars": 12000
}
```

| 参数 | 默认值 | 限制 |
| --- | --- | --- |
| `query` | 必填 | 去掉首尾空白后为 1–2000 字符 |
| `top_k` | 5 | 返回候选片段上限，范围 1–20 |
| `min_similarity` | 不限制 | 可选，范围 -1–1；按实际资料和模型效果调整 |
| `document_id` | 不限制 | 可选，必须是该产品内的资料 |
| `max_context_chars` | 12000 | 上下文长度预算，范围 256–50000 字符，包含引用标题和分隔符 |

响应包含 `product_id`、`query`、`model`、`dimensions`、`context` 和 `sources`。

- `context`：带 `[1]`、`[2]` 等引用标签的资料文本，可传给后续内容 Agent。
- `sources`：对应引用的文件名、文档 ID、片段 ID/序号、相似度、实际采用的文本及原始元数据。
- `truncated`：来源文本因上下文预算被截断时为 `true`；metadata 中的偏移仍指向原始完整片段。

没有当前模型/维度的向量时不调用查询 Embedding，直接返回空 `context` 和 `sources`。
没有达到相似度阈值的结果时也返回空结果。上下文预算可能使实际返回片段少于 `top_k`。

## 后端调用

使用认证后、当前请求专用的数据库 Session：

```python
from rag.retriever import index_document, retrieve

indexed = index_document(db, product_id=3, document_id=1)
knowledge = retrieve(db, product_id=3, query="通勤时有哪些使用优势？")

context = knowledge.context
sources = knowledge.sources
```

一键上传入口为 `upload_and_index_document(db, product_id, upload)`，接收上传文件对象。
实际向量化和检索会向配置的模型服务发送资料或查询文本，并可能产生 API 费用。

底层调用可使用 `load_document`、`split_document`、`embed_document` 和
`save_document_vectors`。保存时须传入文档的完整 `EmbeddedChunk` 列表，不能只传其中一部分。
`split_text`、`embed_chunks` 和 `embed_texts` 用于可信内部数据，不独立执行数据库归属校验。

## 文件和配置

### 产品资料路径

产品资料继续保存在项目下的 `storage/uploads`，与 pgvector 的安装资料目录相互独立。
`UPLOAD_DIR` 可在 `.env` 中配置；相对路径以项目目录为基准，绝对路径直接使用。

```dotenv
UPLOAD_DIR=storage/uploads
```

新上传文件的实际路径为 `{UPLOAD_DIR}/{user_id}/{product_id}/{随机文件名}.{扩展名}`。
数据库的 `storage_key` 使用 `uploads/{user_id}/{product_id}/{文件名}` 逻辑标识，
读取时会验证其归属和目录边界。RAG 不另建本地向量缓存，向量保存在业务数据库。

支持 PDF、TXT、MD、DOCX，单文件最多 20 MB；TXT/MD 必须为 UTF-8，可带 BOM。
PDF 最多 500 页、单页内容流最多 10 MB；DOCX 正文 XML 最多 20 MB。
解析文本最多 200 万字符，DOCX 提取正文段落和表格文字；扫描 PDF 需先进行 OCR。
loader 会统一换行、去除 NUL 和首尾空白，Markdown 保留原有文本标记。

### 通义千问 Embedding

账号可在“设置 → 模型连接 → Embedding”添加、编辑和删除自己的接口，设置默认后同时用于资料入库和检索，支持兼容协议的其他供应商。配置与迁移规则见 [模型连接](model_connections.md)。以下 `.env` 配置作为未设置用户默认连接时的系统回退。切换接口、模型或维度后需重新入库，旧向量按空间隔离保留。

当前本地配置使用百炼北京官方兼容接口、`text-embedding-v3`、1024 维、每批 10 条：

```dotenv
DASHSCOPE_API_KEY=your-key
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_DIMENSIONS=1024
EMBEDDING_BATCH_SIZE=10
```

`EMBEDDING_API_KEY` 可单独设置，省略时使用 `DASHSCOPE_API_KEY`。
模型、根地址、维度和批量大小均由 `config.py` 的 `EMBEDDING_*` 配置统一提供，
省略时分别默认 v3、上述官方地址、1024 维和每批 10 条；不读取聊天模型的
`DASHSCOPE_MODEL` 或 `DASHSCOPE_URL`。`.env.example` 与本机使用相同的模型配置。
模型、维度、根地址和批量上限在加载配置时校验，API Key 在调用模型时校验。
API Key 需与服务商和地域匹配，根地址不含 `/embeddings`。

项目支持 v3 的 64、128、256、512、768、1024 维；v4 另支持 1536、2048 维。
每批最多 10 条，默认请求超时 30 秒；网络异常、429 和部分 5xx 最多重试两次，
401/403 不重试，不跟随重定向。响应会校验模型、顺序、数量、维度和有限数值。

文档和查询必须使用同一模型及维度。更换模型、维度、供应商或实际模型版本后需重新入库，
已有不同模型/维度的向量可共存；仅凭维度相同不能认定向量兼容。
检索会在发出请求前核对客户端的模型和维度与待检索索引的条件一致，发现错配时返回明确错误。

### Token 计数

当前未配置经确认匹配的分词器，Embedding 输入使用 8000 UTF-8 字节的保守检查。
这不是精确 Token 计数；切分长度和上下文预算也以字符计。

取得与向量模型匹配的 `tokenizer.json` 后，可配置：

```dotenv
EMBEDDING_TOKENIZER_PATH=storage/tokenizers/text-embedding-v3/tokenizer.json
EMBEDDING_TOKENIZER_MODEL=text-embedding-v3
```

模块不自动下载分词器，会检查文件及声明的模型、禁用截断和填充，并保留特殊 Token 规则。
配置分词器后按其输出检查项目设置的 v3/v4 单段 8192 Token 上限；
文件与云端模型是否匹配仍需确认，不能用聊天模型词表替代。

`QwenEmbeddings.last_usage` 分别提供本地逐片段计数和服务端成功响应报告的总用量，
未提供的数据为 `None`，服务端批次用量不平分给片段。
本地计数可随片段保存为 `token_count` 和 `token_count_source`，后者标识分词器文件 SHA256。
服务端用量不包含未报告的失败或重试计费；客户端实例不应跨线程交叉复用 `last_usage`。

### PostgreSQL 与 pgvector

本机使用 PostgreSQL 18.6，安装及数据目录分别为 `E:\PostgreSQL` 和 `E:\PostgreSQL\data`。
pgvector 服务器扩展为已校验发布包 SHA256 的
[0.8.6 社区编译版（PostgreSQL 18 / Windows x64）](https://github.com/andreiramani/pgvector_pgsql_windows/releases/tag/0.8.6_18)。
安装资料位于 `E:\DevTools\pgvector-setup`，DLL 位于 `E:\PostgreSQL\lib`，
控制文件及 SQL 位于 `E:\PostgreSQL\share\extension`。

Python 依赖由 `requirements.txt` 管理；Python 的 pgvector 包与服务器扩展需分别安装。
在新环境按 [pgvector 安装说明](https://github.com/pgvector/pgvector#installation)准备匹配的服务器扩展后，执行：

```powershell
python -m alembic upgrade head
```

迁移 `e93b7a20d541` 负责启用扩展并创建 `document_chunks` 表，本机已完成该迁移。
迁移账号需要相应权限；服务器缺少扩展时会明确报错并回滚。
当前文档记录状态使用已有字段，无需为 RAG 入口新增迁移。

向量表保存正文、向量、用户/产品/文档/片段关联、字符偏移、哈希和模型元数据。
检索采用精确余弦距离及用户/产品/模型/维度的 B-tree 联合索引，
相似度为 `1 - cosine_distance`，范围 -1–1，值越大越相似。
片段序号从 0 开始，字符偏移为解析文本的 `[start_char:end_char]`，不是 PDF 页码。

## 隔离、状态与重试

用户身份取自认证 Session。写入、重试和删除先校验产品及文档归属；
检索 SQL 同时限定 `user_id + product_id`、模型和维度，排除正在删除的资料。
跨用户访问按不存在处理，同一用户的不同产品也分别检索。

| 资料状态 | 含义 |
| --- | --- |
| `uploaded` | 文件已保存，尚无可用向量 |
| `indexed` | 已有成功保存的向量快照；是否匹配当前查询模型仍由检索条件决定 |
| `index_failed` | 首次解析或向量化失败，文件和记录保留供重试 |
| `deleting` | 正在删除，禁止重新入库并从检索中排除 |

入库以文档、模型和维度为单位，在同一事务内替换完整片段并更新 `indexed` 状态。
重复入库不追加重复片段，重新切分不残留该模型/维度的旧片段。
重建失败时保留旧向量，有可用快照则状态仍为 `indexed`，错误通过本次请求返回。

一键上传已保存文件后，解析/输入问题返回 422，配置或模型服务问题返回 503；
这些错误的 `detail` 包含文档 ID 和 `/index` 重试地址，无需重复上传。
若数据库本身不可用，状态更新也可能失败；恢复后可查询资料列表并重试。

删除资料记录时，通过外键 `ON DELETE CASCADE` 清除该文档全部模型的向量。
底层 `delete_document_vectors` 只删除指定模型/维度的向量，保留原文；
删除后没有其他向量时，资料状态回到 `uploaded`。

完整入口使用同步请求，大文档可能耗时较长。远程 Embedding 前结束只读事务，
入库时重新校验归属；这些函数会自行提交/回滚，应使用没有其他未提交修改的请求专属 Session。
进程中断后可重新入库；并发重建最终采用最后一次成功提交的完整快照，模型请求本身不去重。

文档正文和文件名属于不可信输入。后续生成 Agent 应把它们作为资料处理，
引用身份以结构化 `sources` 为准，不把资料中的指令当作系统指令。

## 验证

默认测试使用模拟模型响应和独立数据库；真实 PostgreSQL 测试需显式开启。
测试不向百炼发送产品资料，不能替代真实 API Key 和模型服务的连通性验证。

```powershell
python -m unittest discover -s tests -v
```

启用真实 pgvector 测试，验证模拟 Embedding 与 PostgreSQL 入库、排序、引用和隔离的完整链路：

```powershell
$env:PGVECTOR_INTEGRATION_TEST = "1"
python -m unittest discover -s tests -p test_vector_store_postgres.py -v
Remove-Item Env:PGVECTOR_INTEGRATION_TEST
```

真实数据库测试在随机 schema 的外层事务中创建表和数据，结束后回滚，账号需有创建 schema 的权限。
SQLite 的 JSON 向量字段仅用于离线写入测试，生产检索使用 PostgreSQL + pgvector。

接口细节见 [业务 API 说明](api.md)，实现以 `schemas/rag.py` 和 `/docs` 为准。


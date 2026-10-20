# AI 配图生成

配图入口基于已保存的最新 Content，不接受客户端指定用户、产品、平台、模型地址或图片下载链接。
当前实现小红书配图，后续平台应增加独立的平台配置，不复用小红书 Prompt。

```text
当前用户的最新 Content → 校验 Task / Product 归属
    → 复用产品 RAG → DeepSeek 生成图片 Prompt
    → 千问同步生图 → 下载并校验 PNG → 保存本地文件
    → Content 新版本 → 规则检查 → 等待人工审核
```

## 配置

账号可在“设置 → 模型连接 → 生图模型”维护独立的接口、密钥、模型和支持尺寸，设置账号默认后自动用于配图生成，也可通过 `image_model_id` 选择单次调用连接。支持 Images 兼容同步接口和千问同步接口，详见 [模型连接](model_connections.md)。下面的 `.env` 仅作为未设置用户默认连接时的系统回退。

在项目 `.env` 中添加或使用默认值：

```dotenv
# 留空时使用现有 DASHSCOPE_API_KEY。
QWEN_IMAGE_API_KEY=
QWEN_IMAGE_MODEL=qwen-image-2.0
QWEN_IMAGE_BASE_URL=https://dashscope.aliyuncs.com
QWEN_IMAGE_TIMEOUT_SECONDS=180
```

接入[千问官方同步文生图接口](https://help.aliyun.com/zh/model-studio/qwen-image-api)，
系统回退客户端保留 `qwen-image-2.0`、`qwen-image-2.0-pro` 及现有 2.0 日期版本白名单。用户连接按所选同步协议调用填写的模型 ID，不使用系统客户端的模型白名单；实际模型、尺寸及同步接口能力需由供应商支持，异步接口需另加适配器。
北京和新加坡的 Key 与请求地域须匹配，也支持官方业务空间专属根域名。
生图不读取 `DASHSCOPE_MODEL`、`EMBEDDING_MODEL` 或聊天模型配置。
图片 Prompt 生成使用账号选择或默认的文本模型，RAG 使用账号默认 Embedding，未设置时沿用对应系统配置。
需要模型访问权限与可用额度；本地测试通过模拟响应完成，不自动发送产品资料或触发真实模型费用。

新增依赖为 `Pillow==12.3.0`，用于检查下载图片格式、像素和完整性。
图片关联沿用 Content JSON；用户连接及向量空间字段通过迁移 `f96db2ef345a` 创建。

## 生成接口

```http
POST /contents/10/images/generate
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "style_requirement": "通勤氛围、暖色、简洁留白",
  "count": 1,
  "size": "1536*2048",
  "replace_existing": false
}
```

请求体也可以只传 `{}`。`count` 为 1–4，默认 3:4。前端只显示比例，像素单位单独标为 `px`：1:1 对应 `2048*2048`，3:4 对应 `1536*2048`，4:3 对应 `2048*1536`，16:9 对应 `2048*1152`，9:16 对应 `1152*2048`。这些尺寸满足精确宽高比，千问同步请求仍按 `宽*高` 发送；旧尺寸继续兼容历史请求，不修改已生成图片。

自定义连接只启用其配置中具有精确比例的尺寸；例如 `1024*1536` 属于 2:3，不会标成 3:4。不支持的比例在下拉框中禁用，需配置供应商支持的尺寸或更换模型。尺寸范围依据 [千问官方生图接口](https://help.aliyun.com/zh/model-studio/qwen-image-api)，其他模型应以对应供应商规格为准。
默认追加图片，单个版本最多关联 20 张；`replace_existing=true` 只替换新版本中的图片引用，不删除历史图片。
重复生成必须使用返回的新 Content ID，旧版本会返回 409。

成功返回 201，主要字段：

| 字段 | 含义 |
| --- | --- |
| `content` | 保存后的新版本，包含标题、正文、图片 Prompt、图片引用和生成来源 |
| `images` | 新版本关联的全部图片，包含 `index`、`storage_key`、`download_path` |
| `image_model` / `prompt_model` | 千问生图模型和 DeepSeek Prompt 模型 |
| `request_id` | 供应商本次生图请求标识 |
| `task_status` | 当前真实任务状态 |
| `review_status` | `saved`、`waiting_review` 或 `submission_failed`；兼容旧 `waiting_rule_changes` |
| `rule_check` | 规则检查详情，包括风险与覆盖范围 |
| `workflow` | 已启动 LangGraph 时，返回恢复后的图状态 |
| `review_required` / `notices` | 始终要求人工审核，提供展示给用户的提示 |

`waiting_review` 表示素材已提交人工审核，Task 为 `pending_review`；风险、规则缺失和 AI 检查失败仅提示，不阻止审核。`waiting_rule_changes` 仅兼容旧记录。
工作流内部可使用 `saved` 表示图片已保存、下一节点继续编排。创建任务时可以选择 `output_mode="images"` 单独生图，不要求先生成 AI 文案；选择 `text_images` 则先生成文案再配图。
已使用 LangGraph 的任务自动以 `submit_changes` 恢复审核中断，并检查新版本；不重新实现图节点中的文案生成。
未启动 LangGraph 的任务调用同一规则 Service 提供提示；配图版本的审核和发布也不要求检查结果无风险。
生图接口不创建审核通过记录，也不触发发布。

## 文件读取与 Vue 3

文件位于项目下：

```text
storage/generated_images/user_1/product_2/<随机UUID>.png
```

关联保存的是本地 `storage_key`，不保存供应商临时签名 URL；该 URL 只用于立即下载。
图片和文案、RAG 引用、模型名称、正反向 Prompt、来源 Content ID、请求 ID 一起形成版本记录。

使用 `GET /contents/{content_id}/images/{index}` 读取图片，索引从 0 开始。
接口检查 JWT、内容归属、当前用户及产品目录，响应禁止缓存；不公开挂载整个 `storage/`。
Vue 3 应携带 Authorization 获取 Blob，再通过 `URL.createObjectURL(blob)` 展示；卸载预览时释放对象 URL。
不要把 Token 拼入图片 URL，也不要直接用不带认证头的 `<img src="/contents/...">` 请求。
项目现有 `.gitignore` 已忽略 `storage/`，本地图片不作为项目源码提交。

## 失败和并发

- RAG 没有产品资料时拒绝生图，不能用文案猜测产品事实。
- 生图超时、API 失败、空结果、图片不完整、尺寸不符或保存失败时，不新增 Content；清理本次未提交的图片，保留原文案和原有图片。
- 下载仅接受百炼官方结果桶 HTTPS 地址，不携带接口密钥、不跟随重定向；限制每张 20 MiB 和总像素。
- 生成期间不持有数据库行锁；保存前再次锁住任务，校验任务状态、产品及文案版本，取消或并发修改会丢弃本次图片。
- 同一任务的文案与配图生成互斥。运行尝试保存在 Task checkpoint，进程意外退出后，30 分钟租期到期可重新提交；超时不自动重试付费调用。
- 进程在文件写入与数据库提交之间被强制终止时，可能留下未关联文件，当前不自动扫描删除。任务失败记录不是供应商计费凭证，重试前可核对供应商用量。
- 数据库提交应答异常时先核对本次尝试是否已经保存，已保存则继续审核提交；数据库不可用、无法确认提交状态时保留文件，恢复后先查询最新版本再决定重试。

图片已保存但审核提交异常时，仍返回 201，`review_status="submission_failed"`，并返回新 Content 和 `review_error`。
此时重试审核提交即可，不要再次调用生图：

```http
POST /contents/11/images/submit-review
Authorization: Bearer <access_token>
```

该接口只复用已有图片进行审核提交或复查。若 LangGraph 本身已经执行失败，按工作流说明调用 `/workflow/retry` 恢复中断执行，再查询状态。

## 用户提示与当前边界

前端在生成、预览和审核位置展示响应中的 `notices`：

- 规则检查只提供风险提示，不能保证小红书审核通过。
- 基于爆款案例生成文案或图片，不保证成为下一个爆款，需要人工调整和真实运营数据验证。
- 图片是 AI 创作草稿，产品外观、结构、标识和文字必须人工核对。

当前为文生图，不传入产品参考照片，不保证实物外观一致。资料未描述外观时，Prompt 要求使用不展示具体产品的主题氛围插画。
关闭供应商 Prompt 自动扩写并开启 Qwen-Image 水印；风格输入不能覆盖产品事实约束。
Prompt 的字符限制是项目输入上限，不是精确 Token 计算；供应商仍执行自身的 Token 限制。
现有规则检查仅检查文案、图片 Prompt 和脚本文本，不做实际图片视觉识别、OCR 或产品外观比对。

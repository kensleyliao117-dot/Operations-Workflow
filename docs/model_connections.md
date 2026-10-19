# 用户模型连接

“设置 → 模型连接”分为文本、Embedding、生图三个分类，分别支持新增、编辑、删除、启用及设置账号默认模型。不同类别的默认选择互不影响。用户凭据均加密保存到数据库，不写入共享 `.env`。

“设置 → 模型连接”支持自由填写供应商名称、HTTPS API 根地址、API Key 和模型 ID。名称仅用于展示，不决定调用目标；带独立 URL 的配置统一使用 Chat Completions 兼容协议。

API URL 示例为 `https://api.example.com/v1`，实际请求追加 `/chat/completions`。仅支持公网 HTTPS；拒绝本机、私有和保留地址，不跟随重定向，不读取系统代理。DNS 解析结果在连接时校验并固定，TLS 证书仍按原域名验证。不兼容该协议的接口需要新增适配器，不能仅凭供应商名称转换协议。

## 使用与默认模型

每个用户最多保存 20 个配置，JWT 决定归属，不接受客户端指定 `user_id`。

- 保存不发送模型请求；测试连接发送一条简短请求，可能产生费用。
- API Key 不在列表、编辑响应或测试结果中返回；编辑留空保留，更换 URL 必须重新提交 Key。
- 一个用户最多启用一个默认文本模型，用于案例分析、文案生成、规则辅助检查、图片 Prompt 以及运营经验提炼。
- 案例分析和文案生成请求的 `model_id` 可覆盖本次调用；未指定时采用账号默认模型。
- 未设置默认模型时使用当前账号尚未移除的系统模型，旧配置仍可使用 `.env` 中的服务端凭据。
- 所有模型均可从模型列表删除：自定义模型删除数据库中的配置和加密 Key；系统模型仅从当前账号列表移除，不修改共享 `.env`，可点击“恢复内置模型”恢复。
- 删除不会删除历史文案、图片、分析版本或其中保存的模型信息；删除后的配置不能再用于新的生成。删除全部模型且没有默认模型时，会提示先添加或恢复模型。
- Embedding 与实际生图接口在各自分类中配置，聊天模型切换不改变向量库模型或图片接口。
- 模型不可用时返回错误，不静默切换供应商。历史内容和分析仍保留生成时的模型信息。

## 凭据存储与部署

用户连接保存在 `user_analysis_models`；API Key 使用独立 Fernet 密钥加密并绑定用户 ID，不写入共享 `.env`。URL 和供应商名称为普通配置。

部署密钥使用 `MODEL_CREDENTIALS_KEY` 环境变量；留空时，第一次保存 Key 自动生成 `storage/secrets/model-credentials.key`。该目录已被 `.gitignore` 排除。多实例部署需共享同一个部署密钥。备份或迁移数据库时，必须另外安全备份密钥；丢失后无法恢复原 API Key，需重新录入。更换部署密钥需先完成密文迁移，不能直接覆盖。

加密用于保护数据库中的凭据；有权读取部署密钥的服务器管理员仍能解密。对外部署需要 HTTPS 和受限的服务器文件权限。

执行 `python -m alembic upgrade head` 增加连接地址、密文、默认模型字段和默认模型唯一索引。原有数据不删除；首次部署安装更新后的 `requirements.txt`。

## 接口

| 接口 | 用途 |
| --- | --- |
| `GET /analysis-models` | 系统与当前用户的公开模型配置 |
| `POST /analysis-models` | 创建配置，密钥仅用于写入 |
| `PUT /analysis-models/{id}` | 更新所属配置，Key 可留空保留 |
| `DELETE /analysis-models/{id}` | 删除所属配置，不删除历史分析 |
| `DELETE /analysis-models/system/{model_id}` | 从当前账号移除系统模型 |
| `POST /analysis-models/system/restore` | 恢复当前账号的内置系统模型 |
| `POST /analysis-models/{id}/test` | 使用保存的配置测试文字请求 |

模型设置在保存后立即生效，不需要重启后端；变更默认模型或其地址会使依赖旧模型配置的规则检查缓存失效，需重新检查。

## Embedding 和生图连接

自定义连接使用 `user_resource_models`，每个账号最多保存 40 个；每类最多设置一个默认连接。未设置默认连接时沿用部署端 `.env` 配置。删除默认连接后也沿用该回退方式，不会把其他自定义连接自动设为默认。

- Embedding：填写供应商、HTTPS 根地址、API Key、模型 ID 和实际输出维度（1–4096）。支持 `/embeddings` 兼容协议；不接受 `dimensions` 参数的供应商可取消“请求中发送 dimensions 参数”。“测试连接”会发送一条简短文本并验证向量维度。
- 生图：填写对应连接并选择 Images 兼容协议或千问同步协议，声明供应商实际支持的图片尺寸。Images 调用 `/images/generations`，千问同步调用 `/api/v1/services/aigc/multimodal-generation/generation`。首版接受同步结果中的 PNG 图片 URL 或 Base64，不支持异步任务型图片接口。
- 生图连接保存不调用模型；生成图片才可能产生费用。图片 Prompt 仍使用文本模型；生图失败保留已有文案和图片。图片下载不转发 API Key，限制为公网 HTTPS，不跟随重定向。
- 文档入库与 RAG 查询自动使用当前账号默认 Embedding。生图自动使用账号默认连接，也支持 `image_model_id` 指定单次调用的所属连接。配置类型不匹配、停用或越权引用会被拒绝。
- 向量新增 `embedding_space`：根据协议、根地址、模型和维度计算空间标识，入库和检索同时限定用户、产品、空间、模型与维度。旧向量保留为 `legacy`，不混用；更换接口、模型或维度后，需要在产品资料页面重新入库。仅修改密钥或显示名称不改变空间。
- 删除连接不会删除历史向量、资料或生成图片，历史 Content 保存当时使用的模型信息。API Key 不在读取响应中返回，编辑留空保留；更换 URL 必须重新填写密钥。

| 接口 | 用途 |
| --- | --- |
| `GET /resource-models?kind=embedding` | 当前账号的 Embedding 连接，`kind=image` 查询生图 |
| `POST /resource-models` | 创建连接，`kind` 指定用途 |
| `PUT /resource-models/{id}` | 更新所属连接 |
| `DELETE /resource-models/{id}` | 删除所属连接及加密凭据 |
| `POST /resource-models/{id}/test` | 验证 Embedding 调用与维度 |

迁移 `f96db2ef345a` 创建连接表并扩展向量唯一约束。原有资料、向量和系统环境配置保留。

协议参考：[Embedding 兼容格式](https://developers.openai.com/api/reference/resources/embeddings/methods/create)、[Images 兼容格式](https://developers.openai.com/api/reference/resources/images/methods/generate)、[千问同步接口](https://help.aliyun.com/zh/model-studio/qwen-image-api)。

实现参考：[Fernet](https://cryptography.io/en/stable/fernet/)、[HTTP Core SNI 扩展](https://www.encode.io/httpcore/extensions/)。

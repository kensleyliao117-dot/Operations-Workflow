# 可维护的风险提示规则

检查提供风险原因、文案原文和修改建议。结果不决定 Task、Content 能否保存、提交人工审核或发布，也不保证平台最终审核通过。人工决定是否修改，不自动改写已保存文案。

## 规则维护

`CustomRule` 与 `PlatformRule` 均来自数据库。前端“设置 → 平台规则”支持新增、修改、停用和删除。

| 资源 | 新增/查询 | 修改/删除 |
| --- | --- | --- |
| 自定义规则 | `POST/GET /rules/custom` | `PUT/DELETE /rules/custom/{id}` |
| 平台参考规则 | `POST/GET /rules/platform` | `PUT/DELETE /rules/platform/{id}` |

规则按 JWT 用户隔离，不接受客户端指定归属。新建的平台规则属于当前用户；历史公共参考规则的修改与移除保存为当前用户的覆盖配置，其他用户仍看到自己的规则。删除自定义规则保留历史检查快照；移除公共参考项不删除其他用户的数据。

参数：

- `platform_name`：平台标识，自定义规则留空时可用于所有平台。
- `rule_type`：`ai`、`keyword`、`length`。
- `content`：规则说明或调整建议，AI 规则必填。
- `keywords`：关键词规则的匹配词，至少一个。
- `max_length`：长度规则的字符上限，不代表平台官方限制。
- `targets`：`title/body/tags/image_prompt/video_script/image_assets`。
- `severity`：`low/medium/high`；`is_active` 控制参与检查。
- `source_url`：可选参考来源，不代表规则已获得官方认证。

修改后递增配置版本；历史 ContentRuleCheck 继续保留当次规则和风险快照。可调整平台与用户规则，不在代码中固定运营措辞。

## 检查已保存版本

```http
POST /rules/checks
Authorization: Bearer <token>
Content-Type: application/json

{"content_id": 10, "force": false}
```

先校验内容归属，再读取有效规则，执行关键词/长度匹配和文本模型辅助检查。检查返回：

| status | 含义 |
| --- | --- |
| `no_risk_detected` | 此次配置和模型未发现风险 |
| `risk_detected` | 发现需人工判断的表达 |
| `incomplete` | 规则覆盖不完整，例如没有平台规则 |
| `error` | AI 响应无效、超时或其他检查错误 |

上述状态都不是发布许可或禁止。风险包含检查字段、原文、原因、建议、级别与规则来源；模型不得伪造规则 ID 或原文证据。仅检查文本和图片 Prompt，不声称识别了实际图片像素、OCR 或视频。

`GET /rules/checks?content_id=10` 查询历史记录，`GET /rules/checks/{id}` 查询详情。相同内容、模型与规则快照可复用已完成的结果；`force=true` 自愿重新调用检查模型。失败结果不作为成功缓存。配置变动影响后续提示，不使已通过的人工审核失效。

工作流保存素材后进入 `pending_review`，检查失败保留说明。人工修改新增版本并重新提交；人工审核通过后可以自动发布完整素材，或下载部分素材到平台补充并登记发布结果。并发生成、版本变更、账号归属与重复发布仍分别校验。

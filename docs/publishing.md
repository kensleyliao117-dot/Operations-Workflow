# 小红书本地发布

当前发布适配器通过已开启 CDP 的本机 Chrome 操作[小红书创作服务平台](https://creator.xiaohongshu.com/)，
不构造私有签名，不使用第三方发布代理，不绕过登录、验证码或页面拦截。

```text
已保存文案和图片 → 平台规则检查 → 人工审核 approved
    → 创建发布记录并固定审核版本、账号和素材摘要
    → 本地 Chrome 核对实际登录账号 → 上传图片并填写原文
    → 点击前再次校验 → 点击一次发布 → 保存真实结果
```

## 发布前检查

- JWT 确定项目用户，Task、Content 和 PlatformAccount 均须属于当前用户。
- 任务必须为 `approved`；当前最新 Content 必须具有对应的人工审核通过记录。
- Chrome 发布强制要求当前内容和当前平台规则对应的完整检查结果，普通直接生成的任务也适用。
- 发布记录保存 `content_id`、`content_version`、`review_id`、文案快照和本地图片 SHA-256；执行时及点击前再次校验。
- 发布账号必须显式指定 `account_id`，执行请求还需 `confirm_account=true`。
- `platform_user_id` 使用个人主页 `/user/profile/<24位ID>` 中的 ID，不使用昵称或页面显示的小红书号。
- 只接受当前用户的本地已保存 PNG 图片，最多 20 张为项目输入限制；不直接抓取请求中的任意远程图片 URL。

页面控件、上传预览、正文、标题、发布按钮或账号不能唯一识别时停止发布，不截断或重写已审核内容。
发布操作不生成新 Content，也不改动原文案和图片。话题按已保存标签追加为正文中的 `#话题`，暂不处理平台话题推荐弹窗。

## 初始化

```powershell
python -m alembic upgrade head
python run_local.py
```

迁移 `f082c51b903e` 新增发布快照、阶段和幂等字段，并添加约束：同一任务最多有一个进行中、结果不明或成功的发布记录。
已有发布记录保留，默认仍按 `manual` 人工登记模式处理。
迁移不会自动清理历史重复记录；若约束创建失败，先核对真实发布历史再处理。

Chrome 启动方式及 `storage/chrome-profile/` 目录见 [采集说明](collectors.md)。
需在同一浏览器中登录创作服务平台，不以采集网页的登录态代替发布账号核验。
`CHROME_CDP_URL` 必须指向本机。执行及 Chrome 结果核实接口仅接受本机直连、JWT 和许可的网页来源，拒绝代理转发。
多个用户可共用采集会话；发布会话通过 PostgreSQL advisory lock 在进程间互斥，不能同时操作同一 Chrome。

## 1. 创建发布尝试

```http
POST /publish-records
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "task_id": 1,
  "content_id": 11,
  "platform": "rednote",
  "account_id": 2,
  "publisher_backend": "chrome",
  "visibility": "private",
  "idempotency_key": "publish-request-unique-001"
}
```

`idempotency_key` 为前端生成并在同一次请求重试中复用的 8–100 位字母、数字、下划线或连字符字符串。
`visibility` 可选 `public` / `private`，默认 public。私密测试必须明确传 private，点击前会核对页面为“仅自己可见”；无法确认就停止，不先公开再修改权限。
相同任务、键和参数返回同一记录；同键更换内容或账号返回 409，不新增记录。
返回 201，记录状态为 `publishing`、阶段为 `prepared`，Task 切换为 `publishing`；此时尚未点击平台发布按钮。

## 2. 执行发布

```http
POST /publish-records/3/execute
Authorization: Bearer <access_token>
Content-Type: application/json

{"confirm_account": true}
```

执行器连接 Chrome，新建发布标签页并核对创作中心实际用户 ID；不会自动切换账号。
账号识别使用本机实际页面已观察到的同源账号信息请求 `/api/galaxy/user/info`，仅读取 `data.userId`，
不导出 Cookie、不保存手机号等其他账号资料；该页面接口变更或无法确认身份时停止发布。
上传完成后核对标题、正文、图片预览，点击前再次核对数据库审核版本、账号和文件摘要。
提交前先保存 `delivery_phase=submitting`，再点击一次发布。
仅接受本次页面发布请求返回的明确成功和有效作品 ID，不使用作品列表的第一条推测发布成功。

| publish_status | 含义 | Task |
| --- | --- | --- |
| `publishing` | 已准备或正在执行 | `publishing` |
| `published` | 本次提交明确返回作品 ID，已保存 ID、详情链接和发布时间 | `completed` |
| `failed` | 提交前准备失败，或平台明确拒绝本次提交 | `failed`，保留发布失败阶段 |
| `unknown` | 点击后超时、响应无法确认，或进程中断留下执行标记 | 保持 `publishing`，等待人工核实 |

返回 200 表示已获得该记录的当前处理结果，不代表发布一定成功，前端必须检查 `publish_status`。
已结束或结果不明的同一记录重复执行只返回记录，不会再次点击发布。
共享浏览器忙碌返回 409，应稍后查询或重试同一记录，不创建另一次尝试。

成功表示平台接收并创建作品，不保证审核通过、公开可见或获得流量。
生成图片与文案仍需人工核对，基于案例创作不保证成为爆款，风险检查不保证平台过审。

## 3. 核实不确定结果

`unknown` 禁止直接重发。先在创作中心查看实际账号的作品，确认已发布后补录：

```http
POST /publish-records/3/result
```

```json
{
  "publish_status": "published",
  "account_id": 2,
  "platform_post_id": "0123456789abcdef01234567",
  "platform_post_url": "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
}
```

ID 与链接都传入时必须一致；也可只传其中一个，服务补齐另一个。
已有成功结果不可改成失败或替换成另一篇作品；完全相同的重复回填可安全返回。
Chrome 结果核实同样使用浏览器互斥锁，活跃发布执行中不能通过回填接口提前解除防重限制。

只有明确核实平台没有发布，才能登记失败并解锁下一次尝试：

```json
{
  "publish_status": "failed",
  "account_id": 2,
  "error_message": "已核对指定账号，确认此次提交没有生成作品",
  "confirm_not_published": true
}
```

## 4. 重试明确失败的尝试

```http
POST /publish-records/3/retry
```

```json
{"idempotency_key": "publish-retry-unique-002"}
```

返回新的 `prepared` 记录，旧失败记录不覆盖，再使用新 ID 调用 `/execute`。
同一次重试复用同一个新键；不能复用旧尝试的键。仍需通过版本、规则和账号检查。
不自动处理发布后的回滚、删帖或重新审核；页面需要登录或人工操作时由运营人员处理后再明确重试。

## 查询与反馈

`GET /publish-records/{id}` 和 `GET /publish-records?task_id=1` 返回绑定的账号、Content ID、版本、审核 ID、状态、作品 ID、链接和错误说明。
已有 `/publish-records/{id}/metrics` 继续使用该发布记录与实际账号保存指标；只有成功作品可以登记反馈。
发布成功还会持久记录同步事件，并尝试归档到独立 `OperationsMemory`；经验库故障不会改变发布成功状态。
新运营闭环的指标采集、经验提炼和下一轮检索使用 [独立经验库接口](memory.md)。
所有查询沿用用户隔离。前端不能把共享 Chrome 的采集账号直接当作项目用户或默认发布账号。

## 测试与联调边界

隔离测试覆盖归属、审核版本、图片变化、规则变化、账号确认、重复请求、提交中断、未知结果核实与失败重试。
浏览器适配器使用页面公开 DOM 和本次页面请求的响应。上传预览与发布按钮定位还参考了
[发布适配实现源码](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/publish.go)，这些选择器不构成平台稳定接口。
平台页面变化时可能停止执行，需要在本机真实页面上核对后更新适配器。
2026-10-18 已在真实创作中心验证账号读取、配图上传、文案填充、富文本段落核对和“仅自己可见”设置。
最终发布由人工在页面完成，已只读回查作品 ID、私密标识、正文和图片数量；自动点击发布及提交响应仍未完成真实验证。
人工核实成功结果可提供带时区的 `published_at`，须为真实历史时间，省略时使用登记时刻；失败或结果不明不得提供发布时间，已登记时间不得覆盖。
演练中的旧失败记录保留，已核实作品通过新记录登记，后续指标和运营经验使用成功记录关联，不再次发送帖子。

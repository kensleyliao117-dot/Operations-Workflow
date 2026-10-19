# 多用户注册、登录与产品资料

## 首次启动

```powershell
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app:app --reload
```

`.env` 必须有 PostgreSQL `DATABASE_URL` 和随机生成的 `JWT_SECRET_KEY`（至少 32 字节）。
当前本地 `.env` 已生成独立随机密钥，不应提交到 Git；换环境请生成自己的密钥。
缺少密钥时应用拒绝启动。轮换密钥会使旧令牌失效。

## 注册与登录

`POST /auth/register`：

```json
{
  "username": "alice",
  "password": "use-a-long-unique-password"
}
```

用户名 3–100 字符，仅字母、数字、下划线、点和短横线，统一小写，不区分大小写。
密码 12–128 字符，不裁剪空格。邮箱可选，不需要另填 name。响应不包含密码哈希。

`POST /auth/login`：

```json
{"username": "alice", "password": "use-a-long-unique-password"}
```

返回 `access_token`、`token_type=bearer`、`expires_in=3600`。
Vue 的 API 客户端在业务请求中附加：

```http
Authorization: Bearer <access_token>
```

Swagger `/docs`：先登录，复制 access_token，再点 Authorize 粘贴 Token。
前端可以先在内存保存 Token，刷新后重新登录；收到 401 清理登录状态。
当前不提供刷新 Token 或密码找回接口。

`POST /auth/logout` 带上当前 Token，返回 204。后端持久撤销当前会话，前端同时清理 Token。
同一账户的其他登录会话不受影响，已退出令牌再次访问返回 401。

## 用户与数据归属

- 已移除公开的 `/users` 创建、用户列表和用户详情接口；注册只能走 `/auth/register`。
- 除健康检查、注册、登录和 API 文档外，业务接口均要求认证。
- 产品、资料、任务、内容、审核、发布记录、Prompt、自定义规则、小红书案例与分析按用户隔离。
- 跨用户查询或修改返回 404；列表只返回自己的数据。
- `user_id`、`reviewer_id` 由认证身份决定，创建请求不接受这些字段。
- 共享平台规则允许登录用户读取，只有 `users.is_admin=true` 的管理员可维护。
  注册不接受管理员权限字段，管理员由部署者通过可信数据库管理渠道设置。
- 旧用户、旧任务保留，不设置默认密码。旧案例 owner 为空，不会分配给新用户。
  旧数据如需迁移到新账号，需要管理员明确决定归属。

## 产品资料与任务

1. `POST /products`，例如 `{"name":"保温杯","description":"产品说明"}`。
2. `POST /products/{id}/documents`，使用 `multipart/form-data` 的 `file` 字段上传。
   支持 PDF/TXT/MD/DOCX，单文件最多 20 MB；TXT/MD 使用 UTF-8。多份资料可分多次上传。
3. `GET /products/{id}/documents` 查看资料。
4. `GET /products/{id}/documents/{document_id}/download` 认证下载。
5. `POST /tasks` 提交自己的 `product_id` 和运营需求。

产品维护接口：

- `PATCH /products/{id}`：部分修改 `name`、`description`。名称不能置空，描述可用 null 清除。
- `DELETE /products/{id}`：删除自己的空产品，成功返回 204。有关联任务或资料时返回 409，保留业务历史。
- `DELETE /products/{id}/documents/{document_id}`：删除自己的资料记录和对应文件，成功返回 204。
  文件删除失败时返回 503，资料状态为 `deleting`，禁止下载；可重试同一删除接口。
  资料已被明确标记删除，但数据库最终删除失败时，同样可重试完成清理。

以上接口均要求 JWT；跨用户修改、删除返回 404。
当前未建立 RAG 索引；未来接入向量库后，资料删除还需同步清理切片与向量。

文件使用随机名称存放于 `{UPLOAD_DIR}/{user_id}/{product_id}/`，默认根目录为项目的 `storage/uploads/`。
不公开挂载上传目录为静态目录。
客户端文件名仅用于展示；下载通过用户归属校验。不根据客户端输入读取任意磁盘路径。
上传状态为 `uploaded`；`rag.document_loader.load_document()` 已支持读取资料文本，但上传后尚不自动解析。
文本切分和千问向量化模块已实现，均需显式调用；向量入库和 RAG 检索尚未实现，仍不能自动生成营销内容。调用方式见 [rag.md](rag.md)。
后续 RAG 必须同时用 `user_id` 和 `product_id` 限定资料来源。

### 自定义上传目录

在项目 `.env` 设置并重启服务：

```dotenv
UPLOAD_DIR=storage/uploads
```

也可以使用绝对路径，例如 `UPLOAD_DIR=D:/OperationsData/uploads`。
相对路径始终以项目根目录为基准，省略或留空使用默认值。
上传、下载、删除和 RAG 文本读取统一使用该配置；生成图片的存储目录不受影响。
数据库 `storage_key` 继续使用 `uploads/{user_id}/{product_id}/{filename}` 的逻辑标识，无需迁移数据库。
切换到新目录时，需要把原上传目录中的用户子目录复制到新目录后再切换；不会自动搬动旧文件。

## 认证实现

密码使用 Argon2 哈希；JWT 固定 HS256，并验证签名、签发方、接收方、有效期及持久会话。
每次登录都有独立随机会话 ID，数据库只存它的 SHA256 和有效期、撤销时间。
实现参考 [FastAPI JWT 示例](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)、
[PyJWT 文档](https://pyjwt.readthedocs.io/en/stable/usage.html)。

跨用户与认证集成测试：`python -m unittest discover -s tests -v`。

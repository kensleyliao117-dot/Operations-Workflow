# 可替换的 UI 素材

| 路径                               | 用途                 | 建议比例 |
| ---------------------------------- | -------------------- | -------- |
| `brand/logo.svg`                   | 导航与登录页品牌图标 | 1:1      |
| `brand/favicon.svg`                | 浏览器标签图标       | 1:1      |
| `illustrations/auth.svg`           | 登录、注册页插图     | 约 11:6  |
| `illustrations/empty-products.svg` | 产品空状态           | 4:3      |
| `illustrations/empty-content.svg`  | 内容空状态           | 4:3      |
| `placeholders/image.svg`           | 图片加载占位         | 4:3      |

可以直接替换同名文件；更换为 PNG、WebP 等格式时，修改 `src/config/ui.js` 对应路径。
favicon 路径在 `index.html` 中。资源根路径可通过 `VITE_UI_ASSET_BASE` 设置。
界面配色和布局由 `src/styles/` 管理，不必修改 API 层。

此目录只存公开 UI 素材，产品资料与 AI 生成图仍通过后端鉴权接口访问。

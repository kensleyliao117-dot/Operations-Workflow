const base = (import.meta.env.VITE_UI_ASSET_BASE || '/ui').replace(/\/$/, '')

// 页面只使用资源语义名称；更换品牌和插图时集中修改此处或替换 public/ui 文件。
export const ui = Object.freeze({
  appName: 'Operations Workflow',
  appSubtitle: '内容运营工作台',
  assets: {
    logo: `${base}/brand/logo.svg`,
    auth: `${base}/illustrations/auth.svg`,
    products: `${base}/illustrations/empty-products.svg`,
    content: `${base}/illustrations/empty-content.svg`,
    imageFallback: `${base}/placeholders/image.svg`,
  },
})

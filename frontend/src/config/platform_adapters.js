import { defineAsyncComponent } from 'vue'

// 每个平台显式注册业务组件，缺少适配器时不回退到其他平台的采集或发布界面。
export const platformAdapters = Object.freeze({
  rednote: {
    cases: defineAsyncComponent(() => import('../components/CaseLibrary.vue')),
    publishing: defineAsyncComponent(() => import('../components/PublishingPanel.vue')),
  },
})

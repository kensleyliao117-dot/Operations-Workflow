<script setup>
import { ui } from '../config/ui.js'
</script>
<template>
  <div class="auth-card">
    <aside class="auth-aside">
      <div>
        <div class="brand">
          <img :src="ui.assets.logo" alt="" />
          <span>{{ ui.appName }}</span>
        </div>
        <h2>
          从产品资料，
          <br />
          到下一篇好内容。
        </h2>
        <p>
          收集灵感、生成草稿、人工审核，
          <br />
          让每一步内容运营都有据可循。
        </p>
      </div>
      <img class="illustration" :src="ui.assets.auth" alt="" />
      <small class="muted">产品管理 / 内容生成 / 审核发布</small>
    </aside>
    <section class="auth-form"><slot /></section>
  </div>
</template>

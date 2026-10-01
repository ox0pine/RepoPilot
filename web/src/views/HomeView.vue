<script setup lang="ts">
import BrandMark from '../components/BrandMark.vue'

defineEmits<{ navigateAuth: [] }>()
</script>

<template>
  <div class="public-page">
    <header class="public-header">
      <a href="/" aria-label="RepoPilot 首页"><BrandMark /></a>
      <a class="btn-ghost" href="/auth" @click.prevent="$emit('navigateAuth')">进入工作台</a>
    </header>
    <main id="main" tabindex="-1">
      <section class="home-intro" aria-labelledby="intro-title">
        <p class="muted">面向 Python 仓库的代码任务工作台</p>
        <h1 id="intro-title"><span>从一个 Issue，</span><span>推进一次代码改动。</span></h1>
        <p class="home-description">让 AI 在隔离工作区中修复问题、实现功能。通过代码差异和验证报告审查结果，再决定如何交付。</p>
        <a class="btn-primary" href="/auth" @click.prevent="$emit('navigateAuth')">开始使用</a>
      </section>
      <ol class="home-steps" aria-label="任务流程">
        <li><span class="step-number">01</span><h2>准备任务</h2><p>连接 Git 平台，填写仓库、提交 SHA 和 Issue，核对执行方案。</p></li>
        <li><span class="step-number">02</span><h2>跟进修改</h2><p>查看代码修改、测试结果和执行进展，处理需要确认的操作。</p></li>
        <li><span class="step-number">03</span><h2>审查交付</h2><p>审查代码差异和验证报告，下载补丁或批准发布到 Git 平台。</p></li>
      </ol>
      <p class="execution-boundary">开始前需由管理员配置运行环境与验收方案。发布由你批准，PR / MR 的审查与合并在 Git 平台完成。</p>
    </main>
  </div>
</template>

<style scoped>
.public-page { width: min(960px, 100%); margin: auto; padding: 0 24px; }
.public-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 64px; }
.home-intro { padding: 72px 0 48px; }
h1 { font-size: clamp(21px, 6.5vw, 32px); line-height: 1.5; font-weight: 600; margin: 16px 0; }
h1 span { display: block; white-space: nowrap; }
.home-description { max-width: 580px; color: var(--text-secondary); margin-bottom: 24px; }
.home-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; list-style: none; padding: 24px 0; border-top: 1px solid var(--border); }
.step-number { display: block; margin-bottom: 8px; color: var(--text-muted); font-size: 11px; }
.home-steps h2 { font-size: 13px; font-weight: 600; }
.home-steps p, .execution-boundary { color: var(--text-secondary); }
.execution-boundary { border-top: 1px solid var(--border); padding: 16px 0; font-size: 12px; }
@media (max-width: 640px) { .public-page { padding: 0 16px; } .home-intro { padding: 40px 0 32px; } .home-steps { grid-template-columns: 1fr; gap: 12px; } }
</style>

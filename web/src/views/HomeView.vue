<script setup lang="ts">
import { NButton, NCard } from 'naive-ui'
import { Activity, ArrowRight, GitPullRequest, ListChecks } from '@lucide/vue'
import BrandMark from '../components/BrandMark.vue'

const emit = defineEmits<{ navigateAuth: [] }>()
const steps = [
  { number: '01', title: '固定来源与批准目标', description: '固定仓库、commit 与 Issue，查看源码上下文并人工批准目标。', icon: ListChecks },
  { number: '02', title: '查看工具结果和检查记录', description: '显式开始执行，查看实际工具日志、基线与最终检查结果。', icon: Activity },
  { number: '03', title: '查看差异并下载 Patch', description: '审阅代码差异和报告，下载 Patch 后由你决定如何交付。', icon: GitPullRequest },
]
</script>

<template>
  <div class="public-page">
    <header class="public-header">
      <a class="brand-link" href="/" aria-label="RepoPilot 首页"><BrandMark /></a>
      <NButton attr-type="button" class="header-action" @click="emit('navigateAuth')">进入工作台</NButton>
    </header>
    <main id="main" tabindex="-1">
      <section class="home-hero" aria-labelledby="intro-title">
        <div class="home-intro">
          <p class="eyebrow">RepoPilot · 代码任务工作台</p>
          <h1 id="intro-title">让代码任务<br>有序推进</h1>
          <p class="home-description">面向 Python、React 与 Vue 项目的代码任务工作台</p>
          <NButton type="primary" attr-type="button" class="hero-action" @click="emit('navigateAuth')">
            进入工作台
            <template #icon><ArrowRight :size="18" :stroke-width="1.75" aria-hidden="true" /></template>
          </NButton>
        </div>
      </section>
      <section class="workflow-section" aria-labelledby="workflow-title">
        <div class="section-heading">
          <h2 id="workflow-title">从目标到成果审阅</h2>
          <p>执行需先准备 Docker 环境，结果仍需人工审阅。</p>
        </div>
        <ol class="home-steps" aria-label="任务流程">
          <li v-for="step in steps" :key="step.number">
            <NCard class="step-card">
              <div class="step-heading">
                <component :is="step.icon" :size="20" :stroke-width="1.75" aria-hidden="true" />
              </div>
              <p class="step-number">{{ step.number }}</p>
              <h3>{{ step.title }}</h3>
              <p class="step-description">{{ step.description }}</p>
            </NCard>
          </li>
        </ol>
      </section>
      <footer class="execution-boundary">批准不会自动执行；每次执行从固定 commit 重新开始。检查通过不代表独立验收，代码差异与报告仍需人工审阅。</footer>
    </main>
  </div>
</template>

<style scoped>
.public-page { max-width: 1168px; margin: 0 auto; padding: 0 24px; }
.public-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; height: 64px; border-bottom: 1px solid var(--border-soft); }
.brand-link { display: inline-flex; flex-shrink: 0; }
.home-hero { padding: 72px 0 64px; }
.eyebrow { margin: 0 0 16px; color: var(--accent); font-size: 13px; font-weight: 600; }
h1 { margin: 0; font-size: clamp(36px, 4.5vw, 56px); line-height: 1.2; letter-spacing: -0.035em; font-weight: 650; }
.home-description { max-width: 440px; margin: 24px 0 32px; color: var(--text-secondary); font-size: 17px; }
.hero-action { min-height: 44px; }
.workflow-section { padding: 32px 0; border-top: 1px solid var(--border-soft); }
.section-heading { margin-bottom: 24px; }
.section-heading h2 { margin: 0 0 8px; font-size: 20px; font-weight: 600; }
.section-heading p { margin: 0; color: var(--text-secondary); }
.home-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; padding: 0; margin: 0; list-style: none; }
.home-steps li { display: flex; min-width: 0; }
.step-card { height: 100%; }
.step-heading { display: flex; align-items: center; justify-content: space-between; color: var(--text-secondary); }
.step-number { margin: 24px 0 4px; font-size: 12px; color: var(--text-muted); }
h3 { margin: 0 0 8px; font-size: 18px; font-weight: 600; }
.step-description { margin: 0; color: var(--text-secondary); }
.execution-boundary { padding: 24px 0 32px; border-top: 1px solid var(--border-soft); color: var(--text-secondary); font-size: 13px; }
@media (max-width: 768px) {
  .public-page { padding: 0 16px; }
  .home-hero { padding: 40px 0 32px; }
  .home-description { margin: 16px 0 24px; font-size: 16px; }
  .home-steps { grid-template-columns: minmax(0, 1fr); gap: 16px; }
  .header-action { min-height: 44px; }
}
</style>

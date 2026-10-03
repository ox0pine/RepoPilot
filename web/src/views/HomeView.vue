<script setup lang="ts">
import { NButton, NCard } from 'naive-ui'
import { Activity, ArrowRight, GitPullRequest, ListChecks } from '@lucide/vue'
import BrandMark from '../components/BrandMark.vue'

const emit = defineEmits<{ navigateAuth: [] }>()
const steps = [
  { number: '01', title: '确定任务来源', description: '选择仓库、固定 commit 与 Issue，基于明确的代码版本生成方案。', icon: ListChecks },
  { number: '02', title: '批准并执行', description: '审阅方案后启动执行，随时查看工具输出、检查结果与执行记录。', icon: Activity },
  { number: '03', title: '审阅并交付代码', description: '核对代码变更与报告，确认后推送修复分支，再前往 GitHub 手动合并。', icon: GitPullRequest },
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
          <h1 id="intro-title">从 Issue 到<br>可审阅的代码变更</h1>
          <p class="home-description">生成实施方案、执行代码修改，并通过修复分支交付结果。</p>
          <NButton type="primary" attr-type="button" class="hero-action" @click="emit('navigateAuth')">
            进入工作台
            <template #icon><ArrowRight :size="18" :stroke-width="1.75" aria-hidden="true" /></template>
          </NButton>
        </div>
      </section>
      <section class="workflow-section" aria-labelledby="workflow-title">
        <div class="section-heading">
          <h2 id="workflow-title">清晰推进每一项任务</h2>
          <p>每次执行都从固定 commit 开始，结果由你审阅和交付。</p>
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
      <footer class="execution-boundary">支持 Python、React 和 Vue 项目。</footer>
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

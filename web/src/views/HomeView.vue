<script setup lang="ts">
import { NButton, NCard, NTag } from 'naive-ui'
import { Activity, ArrowRight, GitPullRequest, ListChecks } from '@lucide/vue'
import BrandMark from '../components/BrandMark.vue'

const emit = defineEmits<{ navigateAuth: [] }>()
const plannedSteps = [
  { number: '01', title: '描述任务', description: '明确目标、项目背景与验收要求，让下一步有据可依。', icon: ListChecks },
  { number: '02', title: '观察执行', description: '了解任务推进过程，在需要时补充反馈与调整方向。', icon: Activity },
  { number: '03', title: '审查变更', description: '结合代码差异与验证结果，审查改动并决定如何交付。', icon: GitPullRequest },
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
          <h2 id="workflow-title">下一步，让任务连成流程</h2>
          <p>以下流程正在规划，尚未开放。</p>
        </div>
        <ol class="home-steps" aria-label="规划中的任务流程">
          <li v-for="step in plannedSteps" :key="step.number">
            <NCard class="step-card">
              <div class="step-heading">
                <component :is="step.icon" :size="20" :stroke-width="1.75" aria-hidden="true" />
                <NTag size="small" :bordered="false">规划中</NTag>
              </div>
              <p class="step-number">{{ step.number }}</p>
              <h3>{{ step.title }}</h3>
              <p class="step-description">{{ step.description }}</p>
            </NCard>
          </li>
        </ol>
      </section>
      <footer class="execution-boundary">当前可用：登录、模型设置与 GitHub 授权。任务执行与流式对话建设中。</footer>
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

<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NSkeleton } from 'naive-ui'
import { ArrowUpRight, CheckCircle2, CircleAlert, MessagesSquare, Plus, Settings, Target } from '@lucide/vue'
import { followLink } from '../router'
import { refreshTasks, taskStore } from '../stores/tasks'
import type { TaskSummary } from '../api/tasks'

const emit = defineEmits<{ settings: [] }>()
const cards = computed(() => [
  { label: '全部对话', value: taskStore.stats?.total, icon: MessagesSquare, tone: 'default' },
  { label: '待批准目标', value: taskStore.stats?.awaiting_approval, icon: Target, tone: 'warning' },
  { label: '已批准目标', value: taskStore.stats?.approved, icon: CheckCircle2, tone: 'success' },
  { label: '生成失败', value: taskStore.stats?.generation_failed, icon: CircleAlert, tone: 'danger' },
])
const recentTasks = computed(() => taskStore.items.slice(0, 5))
const statusLabels: Record<TaskSummary['status'], string> = {
  draft: '待生成', generating: '生成中', awaiting_approval: '待批准',
  approved: '已批准', generation_failed: '生成失败',
}
function repositoryName(url: string): string {
  return url.replace('https://github.com/', '')
}
function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}
</script>

<template>
  <div class="dashboard">
    <header class="dashboard-header">
      <div><p class="eyebrow">RepoPilot</p><h1>工作台</h1><p class="description">从 Issue 开始，生成目标并完成人工审批。</p></div>
      <a class="new-link" href="/app/new" @click="followLink($event, '/app/new')"><Plus :size="18" aria-hidden="true" />新建对话</a>
    </header>
    <section aria-label="对话统计" class="stats-section" :aria-busy="taskStore.statsLoading">
      <div v-if="taskStore.statsError" class="error-state" role="alert"><div><h2>统计暂时无法读取</h2><p>{{ taskStore.statsError }}</p></div><NButton :loading="taskStore.statsLoading" @click="refreshTasks">重试</NButton></div>
      <div v-else class="stats-grid">
        <article v-for="card in cards" :key="card.label" class="stat-card">
          <div class="stat-heading"><h2>{{ card.label }}</h2><span class="stat-icon" :class="card.tone"><component :is="card.icon" :size="20" :stroke-width="1.75" aria-hidden="true" /></span></div>
          <NSkeleton v-if="taskStore.statsLoading && !taskStore.stats" width="72px" height="38px" :sharp="false" />
          <p v-else class="stat-value">{{ card.value ?? '—' }}</p>
        </article>
      </div>
    </section>
    <section class="preparation-card" aria-labelledby="preparation-title">
      <span class="preparation-icon"><Settings :size="22" :stroke-width="1.75" aria-hidden="true" /></span>
      <div><h2 id="preparation-title">模型与 GitHub 配置</h2><p>生成目标需要可用的模型服务与 GitHub 访问权限，可在设置中查看或调整。</p></div>
      <NButton class="settings-button" attr-type="button" aria-haspopup="dialog" @click="emit('settings')">打开设置</NButton>
    </section>
    <section class="recent-card" aria-labelledby="dashboard-recent-title" :aria-busy="taskStore.listLoading">
      <header class="section-header"><h2 id="dashboard-recent-title">最近对话</h2><span>最近更新的 5 条</span></header>
      <div v-if="taskStore.listLoading && !recentTasks.length" class="recent-skeleton" role="status" aria-label="正在加载最近对话"><NSkeleton v-for="index in 3" :key="index" height="64px" :sharp="false" /></div>
      <div v-if="taskStore.listError" class="error-state recent-error" role="alert"><div><h3>最近对话暂时无法读取</h3><p>{{ taskStore.listError }}</p></div><NButton :loading="taskStore.listLoading" @click="refreshTasks">重试</NButton></div>
      <div v-if="recentTasks.length" class="recent-list">
        <a v-for="task in recentTasks" :key="task.id" class="recent-row" :href="`/app/tasks/${task.id}`" @click="followLink($event, `/app/tasks/${task.id}`)">
          <span class="task-copy"><span class="task-title" :title="task.title">{{ task.title }}</span><span class="task-repository">{{ repositoryName(task.repository_url) }}</span></span>
          <span class="task-state" :class="task.status">{{ statusLabels[task.status] }}</span>
          <time :datetime="task.updated_at">{{ formatDate(task.updated_at) }}</time>
          <ArrowUpRight class="row-arrow" :size="18" aria-hidden="true" />
        </a>
      </div>
      <div v-else-if="!taskStore.listLoading && !taskStore.listError" class="empty-state"><MessagesSquare :size="32" :stroke-width="1.5" aria-hidden="true" /><h3>还没有对话</h3><p>填写仓库、commit 和 Issue，开始审阅你的第一个目标。</p><a class="new-link" href="/app/new" @click="followLink($event, '/app/new')"><Plus :size="18" aria-hidden="true" />新建对话</a></div>
    </section>
    <p class="scope-note">批准仅确认目标，不会执行代码。代码执行尚未接入。</p>
  </div>
</template>

<style scoped>
.dashboard { width: 100%; max-width: 1120px; margin: 0 auto; }
.dashboard-header { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.eyebrow { margin: 0 0 8px; color: var(--text-muted); font-size: 12px; letter-spacing: .05em; }
h1 { margin: 0; color: var(--text); font-size: 28px; line-height: 1.3; }
.description { margin: 8px 0 0; color: var(--text-secondary); }
.new-link { display: inline-flex; align-items: center; justify-content: center; gap: 8px; flex-shrink: 0; min-height: 44px; box-sizing: border-box; padding: 8px 16px; border-radius: 8px; background: var(--accent); color: var(--surface); text-decoration: none; font-weight: 600; }
.new-link:hover { background: var(--accent-hover); color: var(--surface); }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.stats-section { margin-bottom: 24px; }
.stats-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.stat-card { padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.stat-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; }
h2 { margin: 0; font-size: 16px; font-weight: 600; color: var(--text); }
.stat-heading h2 { font-size: 14px; color: var(--text-secondary); }
.stat-icon { display: inline-flex; padding: 8px; border-radius: 10px; background: var(--accent-soft); color: var(--accent); }
.stat-icon.warning { background: var(--warning-bg); color: var(--warning-text); }
.stat-icon.success { background: var(--success-bg); color: var(--success-text); }
.stat-icon.danger { background: var(--danger-bg); color: var(--danger-text); }
.stat-value { margin: 0; font-size: 32px; line-height: 38px; font-weight: 600; color: var(--text); }
.preparation-card { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.preparation-icon { display: inline-flex; flex-shrink: 0; color: var(--text-muted); }
.preparation-card > div { flex: 1; min-width: 0; }
.preparation-card p { margin: 4px 0 0; color: var(--text-secondary); font-size: 13px; }
.settings-button { min-height: 40px; flex-shrink: 0; }
.recent-card { overflow: hidden; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.section-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 20px 24px; border-bottom: 1px solid var(--border-soft); }
.section-header > span { font-size: 12px; color: var(--text-muted); }
.recent-row { display: flex; align-items: center; gap: 16px; min-height: 76px; box-sizing: border-box; padding: 16px 24px; color: var(--text); text-decoration: none; border-bottom: 1px solid var(--border-soft); }
.recent-row:last-child { border-bottom: 0; }
.recent-row:hover { background: var(--surface-hover); }
.recent-row:focus-visible { outline-offset: -3px; }
.task-copy { display: flex; flex-direction: column; flex: 1; min-width: 0; gap: 4px; }
.task-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.task-repository { color: var(--text-muted); font-size: 12px; overflow-wrap: anywhere; }
.task-state { flex-shrink: 0; padding: 3px 8px; border-radius: 6px; background: var(--bg); color: var(--text-secondary); font-size: 12px; }
.task-state.awaiting_approval { background: var(--warning-bg); color: var(--warning-text); }
.task-state.approved { background: var(--success-bg); color: var(--success-text); }
.task-state.generation_failed { background: var(--danger-bg); color: var(--danger-text); }
.task-state.generating { background: var(--accent-soft); color: var(--accent); }
time { color: var(--text-muted); font-size: 12px; white-space: nowrap; }
.row-arrow { flex-shrink: 0; color: var(--text-muted); }
.recent-skeleton { display: flex; flex-direction: column; gap: 16px; padding: 24px; }
.empty-state { display: flex; flex-direction: column; align-items: center; padding: 40px 24px; color: var(--text-muted); text-align: center; }
h3 { margin: 12px 0 0; font-size: 16px; color: var(--text); }
.empty-state p { margin: 8px 0 24px; }
.error-state { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.error-state h3 { margin: 0; }
.error-state p { margin: 4px 0 0; color: var(--danger-text); overflow-wrap: anywhere; }
.recent-error { margin: 16px 24px; background: var(--danger-bg); }
.scope-note { margin: 16px 0 0; color: var(--text-muted); font-size: 12px; }
@media (max-width: 1100px) { .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 768px) { h1 { font-size: 24px; } .dashboard-header { align-items: flex-start; } .preparation-card { flex-wrap: wrap; } .settings-button { margin-left: 38px; } .recent-row { gap: 12px; padding: 16px; flex-wrap: wrap; } .task-copy { flex-basis: calc(100% - 100px); } time { width: calc(100% - 30px); } .section-header { padding: 16px; } .recent-error { margin: 16px; } }
@media (max-width: 480px) { .stats-grid { grid-template-columns: minmax(0, 1fr); } .dashboard-header { flex-wrap: wrap; gap: 16px; } .stat-card { padding: 16px 20px; } .error-state { align-items: flex-start; flex-direction: column; } }
</style>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { NButton, NSkeleton } from 'naive-ui'
import { ListChecks, Plus } from '@lucide/vue'
import { followLink } from '../router'
import { loadMoreTasks, refreshTasks, setTaskFilter, taskStore } from '../stores/tasks'
import { formatDateTime } from '../lib/time'
import type { TaskStats, TaskSummary } from '../api/tasks'

const statusLabels: Record<TaskSummary['status'], string> = {
  draft: '待生成方案', generating: '方案生成中', awaiting_approval: '待批准',
  approved: '已批准', generation_failed: '生成失败',
}
const statusTones: Record<TaskSummary['status'], string> = {
  draft: 'muted', generating: 'accent', awaiting_approval: 'warning',
  approved: 'success', generation_failed: 'danger',
}
// Re-read on entry: task detail pages update the store, but the list can be stale after idle time.
onMounted(() => { void refreshTasks() })
const summary = computed(() => {
  const stats = taskStore.stats
  if (!stats) return ''
  const parts = [`${stats.total} 项任务`]
  if (stats.awaiting_approval) parts.push(`${stats.awaiting_approval} 项待批准`)
  if (stats.generation_failed) parts.push(`${stats.generation_failed} 项生成失败`)
  if (stats.approved) parts.push(`${stats.approved} 项已批准`)
  return parts.join(' · ')
})
const filterLabels: Array<[TaskSummary['status'], string]> = [
  ['awaiting_approval', '待批准'], ['generation_failed', '生成失败'], ['generating', '方案生成中'],
  ['draft', '待生成'], ['approved', '已批准'],
]
// Only statuses that actually have tasks right now; the row stays quiet otherwise.
const filterOptions = computed(() => {
  const stats: TaskStats | null = taskStore.stats
  if (!stats) return []
  const options: Array<{ value: TaskSummary['status'] | null; label: string; count: number }> = [
    { value: null, label: '全部', count: stats.total },
  ]
  for (const [value, label] of filterLabels) {
    const count = stats[value]
    if (count > 0) options.push({ value, label, count })
  }
  return options
})
// The label must outlive the option: a task can leave the filter while it is selected.
const activeFilterLabel = computed(() => filterLabels.find(([value]) => value === taskStore.filter)?.[1] ?? '')
function repositoryName(url: string): string {
  return url.replace('https://github.com/', '')
}
</script>

<template>
  <div class="task-list">
    <header class="list-header">
      <div class="list-heading">
        <h1>任务</h1>
        <p class="description">{{ summary || '每项任务固定一个仓库、一个 commit 和一个 Issue。' }}</p>
      </div>
      <div class="list-actions">
        <NButton quaternary size="small" :loading="taskStore.listLoading" :disabled="taskStore.moreLoading" @click="refreshTasks">刷新</NButton>
        <a class="new-link" href="/app/new" @click="followLink($event, '/app/new')"><Plus :size="16" :stroke-width="1.5" aria-hidden="true" />新建任务</a>
      </div>
    </header>

    <nav v-if="filterOptions.length > 1" class="filters" aria-label="按状态筛选">
      <button v-for="option in filterOptions" :key="option.label" type="button" :class="{ active: taskStore.filter === option.value }" :aria-pressed="taskStore.filter === option.value" @click="setTaskFilter(option.value)">
        {{ option.label }}<span class="count">{{ option.count }}</span>
      </button>
    </nav>

    <section class="list-card" aria-label="任务列表" :aria-busy="taskStore.listLoading || taskStore.moreLoading">
      <div v-if="taskStore.listError" class="error-state" role="alert">
        <p>{{ taskStore.listError }}</p>
        <NButton size="small" :loading="taskStore.listLoading" @click="refreshTasks">重新加载</NButton>
      </div>

      <div v-if="taskStore.listLoading && !taskStore.items.length" class="skeleton" role="status" aria-label="正在加载任务">
        <NSkeleton v-for="index in 3" :key="index" height="72px" :sharp="false" />
      </div>

      <nav v-else-if="taskStore.items.length" class="rows" aria-label="任务">
        <a v-for="task in taskStore.items" :key="task.id" class="row" :href="`/app/tasks/${task.id}`" @click="followLink($event, `/app/tasks/${task.id}`)">
          <span class="row-copy">
            <span class="row-title" :title="task.title">{{ task.title }}</span>
            <span class="row-repository">{{ repositoryName(task.repository_url) }}</span>
          </span>
          <span class="row-meta">
            <span class="status" :data-tone="statusTones[task.status]">{{ statusLabels[task.status] }}</span>
            <time :datetime="task.updated_at">{{ formatDateTime(task.updated_at) }}</time>
          </span>
        </a>
      </nav>

      <div v-else-if="!taskStore.listError && taskStore.filter" class="empty-state">
        <ListChecks :size="18" :stroke-width="1.5" aria-hidden="true" />
        <h3>没有{{ activeFilterLabel }}的任务</h3>
        <p>当前筛选下没有任务，其他状态仍有 {{ (taskStore.stats?.total ?? 0) - (taskStore.stats?.[taskStore.filter] ?? 0) }} 项。</p>
        <NButton size="small" @click="setTaskFilter(null)">查看全部任务</NButton>
      </div>

      <div v-else-if="!taskStore.listError" class="empty-state">
        <ListChecks :size="18" :stroke-width="1.5" aria-hidden="true" />
        <h3>还没有任务</h3>
        <p>选择仓库、基准 commit 和一个 Issue，生成可审阅的方案。</p>
        <a class="new-link" href="/app/new" @click="followLink($event, '/app/new')"><Plus :size="16" :stroke-width="1.5" aria-hidden="true" />新建任务</a>
      </div>

      <div v-if="taskStore.nextCursor" class="load-more">
        <NButton size="small" :loading="taskStore.moreLoading" :disabled="taskStore.listLoading" @click="loadMoreTasks">加载更多</NButton>
      </div>
    </section>

  </div>
</template>

<style scoped>
.task-list { width: 100%; max-width: var(--content-width); }
.list-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 24px; }
.list-heading { min-width: 0; }
.list-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
h1 { margin: 0; font-size: 26px; font-weight: 500; line-height: 1.3; }
.description { margin: 4px 0 0; color: var(--text-secondary); font-size: 13px; font-variant-numeric: tabular-nums; }
.new-link { display: inline-flex; align-items: center; justify-content: center; gap: 8px; flex-shrink: 0; min-height: 32px; padding: 0 12px; border-radius: 8px; background: var(--accent); color: var(--on-accent); text-decoration: none; font-weight: 500; font-size: 13px; }
.new-link:hover { background: var(--accent-hover); color: var(--on-accent); }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.filters { display: flex; flex-wrap: wrap; gap: 4px; margin: -8px 0 16px; }
.filters button { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; padding: 2px 10px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--text-secondary); font: inherit; font-size: 13px; cursor: pointer; }
.filters button:hover { background: var(--surface-hover); color: var(--text); }
.filters button.active { border-color: var(--border); background: var(--surface); color: var(--text); }
.filters button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.filters .count { color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.list-card { overflow: hidden; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.rows { display: flex; flex-direction: column; }
.row { display: flex; align-items: center; gap: 16px; min-height: 72px; padding: 12px 20px; color: var(--text); text-decoration: none; border-bottom: 1px solid var(--border-soft); }
.row-meta { display: grid; grid-template-columns: 88px 104px; align-items: center; gap: 16px; flex-shrink: 0; }
.row:last-child { border-bottom: 0; }
.row:hover { background: var(--surface-hover); }
.row:focus-visible { outline-offset: -2px; }
.row-copy { display: flex; flex-direction: column; flex: 1; min-width: 0; gap: 4px; }
.row-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.row-repository { color: var(--text-muted); font-size: 12px; overflow-wrap: anywhere; font-family: var(--font-mono); }
time { color: var(--text-muted); font-size: 12px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.skeleton { display: flex; flex-direction: column; gap: 16px; padding: 20px; }
.empty-state { display: flex; flex-direction: column; align-items: center; padding: 56px 24px; color: var(--text-muted); text-align: center; }
.empty-state h3 { margin: 12px 0 0; font-size: 15px; font-weight: 500; color: var(--text); }
.empty-state p { margin: 4px 0 24px; font-size: 13px; }
.error-state { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 16px 20px; padding: 12px 16px; border-radius: 8px; background: var(--danger-bg); color: var(--danger-text); }
.error-state p { margin: 0; font-size: 13px; overflow-wrap: anywhere; }
.load-more { display: flex; justify-content: center; padding: 12px; border-top: 1px solid var(--border-soft); }
@media (max-width: 768px) {
  .list-header { align-items: flex-start; gap: 12px; }
  .list-actions .new-link { display: none; }
  .row { flex-wrap: wrap; gap: 8px 12px; padding: 12px 16px; }
  .row-copy { flex-basis: 100%; }
  .row-meta { display: flex; flex-wrap: wrap; gap: 8px 12px; }
  .error-state { flex-direction: column; align-items: flex-start; margin: 12px 16px; }
}
</style>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { NButton, NSkeleton } from 'naive-ui'
import { ListChecks, Plus } from '@lucide/vue'
import { followLink } from '../router'
import { loadMoreTasks, refreshTasks, taskStore } from '../stores/tasks'
import type { TaskSummary } from '../api/tasks'

const statusLabels: Record<TaskSummary['status'], string> = {
  draft: '待生成方案', generating: '方案生成中', awaiting_approval: '待批准',
  approved: '已批准', generation_failed: '生成失败',
}
// Re-read on entry: task detail pages update the store, but the list can be stale after idle time.
onMounted(() => { void refreshTasks() })
const summary = computed(() => {
  const stats = taskStore.stats
  if (!stats) return ''
  const parts = [`共 ${stats.total} 项`]
  if (stats.awaiting_approval) parts.push(`${stats.awaiting_approval} 项待批准`)
  if (stats.generation_failed) parts.push(`${stats.generation_failed} 项生成失败`)
  if (stats.approved) parts.push(`${stats.approved} 项已批准`)
  return parts.join(' · ')
})
function repositoryName(url: string): string {
  return url.replace('https://github.com/', '')
}
function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}
</script>

<template>
  <div class="task-list">
    <header class="list-header">
      <div>
        <h1>任务</h1>
        <p class="description">{{ summary || '每项任务固定一个仓库、一个 commit 和一个 Issue。' }}</p>
      </div>
      <NButton size="small" class="refresh-button" :loading="taskStore.listLoading" :disabled="taskStore.moreLoading" @click="refreshTasks">刷新</NButton>
    </header>

    <section class="list-card" aria-label="任务列表" :aria-busy="taskStore.listLoading || taskStore.moreLoading">
      <div v-if="taskStore.listError" class="error-state" role="alert">
        <p>{{ taskStore.listError }}</p>
        <NButton :loading="taskStore.listLoading" @click="refreshTasks">重新加载</NButton>
      </div>

      <div v-if="taskStore.listLoading && !taskStore.items.length" class="skeleton" role="status" aria-label="正在加载任务">
        <NSkeleton v-for="index in 3" :key="index" height="64px" :sharp="false" />
      </div>

      <nav v-else-if="taskStore.items.length" class="rows" aria-label="任务">
        <a v-for="task in taskStore.items" :key="task.id" class="row" :href="`/app/tasks/${task.id}`" @click="followLink($event, `/app/tasks/${task.id}`)">
          <span class="row-copy">
            <span class="row-title" :title="task.title">{{ task.title }}</span>
            <span class="row-repository">{{ repositoryName(task.repository_url) }}</span>
          </span>
          <span class="row-meta">
            <span class="row-state" :class="task.status">{{ statusLabels[task.status] }}</span>
            <time :datetime="task.updated_at">{{ formatDate(task.updated_at) }}</time>
          </span>
        </a>
      </nav>

      <div v-else-if="!taskStore.listError" class="empty-state">
        <ListChecks :size="28" :stroke-width="1.5" aria-hidden="true" />
        <h3>还没有任务</h3>
        <p>选择仓库、基准 commit 和一个 Issue，生成可审阅的方案。</p>
        <a class="new-link" href="/app/new" @click="followLink($event, '/app/new')"><Plus :size="18" aria-hidden="true" />新建任务</a>
      </div>

      <div v-if="taskStore.nextCursor" class="load-more">
        <NButton :loading="taskStore.moreLoading" :disabled="taskStore.listLoading" @click="loadMoreTasks">加载更多</NButton>
      </div>
    </section>

  </div>
</template>

<style scoped>
.task-list { width: 100%; max-width: 960px; margin: 0 auto; }
.list-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.refresh-button { flex-shrink: 0; }
h1 { margin: 0; font-size: 26px; line-height: 1.35; }
.description { margin: 6px 0 0; color: var(--text-secondary); font-size: 13px; }
.new-link { display: inline-flex; align-items: center; justify-content: center; gap: 8px; flex-shrink: 0; min-height: 40px; padding: 8px 16px; border-radius: 8px; background: var(--accent); color: var(--surface); text-decoration: none; font-weight: 600; font-size: 14px; }
.new-link:hover { background: var(--accent-hover); color: var(--surface); }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.list-card { overflow: hidden; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.rows { display: flex; flex-direction: column; }
.row { display: flex; align-items: center; gap: 16px; min-height: 72px; padding: 14px 20px; color: var(--text); text-decoration: none; border-bottom: 1px solid var(--border-soft); }
.row-meta { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.row:last-child { border-bottom: 0; }
.row:hover { background: var(--surface-hover); }
.row:focus-visible { outline-offset: -3px; }
.row-copy { display: flex; flex-direction: column; flex: 1; min-width: 0; gap: 3px; }
.row-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.row-repository { color: var(--text-muted); font-size: 12px; overflow-wrap: anywhere; }
.row-state { flex-shrink: 0; padding: 3px 8px; border-radius: 6px; background: var(--bg); color: var(--text-secondary); font-size: 12px; }
.row-state.awaiting_approval { background: var(--warning-bg); color: var(--warning-text); }
.row-state.approved { background: var(--success-bg); color: var(--success-text); }
.row-state.generation_failed { background: var(--danger-bg); color: var(--danger-text); }
.row-state.generating { background: var(--accent-soft); color: var(--accent); }
time { color: var(--text-muted); font-size: 12px; white-space: nowrap; }
.skeleton { display: flex; flex-direction: column; gap: 16px; padding: 20px; }
.empty-state { display: flex; flex-direction: column; align-items: center; padding: 48px 24px; color: var(--text-muted); text-align: center; }
.empty-state h3 { margin: 12px 0 0; font-size: 16px; color: var(--text); }
.empty-state p { margin: 8px 0 20px; font-size: 13px; }
.error-state { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 16px 20px; padding: 14px 16px; border-radius: 8px; background: var(--danger-bg); color: var(--danger-text); }
.error-state p { margin: 0; font-size: 13px; overflow-wrap: anywhere; }
.load-more { display: flex; justify-content: center; padding: 14px; border-top: 1px solid var(--border-soft); }
@media (max-width: 768px) {
  h1 { font-size: 22px; }
  .row { flex-wrap: wrap; gap: 6px 12px; padding: 14px 16px; }
  .row-copy { flex-basis: 100%; }
  .row-meta { flex-wrap: wrap; gap: 8px; }
  .error-state { flex-direction: column; align-items: flex-start; margin: 14px 16px; }
}
</style>

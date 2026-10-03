<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { NButton, NDrawer, NDrawerContent, NSkeleton } from 'naive-ui'
import { LayoutDashboard, LogOut, Menu, Plus, Settings } from '@lucide/vue'
import BrandMark from './BrandMark.vue'
import { followLink, route } from '../router'
import { loadMoreTasks, refreshTasks, taskStore } from '../stores/tasks'
import type { TaskSummary } from '../api/tasks'

const emit = defineEmits<{ settings: []; logout: [] }>()
const mobile = ref(window.matchMedia('(max-width: 768px)').matches)
const drawerOpen = ref(false)
const menuButton = ref<HTMLButtonElement | null>(null)
let pendingSettings = false
const media = window.matchMedia('(max-width: 768px)')
const statusLabels: Record<TaskSummary['status'], string> = {
  draft: '待生成方案', generating: '方案生成中', awaiting_approval: '待批准',
  approved: '已批准', generation_failed: '生成失败',
}
function repositoryName(url: string): string {
  return url.replace('https://github.com/', '')
}
function onMediaChange(event: MediaQueryListEvent): void {
  mobile.value = event.matches
  if (!event.matches) drawerOpen.value = false
}
function openSettings(): void {
  if (drawerOpen.value) {
    pendingSettings = true
    drawerOpen.value = false
  } else emit('settings')
}
function afterDrawerLeave(): void {
  if (mobile.value) menuButton.value?.focus()
  if (pendingSettings) {
    pendingSettings = false
    void nextTick(() => emit('settings'))
  }
}
function selectLink(event: MouseEvent, path: string): void {
  followLink(event, path)
  if (event.defaultPrevented) drawerOpen.value = false
}
watch(route, () => { drawerOpen.value = false })
onMounted(() => media.addEventListener('change', onMediaChange))
onUnmounted(() => media.removeEventListener('change', onMediaChange))
</script>

<template>
  <header v-if="mobile" class="mobile-topbar">
    <BrandMark />
    <a class="new-link mobile-new" href="/app/new" @click="selectLink($event, '/app/new')"><Plus :size="18" aria-hidden="true" />新建任务</a>
    <button ref="menuButton" class="menu-button" type="button" aria-label="打开工作台菜单" aria-haspopup="dialog" :aria-expanded="drawerOpen" @click="drawerOpen = true"><Menu :size="22" aria-hidden="true" /></button>
  </header>
  <component :is="mobile ? NDrawer : 'aside'" :class="mobile ? undefined : 'sidebar'" :show="drawerOpen" placement="left" width="min(320px, calc(100vw - 24px))" :trap-focus="true" :close-on-esc="true" @update:show="drawerOpen = $event" @after-leave="afterDrawerLeave">
    <component :is="mobile ? NDrawerContent : 'div'" :class="mobile ? undefined : 'sidebar-content'" :native-scrollbar="true" :body-content-style="{ display: 'flex', flexDirection: 'column', height: '100%', boxSizing: 'border-box', padding: '24px 16px', overflow: 'hidden' }" :closable="mobile" title="工作台菜单">
      <div class="brand"><BrandMark /></div>
      <nav class="sidebar-nav" aria-label="工作台导航">
        <a class="new-link" href="/app/new" :aria-current="route === '/app/new' ? 'page' : undefined" @click="selectLink($event, '/app/new')"><Plus :size="20" :stroke-width="1.75" aria-hidden="true" />新建任务</a>
        <a class="dashboard-link" :class="{ selected: route === '/app' }" href="/app" :aria-current="route === '/app' ? 'page' : undefined" @click="selectLink($event, '/app')"><LayoutDashboard :size="20" :stroke-width="1.75" aria-hidden="true" />工作台</a>
      </nav>
      <section class="recent-section" aria-labelledby="sidebar-recent-title">
        <h2 id="sidebar-recent-title">最近任务</h2>
        <div class="recent-scroll">
          <div v-if="taskStore.listLoading && !taskStore.items.length" class="list-skeleton" role="status" aria-label="正在加载最近任务"><NSkeleton v-for="index in 4" :key="index" height="58px" :sharp="false" /></div>
          <p v-else-if="!taskStore.items.length && !taskStore.listError" class="empty-note">暂无任务，从新建任务开始。</p>
          <nav v-if="taskStore.items.length" class="recent-list" aria-label="最近任务">
            <a v-for="task in taskStore.items" :key="task.id" class="conversation-link" :class="{ selected: route === `/app/tasks/${task.id}` }" :href="`/app/tasks/${task.id}`" :aria-current="route === `/app/tasks/${task.id}` ? 'page' : undefined" :title="task.title" @click="selectLink($event, `/app/tasks/${task.id}`)">
              <span class="conversation-title">{{ task.title }}</span>
              <span class="conversation-meta"><span class="repository-name">{{ repositoryName(task.repository_url) }}</span><span class="status-label" :class="task.status">{{ statusLabels[task.status] }}</span></span>
            </a>
          </nav>
          <div v-if="taskStore.listError" class="list-error" role="alert"><p>{{ taskStore.listError }}</p><NButton size="small" :loading="taskStore.listLoading || taskStore.moreLoading" @click="refreshTasks">重新加载</NButton></div>
          <NButton v-if="taskStore.nextCursor" class="load-more" :loading="taskStore.moreLoading" :disabled="taskStore.listLoading" @click="loadMoreTasks">加载更多</NButton>
        </div>
      </section>
      <div class="sidebar-actions">
        <NButton class="action-button" attr-type="button" aria-haspopup="dialog" @click="openSettings"><template #icon><Settings :size="18" :stroke-width="1.75" aria-hidden="true" /></template>设置</NButton>
        <NButton class="action-button" attr-type="button" @click="drawerOpen = false; emit('logout')"><template #icon><LogOut :size="18" :stroke-width="1.75" aria-hidden="true" /></template>退出工作台</NButton>
      </div>
    </component>
  </component>
</template>

<style scoped>
.sidebar { box-sizing: border-box; flex: 0 0 240px; width: 240px; height: 100dvh; position: sticky; top: 0; padding: 24px 16px; border-right: 1px solid var(--border); background: var(--surface); }
.sidebar-content { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.brand { display: inline-flex; flex-shrink: 0; margin: 0 8px 24px; }
.sidebar-nav { display: flex; flex-direction: column; flex-shrink: 0; gap: 8px; }
.new-link, .dashboard-link { box-sizing: border-box; display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 44px; padding: 8px 12px; border-radius: 8px; text-decoration: none; font-weight: 600; }
.new-link { color: var(--surface); background: var(--accent); }
.new-link:hover { background: var(--accent-hover); color: var(--surface); }
.dashboard-link { justify-content: flex-start; color: var(--text-secondary); }
.dashboard-link:hover, .conversation-link:hover { background: var(--surface-hover); }
.selected, .dashboard-link.selected { background: var(--accent-soft); color: var(--accent); }
a:focus-visible, .menu-button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.recent-section { display: flex; flex-direction: column; flex: 1; min-height: 0; margin-top: 24px; }
h2 { flex-shrink: 0; margin: 0 12px 12px; color: var(--text-muted); font-size: 12px; font-weight: 600; }
.recent-scroll { min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 3px; }
.recent-list, .list-skeleton { display: flex; flex-direction: column; gap: 8px; }
.conversation-link { display: block; min-width: 0; padding: 10px 9px; border-radius: 8px; color: var(--text); text-decoration: none; }
.conversation-title { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.conversation-meta { display: flex; align-items: center; gap: 8px; margin-top: 4px; color: var(--text-muted); font-size: 11px; }
.repository-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-label { flex-shrink: 0; }
.status-label.approved { color: var(--success-text); }
.status-label.generation_failed { color: var(--danger-text); }
.status-label.awaiting_approval { color: var(--warning-text); }
.empty-note, .list-error { padding: 0 9px; color: var(--text-muted); font-size: 13px; overflow-wrap: anywhere; }
.list-error { color: var(--danger-text); margin: 12px 0; }
.load-more { width: 100%; min-height: 40px; margin-top: 12px; }
.sidebar-actions { display: flex; flex-direction: column; flex-shrink: 0; gap: 8px; margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border-soft); }
.action-button { min-height: 44px; width: 100%; }
.mobile-topbar { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); background: var(--surface); }
.mobile-new { min-height: 40px; margin-left: auto; font-size: 13px; }
.menu-button { display: flex; align-items: center; justify-content: center; flex-shrink: 0; width: 44px; height: 44px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: var(--surface); cursor: pointer; }
</style>

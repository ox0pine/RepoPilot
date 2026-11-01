<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { NButton } from 'naive-ui'
import { ListChecks, LogOut, Plus, Settings } from '@lucide/vue'
import BrandMark from './BrandMark.vue'
import { followLink, route } from '../router'

const emit = defineEmits<{ settings: []; logout: [] }>()
const mobile = ref(window.matchMedia('(max-width: 768px)').matches)
const media = window.matchMedia('(max-width: 768px)')
const tasksActive = () => route.value === '/app' || route.value.startsWith('/app/tasks/')

function onMediaChange(event: MediaQueryListEvent): void {
  mobile.value = event.matches
}
function selectLink(event: MouseEvent, path: string): void {
  followLink(event, path)
}
onMounted(() => media.addEventListener('change', onMediaChange))
onUnmounted(() => media.removeEventListener('change', onMediaChange))
</script>

<template>
  <header v-if="mobile" class="mobile-topbar">
    <a class="brand-link" href="/app" aria-label="任务列表" @click="selectLink($event, '/app')"><BrandMark /></a>
    <div class="topbar-actions">
      <a v-if="route !== '/app/new'" class="primary-link mobile-new" href="/app/new" @click="selectLink($event, '/app/new')"><Plus :size="16" :stroke-width="1.5" aria-hidden="true" />新建任务</a>
      <button class="icon-button" type="button" aria-label="打开设置" aria-haspopup="dialog" @click="emit('settings')"><Settings :size="18" :stroke-width="1.5" aria-hidden="true" /></button>
      <button class="icon-button" type="button" aria-label="退出工作台" @click="emit('logout')"><LogOut :size="18" :stroke-width="1.5" aria-hidden="true" /></button>
    </div>
  </header>
  <aside v-else class="sidebar">
    <div class="sidebar-content">
      <a class="brand" href="/app" aria-label="任务列表" @click="selectLink($event, '/app')"><BrandMark /></a>
      <nav class="sidebar-nav" aria-label="工作台导航">
        <a class="nav-link" :class="{ selected: route === '/app/new' }" href="/app/new" :aria-current="route === '/app/new' ? 'page' : undefined" @click="selectLink($event, '/app/new')"><Plus :size="18" :stroke-width="1.5" aria-hidden="true" />新建任务</a>
        <a class="nav-link" :class="{ selected: tasksActive() }" href="/app" :aria-current="tasksActive() ? 'page' : undefined" @click="selectLink($event, '/app')"><ListChecks :size="18" :stroke-width="1.5" aria-hidden="true" />任务</a>
      </nav>
      <div class="sidebar-actions">
        <NButton quaternary class="action-button" attr-type="button" aria-haspopup="dialog" @click="emit('settings')"><template #icon><Settings :size="18" :stroke-width="1.5" aria-hidden="true" /></template>设置</NButton>
        <NButton quaternary class="action-button" attr-type="button" @click="emit('logout')"><template #icon><LogOut :size="18" :stroke-width="1.5" aria-hidden="true" /></template>退出工作台</NButton>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar { box-sizing: border-box; flex: 0 0 248px; width: 248px; height: 100dvh; position: sticky; top: 0; padding: 20px 12px; border-right: 1px solid var(--border); background: var(--surface); }
.sidebar-content { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.brand { display: inline-flex; flex-shrink: 0; margin: 0 0 24px; padding: 0 12px; text-decoration: none; }
.brand-link { display: inline-flex; flex-shrink: 0; text-decoration: none; }
.sidebar-nav { display: flex; flex-direction: column; flex-shrink: 0; gap: 2px; }
.nav-link { box-sizing: border-box; display: flex; align-items: center; gap: 8px; min-height: 36px; padding: 6px 12px; border-radius: 8px; color: var(--text-secondary); font-weight: 400; text-decoration: none; }
.nav-link svg { flex: none; }
.nav-link:hover { background: var(--surface-hover); color: var(--text); }
.nav-link.selected { background: var(--surface-hover); color: var(--text); font-weight: 500; }
.primary-link { display: inline-flex; align-items: center; gap: 8px; color: var(--on-accent); background: var(--accent); border-radius: 8px; text-decoration: none; font-weight: 500; }
.primary-link:hover { background: var(--accent-hover); color: var(--on-accent); }
a:focus-visible, .icon-button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.sidebar-actions { display: flex; flex-direction: column; flex-shrink: 0; gap: 2px; margin-top: auto; padding-top: 12px; border-top: 1px solid var(--border-soft); }
.action-button { justify-content: flex-start; width: 100%; min-height: 36px; padding: 0 12px; color: var(--text-secondary); font-weight: 400; }
.action-button:hover { color: var(--text); }
.action-button :deep(.n-button__icon) { margin-right: 8px; }
.mobile-topbar { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); background: var(--surface); }
.topbar-actions { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.mobile-new { min-height: 36px; padding: 6px 12px; font-size: 13px; }
.icon-button { display: flex; align-items: center; justify-content: center; flex-shrink: 0; width: 40px; height: 40px; border: 0; border-radius: 8px; color: var(--text-secondary); background: transparent; cursor: pointer; }
.icon-button:hover { background: var(--surface-hover); color: var(--text); }
</style>

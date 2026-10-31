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
      <a v-if="route !== '/app/new'" class="primary-link mobile-new" href="/app/new" @click="selectLink($event, '/app/new')"><Plus :size="18" aria-hidden="true" />新建任务</a>
      <button class="icon-button" type="button" aria-label="打开设置" aria-haspopup="dialog" @click="emit('settings')"><Settings :size="20" aria-hidden="true" /></button>
      <button class="icon-button" type="button" aria-label="退出工作台" @click="emit('logout')"><LogOut :size="20" aria-hidden="true" /></button>
    </div>
  </header>
  <aside v-else class="sidebar">
    <div class="sidebar-content">
      <a class="brand" href="/app" aria-label="任务列表" @click="selectLink($event, '/app')"><BrandMark /></a>
      <nav class="sidebar-nav" aria-label="工作台导航">
        <a class="primary-link" href="/app/new" :aria-current="route === '/app/new' ? 'page' : undefined" @click="selectLink($event, '/app/new')"><Plus :size="20" :stroke-width="1.75" aria-hidden="true" />新建任务</a>
        <a class="nav-link" :class="{ selected: tasksActive() }" href="/app" :aria-current="tasksActive() ? 'page' : undefined" @click="selectLink($event, '/app')"><ListChecks :size="20" :stroke-width="1.75" aria-hidden="true" />任务</a>
      </nav>
      <div class="sidebar-actions">
        <NButton class="action-button" attr-type="button" aria-haspopup="dialog" @click="emit('settings')"><template #icon><Settings :size="18" :stroke-width="1.75" aria-hidden="true" /></template>设置</NButton>
        <NButton class="action-button" attr-type="button" @click="emit('logout')"><template #icon><LogOut :size="18" :stroke-width="1.75" aria-hidden="true" /></template>退出工作台</NButton>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar { box-sizing: border-box; flex: 0 0 232px; width: 232px; height: 100dvh; position: sticky; top: 0; padding: 24px 16px; border-right: 1px solid var(--border); background: var(--surface); }
.sidebar-content { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.brand { display: inline-flex; flex-shrink: 0; margin: 0 8px 28px; text-decoration: none; }
.brand-link { display: inline-flex; flex-shrink: 0; text-decoration: none; }
.sidebar-nav { display: flex; flex-direction: column; flex-shrink: 0; gap: 4px; }
.primary-link, .nav-link { box-sizing: border-box; display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 8px 12px; border-radius: 8px; text-decoration: none; font-weight: 600; }
.primary-link { color: var(--surface); background: var(--accent); }
.primary-link:hover { background: var(--accent-hover); color: var(--surface); }
.nav-link { color: var(--text-secondary); font-weight: 500; }
.nav-link:hover { background: var(--surface-hover); }
.nav-link.selected { background: var(--accent-soft); color: var(--accent); }
a:focus-visible, .icon-button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.sidebar-actions { display: flex; flex-direction: column; flex-shrink: 0; gap: 8px; margin-top: auto; padding-top: 16px; border-top: 1px solid var(--border-soft); }
.action-button { min-height: 44px; width: 100%; justify-content: flex-start; }
.mobile-topbar { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); background: var(--surface); }
.topbar-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.mobile-new { min-height: 40px; padding: 8px 12px; font-size: 13px; }
.icon-button { display: flex; align-items: center; justify-content: center; flex-shrink: 0; width: 40px; height: 40px; border: 1px solid var(--border); border-radius: 8px; color: var(--text-secondary); background: var(--surface); cursor: pointer; }
.icon-button:hover { background: var(--surface-hover); }
</style>

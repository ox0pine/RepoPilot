<script setup lang="ts">
import { NButton } from 'naive-ui'
import { LayoutDashboard, LogOut, Settings } from '@lucide/vue'
import BrandMark from './BrandMark.vue'

const emit = defineEmits<{ settings: []; logout: [] }>()
</script>

<template>
  <aside class="sidebar">
    <div class="brand"><BrandMark /></div>
    <nav class="sidebar-nav" aria-label="工作台导航">
      <span class="current-page" aria-current="page">
        <LayoutDashboard :size="20" :stroke-width="1.75" aria-hidden="true" />
        工作台
      </span>
    </nav>
    <div class="sidebar-actions">
      <NButton class="action-button" attr-type="button" aria-haspopup="dialog" @click="emit('settings')">
        <template #icon><Settings :size="18" :stroke-width="1.75" aria-hidden="true" /></template>
        设置
      </NButton>
      <NButton class="action-button" attr-type="button" @click="emit('logout')">
        <template #icon><LogOut :size="18" :stroke-width="1.75" aria-hidden="true" /></template>
        退出工作台
      </NButton>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  flex: 0 0 240px;
  width: 240px;
  height: 100dvh;
  position: sticky;
  top: 0;
  padding: 24px 16px;
  border-right: 1px solid var(--border);
  background: var(--surface);
}
.brand { display: inline-flex; margin: 0 8px 32px; }
.current-page {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.sidebar-actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid var(--border-soft);
}
.action-button { min-height: 44px; width: 100%; }
@media (max-width: 768px) {
  .sidebar {
    position: static;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 16px;
    flex: none;
    width: 100%;
    height: auto;
    padding: 16px;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }
  .brand { margin: 0; min-width: 0; }
  .current-page { gap: 8px; padding: 8px; }
  .sidebar-actions {
    grid-column: 1 / -1;
    flex-direction: row;
    gap: 12px;
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
  }
  .action-button { flex: 1; min-width: 0; }
}
</style>

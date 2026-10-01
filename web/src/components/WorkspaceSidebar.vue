<script setup lang="ts">
import BrandMark from './BrandMark.vue'

const emit = defineEmits<{ settings: []; logout: [] }>()
</script>

<template>
  <aside class="sidebar">
    <div class="brand"><BrandMark /></div>
    <p class="nav-label">工作台</p>
    <div class="sidebar-actions">
      <button class="logout-button" type="button" @click="emit('logout')">退出工作台</button>
      <button class="settings-button" type="button" aria-haspopup="dialog" @click="emit('settings')">设置</button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar { display: flex; flex-direction: column; flex: 0 0 220px; height: 100dvh; position: sticky; top: 0; padding: 20px 14px; border-right: 1px solid var(--border); background: var(--surface); }
.brand { display: inline-flex; margin: 0 8px 42px; }
.nav-label { margin: 0 10px 8px; color: var(--text-muted); font-size: 11px; }
  .sidebar-actions { display: flex; gap: 8px; margin-top: auto; padding-top: 12px; border-top: 1px solid var(--border-soft); }
  button { flex: 1; min-height: 40px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--text-secondary); font: inherit; white-space: nowrap; cursor: pointer; transition: background-color 150ms, border-color 150ms, color 150ms, transform 150ms; }
  button:hover { background: var(--surface-hover); color: var(--text); transform: translateY(-1px); }
  .settings-button { border-color: var(--accent); color: var(--accent-hover); }
  .settings-button:hover { background: var(--accent-soft); }
  .logout-button { border-color: var(--border); }
@media (max-width: 720px) {
  .sidebar { position: static; flex: none; width: 100%; height: auto; border-right: 0; border-bottom: 1px solid var(--border); }
  .brand { margin-bottom: 12px; }
  .sidebar-actions { position: fixed; bottom: 0; left: 0; right: 0; z-index: 1; padding: 12px 16px max(12px, env(safe-area-inset-bottom)); background: var(--surface); border-top: 1px solid var(--border); }
  button { min-height: 44px; }
}
</style>

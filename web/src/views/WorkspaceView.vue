<script setup lang="ts">
import { ref } from 'vue'
import WorkspaceSidebar from '../components/WorkspaceSidebar.vue'
import SettingsDialog from '../components/SettingsDialog.vue'

const emit = defineEmits<{ logout: [] }>()
const settingsOpen = ref(false)
</script>

<template>
  <div class="workspace-layout">
    <WorkspaceSidebar @settings="settingsOpen = true" @logout="emit('logout')" />
    <main id="main" class="workspace-main" tabindex="-1">
      <header class="workspace-header">
        <div><p class="eyebrow">RepoPilot</p><h1>工作台</h1></div>
        <span class="connection-state"><i></i>本地会话</span>
      </header>
    </main>
    <SettingsDialog v-if="settingsOpen" @close="settingsOpen = false" />
  </div>
</template>

<style scoped>
.workspace-layout { display: flex; min-height: 100dvh; }
.workspace-main { flex: 1; min-width: 0; padding: 34px clamp(20px, 5vw, 64px); }
.workspace-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 32px; }
.eyebrow { margin-bottom: 5px; color: var(--text-muted); font-size: 11px; letter-spacing: .05em; text-transform: uppercase; }
h1 { margin: 0; font-size: 24px; }
.connection-state { display: inline-flex; align-items: center; gap: 7px; padding: 5px 9px; border-radius: 6px; background: var(--success-bg); color: var(--success-text); font-size: 11px; }
.connection-state i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
@media (max-width: 720px) { .workspace-layout { flex-direction: column; } .workspace-main { padding: 24px 16px 92px; } }
</style>

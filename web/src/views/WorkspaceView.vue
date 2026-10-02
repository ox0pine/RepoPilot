<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import WorkspaceSidebar from '../components/WorkspaceSidebar.vue'
import SettingsDialog from '../components/SettingsDialog.vue'
import DashboardView from './DashboardView.vue'
import NewConversationView from './NewConversationView.vue'
import ConversationView from './ConversationView.vue'
import { route } from '../router'
import { refreshTasks } from '../stores/tasks'

const emit = defineEmits<{ logout: [] }>()
const settingsOpen = ref(false)
const taskId = computed(() => route.value.startsWith('/app/tasks/') ? route.value.slice('/app/tasks/'.length).toLowerCase() : null)
onMounted(() => { void refreshTasks() })
</script>

<template>
  <div class="workspace-layout">
    <WorkspaceSidebar @settings="settingsOpen = true" @logout="emit('logout')" />
    <main id="main" class="workspace-main" tabindex="-1">
      <NewConversationView v-if="route === '/app/new'" @settings="settingsOpen = true" @changed="refreshTasks" />
      <ConversationView v-else-if="taskId" :key="taskId" :task-id="taskId" @settings="settingsOpen = true" @changed="refreshTasks" />
      <DashboardView v-else @settings="settingsOpen = true" />
    </main>
    <SettingsDialog v-if="settingsOpen" @close="settingsOpen = false" />
  </div>
</template>

<style scoped>
.workspace-layout { display: flex; min-height: 100dvh; background: var(--bg); }
.workspace-main { box-sizing: border-box; flex: 1; min-width: 0; width: 100%; padding: 48px clamp(24px, 4vw, 48px); }
@media (max-width: 768px) {
  .workspace-layout { flex-direction: column; }
  .workspace-main { padding: 32px 16px; }
}
</style>

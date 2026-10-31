<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import WorkspaceSidebar from '../components/WorkspaceSidebar.vue'
import SettingsDialog from '../components/SettingsDialog.vue'
import TaskListView from './TaskListView.vue'
import NewTaskView from './NewTaskView.vue'
import TaskDetailView from './TaskDetailView.vue'
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
      <NewTaskView v-if="route === '/app/new'" @settings="settingsOpen = true" @changed="refreshTasks" />
      <TaskDetailView v-else-if="taskId" :key="taskId" :task-id="taskId" @settings="settingsOpen = true" @changed="refreshTasks" />
      <TaskListView v-else />
    </main>
    <SettingsDialog v-if="settingsOpen" @close="settingsOpen = false" />
  </div>
</template>

<style scoped>
.workspace-layout { display: flex; min-height: 100dvh; background: var(--bg); }
.workspace-main { box-sizing: border-box; flex: 1; min-width: 0; width: 100%; padding: 36px clamp(20px, 4vw, 40px) 64px; }
@media (max-width: 768px) {
  .workspace-layout { flex-direction: column; }
  .workspace-main { padding: 32px 16px; }
}
</style>

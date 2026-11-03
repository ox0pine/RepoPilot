<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue'
import WorkspaceSidebar from '../components/WorkspaceSidebar.vue'
import TaskListView from './TaskListView.vue'
import NewTaskView from './NewTaskView.vue'
import TaskDetailView from './TaskDetailView.vue'
import { route } from '../router'
import { refreshTasks } from '../stores/tasks'

// The settings dialog pulls in two credential forms; load it when it is opened.
const SettingsDialog = defineAsyncComponent(() => import('../components/SettingsDialog.vue'))

const emit = defineEmits<{ logout: [] }>()
const settingsOpen = ref(false)
const taskId = computed(() => route.value.startsWith('/app/tasks/') ? route.value.slice('/app/tasks/'.length).toLowerCase() : null)
</script>

<template>
  <div class="workspace-layout">
    <WorkspaceSidebar @settings="settingsOpen = true" @logout="emit('logout')" />
    <main id="main" class="workspace-main" tabindex="-1">
      <NewTaskView v-if="route === '/app/new'" @settings="settingsOpen = true" @changed="refreshTasks" />
      <TaskDetailView v-else-if="taskId" :key="taskId" :task-id="taskId" @settings="settingsOpen = true" />
      <TaskListView v-else />
    </main>
    <SettingsDialog v-if="settingsOpen" @close="settingsOpen = false" />
  </div>
</template>

<style scoped>
.workspace-layout { display: flex; min-height: 100dvh; background: var(--bg); }
/* One gutter and one column width for every workspace screen. */
.workspace-main { box-sizing: border-box; flex: 1; min-width: 0; width: 100%; padding: var(--page-gutter) var(--page-gutter) 64px; }
@media (max-width: 768px) {
  .workspace-layout { flex-direction: column; }
  .workspace-main { padding: 24px 16px 48px; }
}
</style>

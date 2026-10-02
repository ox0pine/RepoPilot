<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NCard, NTag } from 'naive-ui'
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
        <NTag :bordered="false">本地会话</NTag>
      </header>
      <NCard class="preparation-card" title="先完成工作台配置">
        <p class="preparation-description">配置模型服务与 GitHub，准备后续代码任务</p>
        <NButton class="open-settings" type="primary" attr-type="button" aria-haspopup="dialog" @click="settingsOpen = true">
          打开设置
        </NButton>
      </NCard>
      <p class="availability-note">任务执行与流式对话尚未接入</p>
    </main>
    <SettingsDialog v-if="settingsOpen" @close="settingsOpen = false" />
  </div>
</template>

<style scoped>
.workspace-layout { display: flex; min-height: 100dvh; background: var(--bg); }
.workspace-main {
  box-sizing: border-box;
  flex: 1;
  min-width: 0;
  width: 100%;
  max-width: 1120px;
  margin: 0 auto;
  padding: 48px clamp(24px, 4vw, 48px);
}
.workspace-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 32px;
}
.eyebrow { margin: 0 0 8px; color: var(--text-muted); font-size: 12px; letter-spacing: .05em; }
h1 { margin: 0; color: var(--text); font-size: 28px; line-height: 1.3; }
.preparation-description { margin: 0 0 24px; color: var(--text-secondary); }
.open-settings { min-height: 44px; }
.availability-note { margin: 16px 0 0; color: var(--text-muted); }
@media (max-width: 768px) {
  .workspace-layout { flex-direction: column; }
  .workspace-main { padding: 32px 16px; }
  .workspace-header { margin-bottom: 24px; }
  h1 { font-size: 24px; }
}
</style>

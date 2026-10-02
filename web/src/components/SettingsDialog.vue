<script setup lang="ts">
import { nextTick, onUnmounted, ref } from 'vue'
import { NButton, NModal } from 'naive-ui'
import { KeyRound, SlidersHorizontal, X } from '@lucide/vue'
import ModelSettingsForm from './ModelSettingsForm.vue'
import GitHubSettingsForm from './GitHubSettingsForm.vue'

const activeCategory = ref<'model' | 'github'>('model')
const githubBusy = ref(false)
const emit = defineEmits<{ close: [] }>()
const opener = document.activeElement
function close(): void {
  if (!githubBusy.value) emit('close')
}
onUnmounted(() => {
  void nextTick(() => {
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus()
  })
})
</script>

<template>
  <NModal :show="true" :mask-closable="false" :close-on-esc="!githubBusy" :trap-focus="true" @update:show="!$event && close()">
    <div class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header class="dialog-header">
        <h2 id="settings-title">设置</h2>
        <NButton attr-type="button" aria-label="关闭设置" :disabled="githubBusy" @click="close"><template #icon><X :size="18" :stroke-width="1.75" aria-hidden="true" /></template>关闭</NButton>
      </header>
      <div class="dialog-body">
        <nav class="settings-categories" aria-label="设置分类">
          <NButton attr-type="button" :type="activeCategory === 'model' ? 'primary' : 'default'" :quaternary="activeCategory !== 'model'" :aria-current="activeCategory === 'model' ? 'page' : undefined" :disabled="githubBusy" @click="activeCategory = 'model'"><template #icon><SlidersHorizontal :size="20" :stroke-width="1.75" aria-hidden="true" /></template>模型提供方</NButton>
          <NButton attr-type="button" :type="activeCategory === 'github' ? 'primary' : 'default'" :quaternary="activeCategory !== 'github'" :aria-current="activeCategory === 'github' ? 'page' : undefined" :disabled="githubBusy" @click="activeCategory = 'github'"><template #icon><KeyRound :size="20" :stroke-width="1.75" aria-hidden="true" /></template>GitHub 授权</NButton>
        </nav>
        <div :key="activeCategory" class="settings-content">
          <ModelSettingsForm v-if="activeCategory === 'model'" />
          <GitHubSettingsForm v-else @busy="githubBusy = $event" />
        </div>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.settings-dialog { box-sizing: border-box; display: flex; flex-direction: column; width: min(960px, calc(100vw - 48px)); height: min(720px, calc(100dvh - 48px)); overflow: hidden; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); color: var(--text); }
.dialog-header { display: flex; flex-shrink: 0; justify-content: space-between; align-items: center; gap: 16px; padding: 16px 24px; border-bottom: 1px solid var(--border); }
h2 { margin: 0; font-size: 20px; }
.dialog-body { display: grid; grid-template-columns: 180px minmax(0, 1fr); flex: 1; min-height: 0; overflow: hidden; }
.settings-categories { display: flex; flex-direction: column; gap: 8px; padding: 24px 12px; background: var(--bg); border-right: 1px solid var(--border); }
.settings-content { min-width: 0; min-height: 0; padding: 24px; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
@media (max-width: 640px) {
  .settings-dialog { width: calc(100vw - 24px); height: calc(100dvh - 24px); }
  .dialog-header { padding: 12px 16px; }
  .dialog-body { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
  .settings-categories { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 12px; border-right: 0; border-bottom: 1px solid var(--border); }
  .settings-content { padding: 16px; }
  .settings-categories > *, .dialog-header > button { min-height: 44px; }
}
</style>

<script setup lang="ts">
import { nextTick, onUnmounted, ref } from 'vue'
import { NButton, NModal } from 'naive-ui'
import { GitBranch, Settings2, SlidersHorizontal, X } from '@lucide/vue'
import ModelSettingsForm from './ModelSettingsForm.vue'
import GitHubSettingsForm from './GitHubSettingsForm.vue'

const activeCategory = ref<'model' | 'github'>('model')
const operationBusy = ref(false)
const emit = defineEmits<{ close: [] }>()
const opener = document.activeElement
function close(): void {
  if (!operationBusy.value) emit('close')
}
onUnmounted(() => {
  void nextTick(() => {
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus()
  })
})
</script>

<template>
  <NModal :show="true" :mask-closable="false" :close-on-esc="!operationBusy" :trap-focus="true" @update:show="!$event && close()">
    <div class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header class="dialog-header">
        <div class="dialog-title"><span class="settings-symbol"><Settings2 :size="21" aria-hidden="true" /></span><div><h2 id="settings-title">工作台设置</h2><p>管理模型服务与仓库访问</p></div></div>
        <NButton quaternary circle attr-type="button" aria-label="关闭设置" :disabled="operationBusy" @click="close"><template #icon><X :size="20" :stroke-width="1.75" aria-hidden="true" /></template></NButton>
      </header>
      <div class="dialog-body">
        <nav class="settings-categories" aria-label="设置分类">
          <button type="button" class="category-button" :class="{ selected: activeCategory === 'model' }" :aria-current="activeCategory === 'model' ? 'page' : undefined" :disabled="operationBusy" @click="activeCategory = 'model'"><SlidersHorizontal :size="19" aria-hidden="true" /><span>模型服务<small>目标生成</small></span></button>
          <button type="button" class="category-button" :class="{ selected: activeCategory === 'github' }" :aria-current="activeCategory === 'github' ? 'page' : undefined" :disabled="operationBusy" @click="activeCategory = 'github'"><GitBranch :size="19" aria-hidden="true" /><span>GitHub<small>仓库访问</small></span></button>
          <p class="nav-note">设置由当前工作台共享</p>
        </nav>
        <div :key="activeCategory" class="settings-content">
          <ModelSettingsForm v-if="activeCategory === 'model'" @busy="operationBusy = $event" />
          <GitHubSettingsForm v-else @busy="operationBusy = $event" />
        </div>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.settings-dialog { box-sizing: border-box; display: flex; flex-direction: column; width: min(1000px, calc(100vw - 48px)); height: min(780px, calc(100dvh - 48px)); overflow: hidden; border: 1px solid var(--border); border-radius: 16px; background: var(--surface); color: var(--text); box-shadow: 0 24px 80px color-mix(in srgb, var(--text) 15%, transparent); }
.dialog-header { display: flex; flex-shrink: 0; justify-content: space-between; align-items: center; gap: 16px; padding: 20px 24px; border-bottom: 1px solid var(--border-soft); }
.dialog-title { display: flex; align-items: center; gap: 12px; }
.settings-symbol { display: grid; place-items: center; width: 42px; height: 42px; border: 1px solid var(--border-soft); border-radius: 12px; background: var(--bg); color: var(--text-secondary); }
h2 { margin: 0; font-size: 18px; line-height: 1.5; }
.dialog-title p { margin: 2px 0 0; font-size: 12px; color: var(--text-muted); }
.dialog-body { display: grid; grid-template-columns: 196px minmax(0, 1fr); flex: 1; min-height: 0; overflow: hidden; }
.settings-categories { display: flex; flex-direction: column; gap: 8px; padding: 20px 12px; background: var(--bg); border-right: 1px solid var(--border-soft); }
.category-button { display: flex; align-items: center; gap: 12px; width: 100%; padding: 13px 14px; border: 1px solid transparent; border-radius: 10px; background: transparent; color: var(--text-secondary); text-align: left; font: inherit; cursor: pointer; }
.category-button > span { font-weight: 600; }
.category-button small { display: block; margin-top: 2px; font-size: 11px; font-weight: 400; color: var(--text-muted); }
.category-button:hover:not(:disabled) { background: var(--surface-hover); }
.category-button.selected { background: var(--surface); border-color: var(--border); color: var(--accent); }
.category-button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.category-button:disabled { cursor: not-allowed; opacity: .6; }
.nav-note { margin: auto 12px 0; padding-top: 24px; color: var(--text-muted); font-size: 11px; }
.settings-content { min-width: 0; min-height: 0; padding: 28px 32px; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
@media (max-width: 640px) {
  .settings-dialog { width: calc(100vw - 24px); height: calc(100dvh - 24px); border-radius: 12px; }
  .dialog-header { padding: 14px 16px; }
  .settings-symbol { display: none; }
  .dialog-body { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
  .settings-categories { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 10px 12px; border-right: 0; border-bottom: 1px solid var(--border-soft); }
  .category-button { padding: 10px 12px; min-height: 44px; }
  .category-button small, .nav-note { display: none; }
  .settings-content { padding: 20px 16px; }
  .dialog-header > button { min-width: 44px; min-height: 44px; }
}
</style>

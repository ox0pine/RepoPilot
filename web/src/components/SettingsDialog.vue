<script setup lang="ts">
import { nextTick, onUnmounted, ref } from 'vue'
import { NButton, NModal } from 'naive-ui'
import { GitBranch, SlidersHorizontal, SunMoon, X } from '@lucide/vue'
import ModelSettingsForm from './ModelSettingsForm.vue'
import GitHubSettingsForm from './GitHubSettingsForm.vue'
import { setThemePreference, themePreference, type ThemePreference } from '../styles/scheme'

type Category = 'model' | 'github' | 'appearance'
const activeCategory = ref<Category>('model')
const operationBusy = ref(false)
const emit = defineEmits<{ close: [] }>()
const opener = document.activeElement

const themeOptions: Array<{ value: ThemePreference; label: string; note: string }> = [
  { value: 'system', label: '跟随系统', note: '随操作系统的浅色或深色切换' },
  { value: 'light', label: '浅色', note: '始终使用浅色背景' },
  { value: 'dark', label: '深色', note: '始终使用深色背景' },
]
const preferenceLabels: Record<ThemePreference, string> = { system: '跟随系统', light: '浅色', dark: '深色' }

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
        <h2 id="settings-title">设置</h2>
        <NButton quaternary circle attr-type="button" aria-label="关闭设置" :disabled="operationBusy" @click="close"><template #icon><X :size="18" :stroke-width="1.5" aria-hidden="true" /></template></NButton>
      </header>
      <div class="dialog-body">
        <nav class="settings-categories" aria-label="设置分类">
          <button type="button" class="category-button" :class="{ selected: activeCategory === 'model' }" :aria-current="activeCategory === 'model' ? 'page' : undefined" :disabled="operationBusy" @click="activeCategory = 'model'"><SlidersHorizontal :size="18" :stroke-width="1.5" aria-hidden="true" /><span>模型服务<small>方案与代码变更</small></span></button>
          <button type="button" class="category-button" :class="{ selected: activeCategory === 'github' }" :aria-current="activeCategory === 'github' ? 'page' : undefined" :disabled="operationBusy" @click="activeCategory = 'github'"><GitBranch :size="18" :stroke-width="1.5" aria-hidden="true" /><span>GitHub<small>仓库与分支交付</small></span></button>
          <button type="button" class="category-button" :class="{ selected: activeCategory === 'appearance' }" :aria-current="activeCategory === 'appearance' ? 'page' : undefined" :disabled="operationBusy" @click="activeCategory = 'appearance'"><SunMoon :size="18" :stroke-width="1.5" aria-hidden="true" /><span>外观<small>{{ preferenceLabels[themePreference] }}</small></span></button>
        </nav>
        <div :key="activeCategory" class="settings-content">
          <ModelSettingsForm v-if="activeCategory === 'model'" @busy="operationBusy = $event" />
          <GitHubSettingsForm v-else-if="activeCategory === 'github'" @busy="operationBusy = $event" />
          <section v-else class="appearance">
            <p class="description">界面配色跟随系统，或固定为浅色或深色。设置保存在当前浏览器。</p>
            <div class="appearance-group" role="radiogroup" aria-labelledby="appearance-title">
              <h3 id="appearance-title" class="sr-only">界面配色</h3>
              <button v-for="option in themeOptions" :key="option.value" type="button" class="appearance-option" :class="{ selected: themePreference === option.value }" role="radio" :aria-checked="themePreference === option.value" @click="setThemePreference(option.value)">
                <span class="appearance-radio" aria-hidden="true" />
                <span class="appearance-copy"><span class="appearance-label">{{ option.label }}</span><span class="appearance-note">{{ option.note }}</span></span>
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.settings-dialog { box-sizing: border-box; display: flex; flex-direction: column; width: min(1000px, calc(100vw - 48px)); height: min(780px, calc(100dvh - 48px)); overflow: hidden; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); color: var(--text); box-shadow: var(--shadow-float); }
.dialog-header { display: flex; flex-shrink: 0; justify-content: space-between; align-items: center; gap: 16px; padding: 12px 20px; border-bottom: 1px solid var(--border-soft); }
h2 { margin: 0; font-size: 15px; font-weight: 500; }
.dialog-body { display: grid; grid-template-columns: 200px minmax(0, 1fr); flex: 1; min-height: 0; overflow: hidden; }
.settings-categories { display: flex; flex-direction: column; gap: 2px; padding: 12px; background: var(--bg); border-right: 1px solid var(--border-soft); }
.category-button { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 12px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--text-secondary); text-align: left; font: inherit; cursor: pointer; }
.category-button > svg { flex: none; color: var(--text-muted); }
.category-button > span { font-weight: 400; }
.category-button small { display: block; margin-top: 2px; font-size: 12px; font-weight: 400; color: var(--text-muted); }
.category-button:hover:not(:disabled) { background: var(--surface-hover); color: var(--text); }
.category-button.selected { background: var(--surface); border-color: var(--border); color: var(--text); }
.category-button.selected > span { font-weight: 500; }
.category-button.selected > svg { color: var(--text); }
.category-button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.category-button:disabled { cursor: not-allowed; opacity: .6; }
.settings-content { min-width: 0; min-height: 0; padding: 24px 32px; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
.appearance .description { margin: 0; color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.appearance-group { margin-top: 16px; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.appearance-option { display: flex; align-items: flex-start; gap: 12px; width: 100%; padding: 12px 16px; border: 0; background: transparent; color: var(--text); text-align: left; font: inherit; cursor: pointer; }
.appearance-option + .appearance-option { border-top: 1px solid var(--border-soft); }
.appearance-option:hover { background: var(--surface-hover); }
.appearance-option.selected { background: var(--surface-hover); }
.appearance-option:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.appearance-radio { flex: none; display: grid; place-items: center; width: 16px; height: 16px; margin-top: 2px; border: 1px solid var(--text-muted); border-radius: 50%; }
.appearance-option.selected .appearance-radio { border-color: var(--accent); }
.appearance-option.selected .appearance-radio::after { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.appearance-copy { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.appearance-label { font-size: 14px; font-weight: 500; }
.appearance-note { color: var(--text-muted); font-size: 12px; line-height: 1.6; }
@media (max-width: 640px) {
  .settings-dialog { width: calc(100vw - 24px); height: calc(100dvh - 24px); }
  .dialog-header { padding: 10px 16px; }
  .dialog-body { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
  .settings-categories { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); padding: 8px 12px; border-right: 0; border-bottom: 1px solid var(--border-soft); }
  .category-button { min-height: 44px; padding: 8px; }
  .category-button small { display: none; }
  .settings-content { padding: 16px; }
  .dialog-header > button { min-width: 44px; min-height: 44px; }
}
</style>

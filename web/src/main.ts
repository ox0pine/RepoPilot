import { createApp } from 'vue'
import App from './App.vue'
import './styles/tokens.css'
import './styles/base.css'
import { cssVariables } from './styles/theme'

for (const [name, value] of Object.entries(cssVariables)) {
  document.documentElement.style.setProperty(name, value)
}
createApp(App).mount('#app')

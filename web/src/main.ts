import { createApp } from 'vue'
import App from './App.vue'
import './styles/tokens.css'
import './styles/base.css'
// Importing the scheme applies the palette before the first render.
import './styles/scheme'

createApp(App).mount('#app')

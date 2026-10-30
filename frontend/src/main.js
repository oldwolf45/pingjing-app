import { createApp } from 'vue'
import App from './App.vue'
import router from './router.js'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import './styles.css'
import { initTheme } from './utils/theme'

// 初始化主题（必须在挂载前，避免闪烁）
initTheme()

const app = createApp(App)
app.use(router)
app.use(ElementPlus, { locale: zhCn, size: 'default' })
app.mount('#app')

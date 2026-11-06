import { createApp } from 'vue'
import { Field, Popup, Radio, RadioGroup } from 'vant'
import 'vant/lib/index.css'

import App from './App.vue'
import router from './router'
import './styles/main.css'

const app = createApp(App)
app.use(router)
app.use(Field)
app.use(Popup)
app.use(Radio)
app.use(RadioGroup)
app.mount('#app')

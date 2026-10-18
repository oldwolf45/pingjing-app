<template>
  <div v-if="visible" class="upd-panel" :class="{ collapsed, done: isDone }">
    <div class="upd-head" @click="collapsed = !collapsed">
      <span class="upd-dot" :class="dotClass"></span>
      <span class="upd-title">{{ title }}</span>
      <span class="upd-toggle">{{ collapsed ? '展开' : '收起' }}</span>
    </div>

    <div v-if="!collapsed" class="upd-body">
      <div class="upd-stage">
        <span class="stage-idx">{{ status.stageIndex || 0 }}/{{ status.stageCount || 7 }}</span>
        <span class="stage-name">{{ status.stageLabel || '准备中' }}</span>
        <span class="stage-count num">
          {{ status.current || 0 }}/{{ status.total || 0 }}
        </span>
      </div>

      <div class="upd-bar">
        <div class="upd-bar-in" :style="{ width: (status.percent || 0) + '%' }"></div>
      </div>

      <div class="upd-msg">{{ status.message || '—' }}</div>

      <div v-if="isDone && status.result" class="upd-result">
        <span>完成 {{ status.result.okCount }}/{{ status.result.total }}</span>
        <span v-if="status.result.failCount">，失败 {{ status.result.failCount }}</span>
        <span v-if="status.result.movedCount">，池子变动 {{ status.result.movedCount }}</span>
      </div>

      <div class="upd-actions">
        <button
          v-if="!isDone"
          class="btn sm"
          :disabled="cancelling || status.cancelRequested"
          @click="onCancel"
        >
          {{ status.cancelRequested ? '正在取消…' : '取消' }}
        </button>
        <button v-else class="btn sm" @click="close">关闭</button>
      </div>

      <div v-if="!isDone" class="upd-tip">取消在当前阶段收尾后生效，不会留下半截数据</div>
    </div>
  </div>
</template>

<script setup>
// 右下角浮出的更新进度面板（DEV_PLAN T4.5）。
//
// 为什么不遮内容：固定贴在右下角，宽 300，页面主体有 24px 内边距，
// 表格最后一列不会被盖住；再给一个「收起」只留一行标题。
// 取消只在阶段边界生效这件事由后端保证，面板这边只是发请求 + 继续轮询。
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api/client.js'

const emit = defineEmits(['done'])

const visible = ref(false)
const collapsed = ref(false)
const cancelling = ref(false)
const status = ref({ status: 'idle', percent: 0, stageCount: 7 })
let timer = null

const isDone = computed(() =>
  ['ok', 'partial', 'failed', 'cancelled', 'idle'].includes(status.value.status))

const title = computed(() => {
  const s = status.value.status
  if (s === 'running') return status.value.cancelRequested ? '更新中（取消中）' : '更新中'
  if (s === 'ok') return '更新完成'
  if (s === 'partial') return '更新完成（部分失败）'
  if (s === 'failed') return '更新失败'
  if (s === 'cancelled') return '更新已取消'
  return '更新'
})

const dotClass = computed(() => {
  const s = status.value.status
  if (s === 'running') return 'running'
  if (s === 'ok') return 'ok'
  if (s === 'cancelled') return 'warn'
  if (s === 'idle') return ''
  return 'bad'
})

async function poll() {
  try {
    status.value = await api.updateStatus()
  } catch (e) {
    /* 后端短暂不可用就跳过这一轮，下一轮再试 */
    return
  }
  if (status.value.status === 'running') {
    timer = window.setTimeout(poll, 700)
  } else {
    stopPolling()
    emit('done', status.value)
  }
}

function stopPolling() {
  if (timer) {
    window.clearTimeout(timer)
    timer = null
  }
}

async function start() {
  collapsed.value = false
  cancelling.value = false
  stopPolling()
  try {
    const r = await api.startUpdate({})
    status.value = r.status || { status: 'running', percent: 0, stageCount: 7 }
    visible.value = true
    if (!r.ok) {
      status.value = r.status || status.value
      return
    }
    timer = window.setTimeout(poll, 400)
  } catch (e) {
    visible.value = true
    status.value = { status: 'failed', percent: 0, stageCount: 7, message: String(e) }
  }
}

async function onCancel() {
  cancelling.value = true
  try {
    const r = await api.cancelUpdate()
    if (r.status) status.value = r.status
  } catch (e) {
    /* 取消失败不改本地状态，交给下一轮轮询纠正 */
  } finally {
    cancelling.value = false
  }
}

function close() {
  stopPolling()
  visible.value = false
}

// 挂载时接回正在跑的更新：跑到一半刷新页面，面板得能自己回来。
// 后端启动时会把上次进程遗留的 running 标成中断，所以这里读到的 running
// 一定是本轮真的在跑。
onMounted(async () => {
  try {
    const st = await api.updateStatus()
    if (st.status === 'running') {
      status.value = st
      visible.value = true
      timer = window.setTimeout(poll, 500)
    }
  } catch (e) {
    /* 后端未就绪，忽略 */
  }
})

onUnmounted(stopPolling)

defineExpose({ start })
</script>

<style scoped>
.upd-panel {
  position: fixed;
  right: 20px;
  bottom: 20px;
  width: 300px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(16, 24, 40, 0.12);
  z-index: 60;
  overflow: hidden;
}
.upd-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  background: #fafbfc;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  user-select: none;
}
.upd-panel.collapsed .upd-head { border-bottom: none; }
.upd-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--fg-3);
  flex: 0 0 auto;
}
.upd-dot.running { background: var(--purple); animation: pulse 1.1s ease-in-out infinite; }
.upd-dot.ok { background: var(--green); }
.upd-dot.warn { background: var(--amber); }
.upd-dot.bad { background: var(--red); }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}
.upd-title { flex: 1; font-weight: 600; }
.upd-toggle { color: var(--fg-3); font-size: 11px; }

.upd-body { padding: 10px 12px 12px; }
.upd-stage {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--fg-2);
  margin-bottom: 6px;
}
.stage-idx {
  background: var(--purple-soft);
  color: var(--purple);
  border-radius: 4px;
  padding: 0 5px;
  font-size: 11px;
}
.stage-name { flex: 1; color: var(--fg); }
.stage-count { color: var(--fg-3); }

.upd-bar {
  height: 5px;
  background: #eef0f4;
  border-radius: 3px;
  overflow: hidden;
}
.upd-bar-in {
  height: 100%;
  background: var(--purple);
  transition: width 0.25s ease;
}

.upd-msg {
  margin-top: 7px;
  font-size: 12px;
  color: var(--fg-2);
  min-height: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.upd-result {
  margin-top: 4px;
  font-size: 12px;
  color: var(--fg);
}
.upd-actions {
  margin-top: 10px;
  display: flex;
  justify-content: flex-end;
}
.upd-tip {
  margin-top: 6px;
  font-size: 11px;
  color: var(--fg-3);
}
</style>

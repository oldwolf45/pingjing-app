<template>
  <div class="stack">
    <section class="card">
      <div class="card-head">
        <h2>数据位置</h2>
        <span class="hint">都在你自己的电脑上</span>
      </div>
      <div class="card-body">
        <div class="row" v-for="(label, key) in folders" :key="key">
          <span class="k">{{ label }}</span>
          <code class="path">{{ paths[key] || '—' }}</code>
          <button class="btn sm" @click="openFolder(key)">打开</button>
        </div>
        <p class="promise">
          {{ privacy.text }}
        </p>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>备份与恢复</h2>
        <div class="spacer"></div>
        <button class="btn primary sm" :disabled="busy" @click="doBackup">立即备份</button>
      </div>
      <div class="card-body">
        <p v-if="msg" class="ok-text">{{ msg }}</p>
        <p v-if="error" class="err">{{ error }}</p>
        <div v-if="!backups.length" class="empty">还没有备份。建议每次跑完更新备份一次。</div>
        <table v-else class="tbl">
          <thead>
            <tr>
              <th>备份 ID</th>
              <th>类型</th>
              <th>时间</th>
              <th class="r">大小</th>
              <th>校验</th>
              <th style="width: 84px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in backups" :key="b.id">
              <td class="num small">{{ b.id }}</td>
              <td>{{ kindLabel(b.kind) }}</td>
              <td class="num">{{ fmtTime(b.created_at) }}</td>
              <td class="r num">{{ kb(b.size) }}</td>
              <td>
                <span v-if="b.verified" class="ok-text">通过</span>
                <span v-else class="err">未通过</span>
              </td>
              <td>
                <button class="btn sm" :disabled="busy || !b.verified" @click="doRestore(b)">
                  恢复
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>导出</h2>
        <span class="hint">JSON 给备份，CSV 给 Excel</span>
      </div>
      <div class="card-body">
        <div class="btns">
          <button class="btn sm" :disabled="busy" @click="doExport('json')">导出 JSON</button>
          <button class="btn sm" :disabled="busy" @click="doExport('csv')">导出 CSV</button>
        </div>
        <p v-if="exportMsg" class="ok-text">{{ exportMsg }}</p>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><h2>版本</h2></div>
      <div class="card-body">
        <div class="row"><span class="k">程序</span><code class="path">v{{ versions.app }}</code></div>
        <div class="row">
          <span class="k">数据库结构</span>
          <code class="path">v{{ versions.schema }}（目标 v{{ versions.schemaTarget }}）</code>
        </div>
        <div class="row"><span class="k">算法</span><code class="path">{{ versions.algo }}</code></div>
      </div>
    </section>
  </div>
</template>

<script setup>
// T5.8 设置页：数据目录 / 备份恢复 / 版本 / 明文写数据保护承诺。
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client.js'

const data = ref({ versions: {}, backups: [], privacy: {} })
const busy = ref(false)
const msg = ref('')
const error = ref('')
const exportMsg = ref('')

const versions = computed(() => data.value.versions || {})
const backups = computed(() => data.value.backups || [])
const privacy = computed(() => data.value.privacy || { text: '' })
const paths = computed(() => ({
  data: versions.value.dataDir,
  backup: versions.value.backupDir,
  export: versions.value.exportDir,
  log: versions.value.logDir,
}))

const folders = { data: '数据目录', backup: '备份目录', export: '导出目录', log: '日志目录' }

function kindLabel(k) {
  return { manual: '手动', pre_migration: '升级前', auto: '自动' }[k] || k || '—'
}
function kb(n) {
  if (!n) return '—'
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`
}
function fmtTime(s) {
  return String(s || '').replace('T', ' ').slice(0, 19)
}

async function load() {
  try {
    data.value = await api.settings()
  } catch (e) {
    error.value = `读取失败：${e.message}`
  }
}

async function openFolder(which) {
  try {
    const r = await api.openFolder(which)
    if (!r.ok) error.value = `打不开：${r.error || '仅桌面版支持'}`
  } catch (e) {
    error.value = e.message
  }
}

async function doBackup() {
  busy.value = true
  msg.value = ''
  error.value = ''
  try {
    const b = await api.backup()
    msg.value = `备份完成：${b.id}`
    await load()
  } catch (e) {
    error.value = `备份失败：${e.message}`
  } finally {
    busy.value = false
  }
}

async function doRestore(b) {
  if (!window.confirm(`从备份 ${b.id} 恢复？当前数据会被覆盖。`)) return
  busy.value = true
  msg.value = ''
  error.value = ''
  try {
    await api.restore({ id: b.id })
    msg.value = `已恢复到 ${b.id}`
    await load()
  } catch (e) {
    error.value = `恢复失败：${e.message}`
  } finally {
    busy.value = false
  }
}

async function doExport(fmt) {
  exportMsg.value = ''
  try {
    const r = await api.exportData(fmt)
    exportMsg.value = `已导出到 ${r.path}`
  } catch (e) {
    error.value = `导出失败：${e.message}`
  }
}

onMounted(load)
</script>

<style scoped>
.stack { display: flex; flex-direction: column; gap: 14px; }
.row { display: flex; align-items: center; gap: 10px; padding: 4px 0; }
.row .k { width: 90px; color: var(--fg-3); font-size: 12px; flex: 0 0 auto; }
.path {
  flex: 1;
  font-size: 11px;
  background: #f6f7f9;
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 3px 7px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.r { text-align: right; }
.btns { display: flex; gap: 8px; }
.promise {
  margin: 12px 0 0;
  padding: 9px 12px;
  background: var(--purple-soft);
  color: var(--purple);
  border-radius: 5px;
  font-size: 12px;
  line-height: 1.7;
}
.err { color: var(--red); font-size: 12px; }
.ok-text { color: var(--green); font-size: 12px; }
</style>

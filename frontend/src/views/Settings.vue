<template>
  <div class="settings-page">
    <!-- 数据位置 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">数据位置</span>
          <span class="subtitle">都在你自己的电脑上</span>
        </div>
      </template>
      <el-descriptions :column="1" border>
        <el-descriptions-item v-for="(label, key) in folders" :key="key" :label="label">
          <template #default>
            <div class="path-row">
              <code class="path">{{ paths[key] || '—' }}</code>
              <el-button size="small" @click="openFolder(key)">打开</el-button>
            </div>
          </template>
        </el-descriptions-item>
      </el-descriptions>
      <el-alert
        :title="privacy.text"
        type="success"
        :closable="false"
        show-icon
        style="margin-top: 14px"
      />
    </el-card>

    <!-- 备份与恢复 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">备份与恢复</span>
          <el-button type="primary" size="small" :loading="busy" @click="doBackup">
            立即备份
          </el-button>
        </div>
      </template>

      <el-table :data="backups" stripe size="default" empty-text="">
        <template #empty>
          <el-empty description="还没有备份。建议每次跑完更新备份一次。" :image-size="80" />
        </template>
        <el-table-column prop="id" label="备份 ID" width="100">
          <template #default="{ row }">
            <span class="num muted small">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">
            <span class="num">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100" align="right">
          <template #default="{ row }">
            <span class="num">{{ kb(row.size) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="校验" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.verified" type="success" size="small" effect="light">通过</el-tag>
            <el-tag v-else type="danger" size="small" effect="light">未通过</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ row }">
            <el-button
              type="primary"
              link
              size="small"
              :disabled="busy || !row.verified"
              @click="doRestore(row)"
            >
              恢复
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 导出 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">导出</span>
          <span class="subtitle">JSON 给备份，CSV 给 Excel</span>
        </div>
      </template>
      <div class="export-btns">
        <el-button :loading="busy" @click="doExport('json')">导出 JSON</el-button>
        <el-button :loading="busy" @click="doExport('csv')">导出 CSV</el-button>
      </div>
      <el-alert
        v-if="exportMsg"
        :title="exportMsg"
        type="success"
        :closable="false"
        show-icon
      />
    </el-card>

    <!-- 外观 -->
    <el-card shadow="never">
      <template #header>
        <span class="title">外观</span>
      </template>
      <div class="theme-row">
        <span class="label">主题</span>
        <el-radio-group v-model="theme" size="default">
          <el-radio-button value="light">浅色</el-radio-button>
          <el-radio-button value="dark">深色</el-radio-button>
          <el-radio-button value="auto">跟随系统</el-radio-button>
        </el-radio-group>
      </div>
    </el-card>

    <!-- 版本 -->
    <el-card shadow="never">
      <template #header>
        <span class="title">版本</span>
      </template>
      <el-descriptions :column="1" border size="default">
        <el-descriptions-item label="程序">
          <code>v{{ versions.app }}</code>
        </el-descriptions-item>
        <el-descriptions-item label="数据库结构">
          <code>v{{ versions.schema }}（目标 v{{ versions.schemaTarget }}）</code>
        </el-descriptions-item>
        <el-descriptions-item label="算法">
          <code>{{ versions.algo }}</code>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'
import { getTheme, setTheme } from '../utils/theme.js'

const theme = ref(getTheme())

watch(theme, (val) => {
  setTheme(val)
})

const data = ref({ versions: {}, backups: [], privacy: {} })
const busy = ref(false)
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
function fmtTime(s) { return String(s || '').replace('T', ' ').slice(0, 19) }

async function load() {
  try {
    data.value = await api.settings()
  } catch (e) {
    ElMessage.error(`读取失败：${e.message}`)
  }
}

async function openFolder(which) {
  try {
    const r = await api.openFolder(which)
    if (!r.ok) ElMessage.error(`打不开：${r.error || '仅桌面版支持'}`)
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function doBackup() {
  busy.value = true
  try {
    const b = await api.backup()
    ElMessage.success(`备份完成：${b.id}`)
    await load()
  } catch (e) {
    ElMessage.error(`备份失败：${e.message}`)
  } finally {
    busy.value = false
  }
}

async function doRestore(b) {
  try {
    await ElMessageBox.confirm(
      `从备份 ${b.id} 恢复？当前数据会被覆盖。`,
      '确认恢复',
      { type: 'warning', confirmButtonText: '确认恢复', cancelButtonText: '取消' }
    )
  } catch { return }
  busy.value = true
  try {
    await api.restore({ id: b.id })
    ElMessage.success(`已恢复到 ${b.id}`)
    await load()
  } catch (e) {
    ElMessage.error(`恢复失败：${e.message}`)
  } finally {
    busy.value = false
  }
}

async function doExport(fmt) {
  try {
    const r = await api.exportData(fmt)
    exportMsg.value = `已导出到 ${r.path}`
    ElMessage.success('导出成功')
  } catch (e) {
    ElMessage.error(`导出失败：${e.message}`)
  }
}

onMounted(load)
</script>

<style scoped>
.settings-page { display: flex; flex-direction: column; gap: 16px; }

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-header .title { font-weight: 600; font-size: 14px; }
.card-header .subtitle { font-size: 12px; color: var(--text-tertiary); }

.path-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.path {
  flex: 1;
  font-size: 12px;
  background: #f6f7f9;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SF Mono', Monaco, monospace;
}

.num { font-variant-numeric: tabular-nums; }
.muted { color: var(--text-tertiary); }
.small { font-size: 11.5px; }

.export-btns {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}

.theme-row {
  display: flex;
  align-items: center;
  gap: 16px;
}
.theme-row .label {
  min-width: 60px;
  font-size: 14px;
  color: var(--text-secondary);
}
</style>

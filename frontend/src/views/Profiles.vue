<template>
  <div class="profiles-page">
    <div class="page-header">
      <div class="title-block">
        <h2>配置 Profile</h2>
        <p>管理隔离的订阅、节点、规则和生成配置</p>
      </div>
      <div class="header-actions">
        <el-button @click="pickImportFile">
          <el-icon><Upload /></el-icon>
          导入到当前 Profile
        </el-button>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          新建 Profile
        </el-button>
        <input ref="importInput" type="file" accept="application/json" hidden @change="importProfile" />
      </div>
    </div>

    <el-table v-loading="loading" :data="profiles" row-key="id" class="profiles-table">
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column prop="id" label="ID" min-width="150" />
      <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.id === activeProfileId" type="success">当前</el-tag>
          <span v-else class="muted">未使用</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="330" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.id !== activeProfileId"
            text
            type="primary"
            :disabled="row.id === 'default' && activeProfileId === row.id"
            @click="activate(row.id)"
          >
            <el-icon><CircleCheck /></el-icon>
            使用
          </el-button>
          <el-button text @click="openEdit(row)">
            <el-icon><Edit /></el-icon>
            编辑
          </el-button>
          <el-button text @click="clone(row)">
            <el-icon><CopyDocument /></el-icon>
            克隆
          </el-button>
          <el-button text @click="exportProfile(row.id)">
            <el-icon><Download /></el-icon>
            导出
          </el-button>
          <el-button v-if="row.id !== 'default'" text type="danger" @click="remove(row)">
            <el-icon><Delete /></el-icon>
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑 Profile' : '新建 Profile'" width="460px">
      <el-form label-width="82px" @submit.prevent="submit">
        <el-form-item label="Profile ID">
          <el-input v-model="form.id" :disabled="Boolean(editingId)" maxlength="64" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="120" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="500" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, CopyDocument, Delete, Download, Edit, Plus, Upload } from '@element-plus/icons-vue'
import { profileApi } from '@/api'
import { useProfileStore, type Profile } from '@/stores/profile'

const profileStore = useProfileStore()
const { profiles, loading, activeProfileId, refreshProfiles, switchProfile } = profileStore
const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref('')
const importInput = ref<HTMLInputElement>()
const form = reactive({ id: '', name: '', description: '' })

const openCreate = () => {
  editingId.value = ''
  Object.assign(form, { id: '', name: '', description: '' })
  dialogVisible.value = true
}

const openEdit = (profile: Profile) => {
  editingId.value = profile.id
  Object.assign(form, {
    id: profile.id,
    name: profile.name,
    description: profile.description || '',
  })
  dialogVisible.value = true
}

const submit = async () => {
  if (!form.name.trim() || (!editingId.value && !form.id.trim())) {
    ElMessage.warning('请填写 Profile ID 和名称')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await profileApi.update(editingId.value, { name: form.name, description: form.description })
    } else {
      await profileApi.create({ id: form.id.trim(), name: form.name, description: form.description })
    }
    await refreshProfiles()
    dialogVisible.value = false
    ElMessage.success('Profile 已保存')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.message || 'Profile 保存失败')
  } finally {
    saving.value = false
  }
}

const activate = async (profileId: string) => {
  try {
    await profileApi.activate(profileId)
    switchProfile(profileId)
    window.location.reload()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.message || '切换 Profile 失败')
  }
}

const clone = async (profile: Profile) => {
  try {
    const { value } = await ElMessageBox.prompt('输入新 Profile ID', `克隆 ${profile.name}`, {
      confirmButtonText: '克隆',
      cancelButtonText: '取消',
      inputValue: `${profile.id}-copy`,
      inputPattern: /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/,
      inputErrorMessage: 'ID 只能包含字母、数字、下划线和短横线',
    })
    await profileApi.clone(profile.id, { id: value, name: `${profile.name} 副本` })
    await refreshProfiles()
    ElMessage.success('Profile 已克隆')
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.message || 'Profile 克隆失败')
  }
}

const remove = async (profile: Profile) => {
  try {
    await ElMessageBox.confirm(`确定删除 Profile「${profile.name}」及其缓存和生成文件吗？`, '删除 Profile', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await profileApi.delete(profile.id)
    await refreshProfiles()
    ElMessage.success('Profile 已删除')
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.message || 'Profile 删除失败')
  }
}

const exportProfile = async (profileId: string) => {
  try {
    const response = await profileApi.export(profileId)
    const url = URL.createObjectURL(new Blob([response.data], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${profileId}.json`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.message || 'Profile 导出失败')
  }
}

const pickImportFile = () => importInput.value?.click()

const importProfile = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const data = JSON.parse(await file.text())
    await profileApi.import(activeProfileId.value, data)
    await refreshProfiles()
    ElMessage.success('配置已导入当前 Profile')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.message || 'Profile 导入失败')
  } finally {
    input.value = ''
  }
}

onMounted(() => {
  refreshProfiles().catch(() => ElMessage.error('加载 Profile 失败'))
})
</script>

<style scoped>
.profiles-page {
  min-height: 100%;
}

.profiles-table {
  width: 100%;
}

.muted {
  color: #909399;
  font-size: 13px;
}

@media (max-width: 720px) {
  .header-actions {
    flex-wrap: wrap;
  }

  .profiles-table :deep(.el-table__fixed-right) {
    display: none;
  }
}
</style>

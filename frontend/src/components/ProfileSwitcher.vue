<template>
  <div class="profile-switcher">
    <el-icon class="profile-icon"><Collection /></el-icon>
    <el-select
      v-model="selectedProfileId"
      size="small"
      class="profile-select"
      :loading="loading"
      aria-label="当前配置 Profile"
      @change="handleChange"
    >
      <el-option
        v-for="profile in profiles"
        :key="profile.id"
        :label="profile.name"
        :value="profile.id"
      >
        <span>{{ profile.name }}</span>
        <small>{{ profile.id }}</small>
      </el-option>
    </el-select>
    <el-button
      text
      class="profile-manager-button"
      title="管理配置 Profile"
      aria-label="管理配置 Profile"
      @click="router.push('/profiles')"
    >
      <el-icon><Setting /></el-icon>
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { useProfileStore } from '@/stores/profile'

const router = useRouter()
const profileStore = useProfileStore()
const { profiles, loading, activeProfileId, refreshProfiles, switchProfile } = profileStore
const selectedProfileId = ref(activeProfileId.value)

watch(activeProfileId, value => {
  selectedProfileId.value = value
})

const handleChange = async (profileId: string) => {
  if (profileId === activeProfileId.value) return
  try {
    await ElMessageBox.confirm(
      '切换后当前页面将重新加载，未保存的编辑内容会丢失。继续吗？',
      '切换配置 Profile',
      { confirmButtonText: '切换', cancelButtonText: '取消', type: 'warning' }
    )
    switchProfile(profileId)
    window.location.reload()
  } catch {
    selectedProfileId.value = activeProfileId.value
  }
}

onMounted(() => {
  refreshProfiles().catch(() => undefined)
})
</script>

<style scoped>
.profile-switcher {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.profile-icon {
  color: #5b67d8;
}

.profile-select {
  width: 158px;
}

.profile-manager-button {
  color: #5b67d8;
}

.profile-select small {
  margin-left: 8px;
  color: #909399;
}

@media (max-width: 700px) {
  .profile-icon {
    display: none;
  }

  .profile-select {
    width: 128px;
  }
}
</style>

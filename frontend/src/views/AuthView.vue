<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { request, errorMessage } from '../api/client'
import type { Session } from '../api/types'
import AppIcon from '../components/AppIcon.vue'

const props = defineProps<{ setup: boolean; notice: string }>()
const emit = defineEmits<{ authenticated: [session: Session] }>()
const setupCompleted = ref(false)
const isSetup = computed(() => props.setup && !setupCompleted.value)
const form = reactive({ bootstrap_secret: '', username: '', password: '', confirm: '' })
const busy = ref(false)
const error = ref('')
async function submit() {
  error.value = ''
  if (!form.username.trim() || !form.password) { error.value = '请填写管理员账号和密码。'; return }
  if (isSetup.value && (!form.bootstrap_secret || form.password.length < 6 || form.password.length > 20 || form.password !== form.confirm)) {
    error.value = '请填写初始化密钥，设置 6–20 位密码，并确认两次密码一致。'; return
  }
  busy.value = true
  try {
    if (isSetup.value) {
      await request('/setup', { method: 'POST', body: JSON.stringify({
        bootstrap_secret: form.bootstrap_secret, username: form.username.trim(), password: form.password,
      }) })
      setupCompleted.value = true
    }
    const session = await request<Session>('/auth/login', { method: 'POST', body: JSON.stringify({ username: form.username.trim(), password: form.password }) })
    form.password = ''; form.confirm = ''; form.bootstrap_secret = ''
    emit('authenticated', session)
  } catch (cause) { error.value = errorMessage(cause) }
  finally { busy.value = false }
}
</script>
<template>
  <main class="auth-layout">
    <section class="auth-story">
      <div class="brand"><span class="brand-mark"><AppIcon name="download" /></span><strong>TGD</strong></div>
      <div><p class="eyebrow">TELEGRAM → YOUR STORAGE</p><h1>看到喜欢的，<br>留在自己的空间。</h1><p>转发视频与图片给专属 Bot，确认后自动保存到本地或 NAS。</p></div>
      <p class="auth-footer">简洁管理 · 自主存储</p>
    </section>
    <section class="auth-panel" aria-labelledby="auth-title">
      <div class="panel auth-card">
        <p class="eyebrow">{{ isSetup ? 'WELCOME' : 'WELCOME BACK' }}</p>
        <h2 id="auth-title">{{ isSetup ? '初始化你的下载助手' : '登录管理界面' }}</h2>
        <p class="muted">{{ isSetup ? '创建唯一的管理员账号，随后连接 Telegram Bot。' : '查看下载进度，管理你的连接与存储。' }}</p>
        <el-alert v-if="notice" :title="notice" type="warning" :closable="false" show-icon />
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item v-if="isSetup" label="初始化密钥" label-for="bootstrap-secret">
            <el-input id="bootstrap-secret" v-model="form.bootstrap_secret" type="password" show-password autocomplete="off" />
            <p class="field-hint">使用部署时配置的初始化密钥。</p>
          </el-form-item>
          <el-form-item label="管理员账号" label-for="username"><el-input id="username" v-model="form.username" autocomplete="username" maxlength="80" /></el-form-item>
          <el-form-item label="密码" label-for="password"><el-input id="password" v-model="form.password" type="password" show-password :autocomplete="isSetup ? 'new-password' : 'current-password'" />
            <p v-if="isSetup" class="field-hint">6–20 位，建议使用密码管理器生成。</p>
          </el-form-item>
          <el-form-item v-if="isSetup" label="确认密码" label-for="confirm-password"><el-input id="confirm-password" v-model="form.confirm" type="password" show-password autocomplete="new-password" /></el-form-item>
          <el-button class="auth-submit" type="primary" native-type="submit" :loading="busy">{{ isSetup ? '创建管理员并进入' : '登录' }}</el-button>
        </el-form>
      </div>
      <p class="auth-caption">TGD / Telegram 媒体下载助手</p>
    </section>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { errorMessage, request } from '../api/client'
const emit = defineEmits<{ changed: [] }>()
const open = ref(false)
const busy = ref(false)
const error = ref('')
const form = reactive({ current: '', password: '', confirm: '' })
function reset() { form.current = ''; form.password = ''; form.confirm = ''; error.value = '' }
async function submit() {
  error.value = ''
  if (!form.current || form.password.length < 6 || form.password.length > 20 || form.password !== form.confirm) {
    error.value = '请填写当前密码，并设置 6–20 位新密码，确保两次输入一致。'; return
  }
  busy.value = true
  try {
    await request('/auth/password', { method: 'POST', body: JSON.stringify({ current_password: form.current, new_password: form.password }) })
    open.value = false; reset(); emit('changed')
  } catch (cause) { error.value = errorMessage(cause) }
  finally { busy.value = false }
}
</script>
<template>
  <el-button text @click="open = true">修改密码</el-button>
  <el-dialog v-model="open" title="修改管理员密码" width="min(440px, calc(100vw - 32px))" :close-on-click-modal="false" @closed="reset">
    <p class="muted">修改后所有会话将退出，需要使用新密码重新登录。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="当前密码" label-for="current-password"><el-input id="current-password" v-model="form.current" type="password" autocomplete="current-password" show-password /></el-form-item>
      <el-form-item label="新密码" label-for="new-password"><el-input id="new-password" v-model="form.password" type="password" autocomplete="new-password" show-password /><p class="field-hint">6–20 位，可使用密码管理器生成。</p></el-form-item>
      <el-form-item label="确认新密码" label-for="confirm-new-password"><el-input id="confirm-new-password" v-model="form.confirm" type="password" autocomplete="new-password" show-password /></el-form-item>
      <el-button type="primary" native-type="submit" :loading="busy">修改并重新登录</el-button>
    </el-form>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { ApiError, errorMessage, onSessionExpired, request, setCsrfToken } from './api/client'
import type { Session } from './api/types'
import AppIcon from './components/AppIcon.vue'
import PasswordChange from './components/PasswordChange.vue'
import AuthView from './views/AuthView.vue'
import TasksView from './views/TasksView.vue'
import SettingsView from './views/SettingsView.vue'
import SystemView from './views/SystemView.vue'

type Page = 'tasks' | 'settings' | 'system'
const pages: { id: Page; label: string }[] = [{ id: 'tasks', label: '下载任务' }, { id: 'settings', label: '设置' }, { id: 'system', label: '系统状态' }]
function hashPage(): Page { return pages.find(item => `#/${item.id}` === location.hash)?.id ?? 'tasks' }
const page = ref<Page>(hashPage())
const session = ref<Session>()
const initialized = ref(true)
const loading = ref(true)
const error = ref('')
const notice = ref('')
const dirty = ref(false)
async function bootstrap() {
  loading.value = true; error.value = ''
  try {
    const status = await request<{ initialized: boolean }>('/setup/status')
    initialized.value = status.initialized
    if (status.initialized) {
      try { authenticated(await request<Session>('/auth/me')) }
      catch (cause) { if (!(cause instanceof ApiError && cause.status === 401)) throw cause }
    }
  } catch (cause) { error.value = errorMessage(cause) }
  finally { loading.value = false }
}
function authenticated(value: Session) { session.value = value; setCsrfToken(value.csrf_token); notice.value = ''; initialized.value = true }
function passwordChanged() { setCsrfToken(''); session.value = undefined; dirty.value = false; notice.value = '密码已修改，请使用新密码登录。' }
onSessionExpired(() => { session.value = undefined; dirty.value = false; notice.value = '会话已过期，请重新登录。' })
async function canLeave() {
  if (!dirty.value) return true
  try { await ElMessageBox.confirm('还有未保存的设置，离开后这些修改将丢失。', '离开设置', { confirmButtonText: '放弃修改并离开', cancelButtonText: '继续编辑', type: 'warning' }); return true }
  catch { return false }
}
async function navigate(target: Page) {
  if (target === page.value || !await canLeave()) return
  dirty.value = false; page.value = target; location.hash = `/${target}`
}
async function hashChanged() {
  const target = hashPage()
  if (target !== page.value) {
    if (await canLeave()) { dirty.value = false; page.value = target }
    else history.replaceState(null, '', `#/${page.value}`)
  }
}
async function logout() {
  if (!await canLeave()) return
  try { await request('/auth/logout', { method: 'POST' }); setCsrfToken(''); session.value = undefined; dirty.value = false }
  catch (cause) { error.value = errorMessage(cause) }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
function focusMain() { document.getElementById('main-content')?.focus() }
onMounted(() => { void bootstrap(); window.addEventListener('hashchange', hashChanged); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => { window.removeEventListener('hashchange', hashChanged); window.removeEventListener('beforeunload', beforeUnload) })
</script>
<template>
  <div v-if="loading" class="startup" role="status"><span class="brand-mark"><AppIcon name="download" /></span><h1>TGD</h1><p class="muted">正在连接你的下载助手…</p></div>
  <div v-else-if="error && !session" class="startup"><h1>暂时无法连接服务</h1><p role="alert">{{ error }}</p><el-button type="primary" @click="bootstrap">重新连接</el-button></div>
  <AuthView v-else-if="!session" :setup="!initialized" :notice="notice" @authenticated="authenticated" />
  <div v-else class="app-shell" :class="{ 'settings-active': page === 'settings' }">
    <a class="skip-link" href="#main-content" @click.prevent="focusMain">跳转到主要内容</a>
    <aside class="sidebar"><a class="brand" href="#/tasks" @click.prevent="navigate('tasks')"><span class="brand-mark"><AppIcon name="download" /></span><span><strong>TGD</strong><small>媒体下载助手</small></span></a><p class="nav-label">工作空间</p><nav aria-label="主导航"><a v-for="item in pages" :key="item.id" :href="`#/${item.id}`" :class="{ active: page === item.id }" :aria-current="page === item.id ? 'page' : undefined" @click.prevent="navigate(item.id)"><AppIcon :name="item.id" /><span>{{ item.label }}</span></a></nav><div class="sidebar-note"><span class="local-dot"></span><div><strong>你的私人媒体空间</strong><p>从 Telegram 到本地 / NAS</p></div></div></aside>
    <div class="main-shell"><header class="topbar"><span class="breadcrumb">工作空间 <span>/</span> {{ pages.find(item => item.id === page)?.label }}</span><div class="account"><span>{{ session.username }}</span><PasswordChange @changed="passwordChanged" /><el-button text @click="logout">退出登录</el-button></div></header><main id="main-content" class="main-content" :class="{ 'main-content--settings': page === 'settings' }" tabindex="-1"><el-alert v-if="error" :title="error" type="error" closable @close="error = ''" /><TasksView v-if="page === 'tasks'" /><SettingsView v-else-if="page === 'settings'" @dirty="dirty = $event" /><SystemView v-else /></main><footer v-if="page !== 'settings'" class="app-footer">TGD <span>Telegram 媒体下载助手</span></footer></div>
  </div>
</template>

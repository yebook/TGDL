import { defineConfig } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  testMatch: 'task-files.spec.ts',
  outputDir: '../.runtime/task-files/results',
  use: { ...base.use, baseURL: 'http://127.0.0.1:18767' },
  webServer: process.env.TGDL_E2E_EXTERNAL_SERVER ? undefined : {
    ...base.webServer,
    env: { TGDL_E2E_PORT: '18767', E2E_TASK_FILES: '1' },
    url: 'http://127.0.0.1:18767/health/ready',
  },
})

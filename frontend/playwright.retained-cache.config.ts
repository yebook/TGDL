import { defineConfig } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  testMatch: 'retained-cache.spec.ts',
  outputDir: '../.runtime/retained-cache/results',
  use: { ...base.use, baseURL: 'http://127.0.0.1:18769' },
  webServer: process.env.TGDL_E2E_EXTERNAL_SERVER ? undefined : {
    ...base.webServer,
    env: { TGDL_E2E_PORT: '18769', E2E_RETAINED_CACHE: '1' },
    url: 'http://127.0.0.1:18769/health/ready',
  },
})

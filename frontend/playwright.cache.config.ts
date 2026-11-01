import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Real REST/service/DB checks; only the private native boundary is synthetic.
export default defineConfig({
  ...base,
  testMatch: 'cache-storage.spec.ts',
  outputDir: '../.runtime/cache-storage/results',
  use: { ...base.use, baseURL: 'http://127.0.0.1:18765' },
  webServer: process.env.TGDL_E2E_EXTERNAL_SERVER ? undefined : {
    ...base.webServer,
    command: 'uv run --directory ../backend python -m tools.e2e_app',
    env: { TGDL_E2E_PORT: '18765', E2E_CACHE_STORAGE: '1' },
    url: 'http://127.0.0.1:18765/health/ready',
  },
})

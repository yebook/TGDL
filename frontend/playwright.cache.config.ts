import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Isolated UI contract checks; cache responses are synthetic, no user media.
export default defineConfig({
  ...base,
  testMatch: 'cache-storage.spec.ts',
  use: { ...base.use, baseURL: 'http://127.0.0.1:18765' },
  webServer: process.env.TGDL_E2E_EXTERNAL_SERVER ? undefined : {
    ...base.webServer,
    command: 'uv run --directory ../backend python -m tools.e2e_app',
    env: { TGDL_E2E_PORT: '18765' },
    url: 'http://127.0.0.1:18765/health/ready',
  },
})

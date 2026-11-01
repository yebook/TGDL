import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Real REST/worker cancellation; only the isolated native boundary is synthetic.
export default defineConfig({
  ...base,
  testMatch: 'cancellation.spec.ts',
  use: { ...base.use, baseURL: 'http://127.0.0.1:18766' },
  webServer: process.env.TGDL_E2E_EXTERNAL_SERVER ? undefined : {
    ...base.webServer,
    command: 'uv run --directory ../backend python -m tools.e2e_app',
    env: { TGDL_E2E_PORT: '18766', E2E_ACQUISITION_CANCEL: '1' },
    url: 'http://127.0.0.1:18766/health/ready',
  },
})

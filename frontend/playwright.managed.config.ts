import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Separate opt-in disposable fixture: does not alter normal acceptance state.
export default defineConfig({ ...base, testMatch: ['managed-api.spec.ts', 'proxy-network-ui.spec.ts'] })

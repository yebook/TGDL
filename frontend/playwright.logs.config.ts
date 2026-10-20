import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Isolated UI contract checks; log responses are synthetic, not user diagnostics.
export default defineConfig({ ...base, testMatch: 'diagnostic-logs.spec.ts' })

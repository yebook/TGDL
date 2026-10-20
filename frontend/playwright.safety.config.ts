import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Opt-in fixture scenarios run separately from the ordinary acceptance state.
export default defineConfig({ ...base, testMatch: 'request-safety.spec.ts' })

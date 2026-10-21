import { defineConfig } from 'vitepress'
import { writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const base = process.env.DOCS_BASE || '/TGDL/'
if (!/^\/(?:[A-Za-z0-9_-]+\/)*$/.test(base)) {
  throw new Error('DOCS_BASE must be / or a slash-delimited path such as /TGDL/.')
}

export default defineConfig({
  lang: 'zh-CN',
  title: 'TGDL',
  description: 'Telegram 媒体下载助手：确认后保存到本地或 NAS。',
  base,
  cleanUrls: false,
  // Navigation alone does not prevent historical/internal pages from publishing.
  srcExclude: ['README.md', 'project-requirements.md', 'node_modules/**'],
  lastUpdated: false,
  async buildEnd(siteConfig) {
    await writeFile(resolve(siteConfig.outDir, '.nojekyll'), '')
  },
  sitemap: undefined,
  themeConfig: {
    logo: undefined,
    nav: [
      { text: '开始使用', link: '/quick-start' },
      { text: '部署', link: '/deployment' },
      { text: '开发', link: '/development' },
    ],
    sidebar: [
      { text: '使用指南', items: [
        { text: '快速开始', link: '/quick-start' },
        { text: 'Docker / 飞牛 OS', link: '/deployment' },
        { text: '任务与归档', link: '/usage' },
        { text: '本地 Bot API 与凭据', link: '/telegram-api' },
        { text: '提示与文件名模板', link: '/templates' },
        { text: '配置备份与恢复', link: '/configuration-backup' },
        { text: '维护与诊断日志', link: '/operations' },
        { text: '常见问题', link: '/faq' },
      ] },
      { text: '开发与项目', items: [
        { text: '开发指南', link: '/development' },
        { text: '部署后验收', link: '/acceptance' },
        { text: 'Docker 隔离验收', link: '/docker-acceptance' },
        { text: '验证范围', link: '/verification' },
        { text: '文档站与 Pages', link: '/pages' },
        { text: '参与贡献与许可', link: '/project' },
      ] },
    ],
    search: { provider: 'local', options: { locales: { root: { translations: {
      button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
      modal: { noResultsText: '没有找到相关结果', resetButtonTitle: '清空搜索',
        footer: { selectText: '选择', navigateText: '切换', closeText: '关闭' } },
    } } } } },
    outline: { label: '本页内容', level: [2, 3] },
    docFooter: { prev: '上一页', next: '下一页' },
    sidebarMenuLabel: '目录',
    returnToTopLabel: '返回顶部',
    socialLinks: [{ icon: 'github', link: 'https://github.com/yebook/TGDL' }],
    footer: { message: '按 MIT 许可证分发 · 文档描述当前源码，真实设备验收范围另列。',
      copyright: 'Copyright © 2026 TGDL contributors' },
  },
})

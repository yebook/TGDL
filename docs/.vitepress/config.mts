import { defineConfig } from 'vitepress'
import { writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import release from '../../backend/app/version.json'

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
      { text: '使用指南', link: '/settings' },
      { text: release.version, link: '/changelog' },
    ],
    sidebar: [
      { text: '开始使用', items: [
        { text: 'Docker', link: '/deployment' },
        { text: '第一次下载', link: '/quick-start' },
        { text: 'Windows 本地运行', link: '/local-run' },
      ] },
      { text: '日常使用', items: [
        { text: '设置怎么用', link: '/settings' },
        { text: '大文件与网络代理', link: '/telegram-api' },
        { text: '任务、进度与归档', link: '/usage' },
        { text: '提示与文件名模板', link: '/templates' },
      ] },
      { text: '维护与帮助', items: [
        { text: '配置备份与恢复', link: '/configuration-backup' },
        { text: '日志与故障处理', link: '/operations' },
        { text: '常见问题', link: '/faq' },
        { text: '更新日志', link: '/changelog' },
      ] },
      { text: '开发与项目', collapsed: true, items: [
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
    footer: { message: `${release.version} · MIT 许可 · 文档对应当前源码`,
      copyright: 'Copyright © 2026 TGDL contributors' },
  },
})

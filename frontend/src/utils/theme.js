/**
 * 主题切换工具
 *
 * 支持三档：
 *   - light: 浅色模式
 *   - dark:  深色模式
 *   - auto:  跟随系统
 *
 * 存在 localStorage，下次启动记住选择。
 */

const STORAGE_KEY = 'zisuye-theme'

/**
 * 获取当前主题
 * @returns {'light'|'dark'|'auto'}
 */
export function getTheme() {
  return localStorage.getItem(STORAGE_KEY) || 'auto'
}

/**
 * 应用主题
 * @param {'light'|'dark'|'auto'} theme
 */
export function applyTheme(theme) {
  const html = document.documentElement

  // 先清除之前的 dark class
  html.classList.remove('dark')

  let effective = theme
  if (theme === 'auto') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    effective = prefersDark ? 'dark' : 'light'
  }

  if (effective === 'dark') {
    html.classList.add('dark')
  }

  // 同步到 Element Plus（通过 html.dark + CSS 变量，Element Plus 自身也有 dark 模式）
  // 这里我们只用 CSS 变量覆盖，不引入 Element Plus 的暗色 CSS，保持轻量
}

/**
 * 设置并保存主题
 * @param {'light'|'dark'|'auto'} theme
 */
export function setTheme(theme) {
  localStorage.setItem(STORAGE_KEY, theme)
  applyTheme(theme)
}

/**
 * 初始化主题（启动时调用一次）
 */
export function initTheme() {
  applyTheme(getTheme())

  // 跟随系统模式：监听系统主题变化
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (getTheme() === 'auto') {
      applyTheme('auto')
    }
  })
}

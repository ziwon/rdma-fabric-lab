// Expressive Code options for Starlight. They live here, not in astro.config.mjs, because the <Code> component
// needs them serializable and the theme-dependent colors below are functions.
import { defineEcConfig } from '@astrojs/starlight/expressive-code';

export default defineEcConfig({
  themes: ['github-dark-dimmed', 'github-light'],
  styleOverrides: {
    borderRadius: '6px',
    borderColor: 'var(--sl-color-gray-5)',
    codeFontFamily: 'var(--sl-font-mono)',
    codeFontSize: '0.82rem',
    codeBackground: ({ theme }) => (theme.type === 'dark' ? '#15181b' : '#f6f7f8'),
    frames: {
      editorTabBarBackground: ({ theme }) => (theme.type === 'dark' ? '#1b1f23' : '#eef1f3'),
      terminalTitlebarBackground: ({ theme }) => (theme.type === 'dark' ? '#1b1f23' : '#eef1f3'),
      frameBoxShadowCssValue: 'none',
    },
  },
});

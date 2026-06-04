import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{ts,tsx}',
    './src/components/**/*.{ts,tsx}',
    './src/app/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: '#08090d',
          surface: '#0f1015',
          surface2: '#141520',
          surface3: '#1a1d2e',
        },
        border: {
          DEFAULT: '#1e2030',
          strong: '#2d3048',
        },
        accent: {
          DEFAULT: '#818cf8',
          hover: '#6366f1',
          dim: 'rgba(129,140,248,0.12)',
        },
        ink: {
          DEFAULT: '#e2e8f0',
          muted: '#94a3b8',
          dim: '#64748b',
        },
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.2s ease-out',
        'slide-up': 'slideUp 0.25s ease-out',
      },
      keyframes: {
        fadeIn: { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        slideUp: { '0%': { opacity: '0', transform: 'translateY(8px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
      },
    },
  },
  plugins: [],
}
export default config

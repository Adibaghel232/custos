/** @type {import('tailwindcss').Config} */
// Every colour is a CSS variable (see src/index.css) so the theme can change without touching components.
const rgb = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    // Type scale tuned for 1080p screen recordings: nothing below 13px, body 15px, key labels 18px+.
    fontSize: {
      xs: ['0.8125rem', { lineHeight: '1.2rem' }],
      sm: ['0.9375rem', { lineHeight: '1.4rem' }],
      base: ['1rem', { lineHeight: '1.5rem' }],
      lg: ['1.125rem', { lineHeight: '1.6rem' }],
      xl: ['1.25rem', { lineHeight: '1.75rem' }],
      '2xl': ['1.5rem', { lineHeight: '2rem' }],
      '3xl': ['1.875rem', { lineHeight: '2.25rem' }],
      '4xl': ['2.25rem', { lineHeight: '2.5rem' }],
      '5xl': ['3rem', { lineHeight: '1' }],
      '6xl': ['3.75rem', { lineHeight: '1' }],
    },
    extend: {
      colors: {
        bg: rgb('bg'),
        panel: rgb('panel'),
        panel2: rgb('panel-2'),
        border: rgb('border'),
        ink: rgb('text'),
        muted: rgb('muted'),
        faint: rgb('faint'),
        t0: rgb('t0'),
        t1: rgb('t1'),
        t2: rgb('t2'),
        t3: rgb('t3'),
        t4: rgb('t4'),
        danger: rgb('danger'),
        dangerink: rgb('danger-ink'),
        ok: rgb('ok'),
        warn: rgb('warn'),
        info: rgb('info'),
        reverted: rgb('reverted'),
        revertedink: rgb('reverted-ink'),
        onfill: rgb('on-fill'),
        def: {
          undefended: rgb('def-undefended'),
          pi: rgb('def-pi'),
          amg: rgb('def-amg'),
          custos: rgb('def-custos'),
        },
      },
      fontFamily: {
        sans: ['"Inter Variable"', 'Inter', 'ui-sans-serif', 'system-ui', '"Segoe UI"', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['"Cascadia Mono"', '"JetBrains Mono"', 'Consolas', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 0 0 rgb(255 255 255 / 0.03) inset, 0 8px 24px -12px rgb(0 0 0 / 0.55)',
        pop: '0 24px 64px -16px rgb(0 0 0 / 0.7), 0 0 0 1px rgb(var(--border) / 1)',
      },
      keyframes: {
        dropIn: { '0%': { opacity: '0', transform: 'translateY(-14px) scale(0.985)' }, '100%': { opacity: '1', transform: 'none' } },
        fadeIn: { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        slideUp: { '0%': { opacity: '0', transform: 'translateY(24px)' }, '100%': { opacity: '1', transform: 'none' } },
        popIn: { '0%': { opacity: '0', transform: 'scale(0.94)' }, '100%': { opacity: '1', transform: 'none' } },
        pulseRing: { '0%': { boxShadow: '0 0 0 0 rgb(var(--ok) / 0.55)' }, '100%': { boxShadow: '0 0 0 14px rgb(var(--ok) / 0)' } },
        flash: { '0%': { backgroundColor: 'rgb(var(--info) / 0.28)' }, '100%': { backgroundColor: 'transparent' } },
        bannerIn: { '0%': { opacity: '0', transform: 'scaleY(0.4)' }, '60%': { opacity: '1', transform: 'scaleY(1.06)' }, '100%': { opacity: '1', transform: 'none' } },
        blink: { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.35' } },
      },
      animation: {
        'drop-in': 'dropIn 420ms cubic-bezier(0.2, 0.8, 0.2, 1) both',
        'fade-in': 'fadeIn 260ms ease-out both',
        'slide-up': 'slideUp 420ms cubic-bezier(0.2, 0.8, 0.2, 1) both',
        'pop-in': 'popIn 260ms cubic-bezier(0.2, 0.8, 0.2, 1) both',
        'pulse-ring': 'pulseRing 1100ms ease-out 2',
        flash: 'flash 1400ms ease-out both',
        'banner-in': 'bannerIn 520ms cubic-bezier(0.2, 0.8, 0.2, 1) both',
        blink: 'blink 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};

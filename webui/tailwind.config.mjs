/** @type {import('tailwindcss').Config} */
const config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Generic enterprise palette.
        'brand-grey':       '#000000',  // true black (app header/hero bg)
        'brand-ink':        '#1A1A1A',  // slightly lifted black for text
        'brand-paper':      '#F7F7F7',  // neutral neutral background
        'brand-line':       '#E2E2E2',  // hairlines / borders
        'brand-line-dark':  '#5E5E5E',  // app on-black divider
        'brand-green':      '#8254FD',  // PRIMARY accent — accent violet
        'brand-green-dark': '#6B3AE0',  // darker purple for hovers
        'brand-magenta':    '#8254FD',
        'brand-red':        '#8254FD',  // (legacy token → purple)
        'brand-orange':     '#03BFBF',  // accent teal (secondary accent)
        'brand-purple':     '#8254FD',
        'brand-turquoise':  '#03BFBF',
        // ─── Domain accents — accent violet/turquoise family only ───
        'dom-people':    '#8254FD',  // accent violet
        'dom-finance':   '#03BFBF',  // accent teal
        'dom-client':    '#6B3AE0',  // deep purple
        'dom-projects':  '#0E8F94',  // deep teal
      },
      fontFamily: {
        sans: ['"Segoe UI"', 'Arial', 'system-ui', 'sans-serif'],
        display: ['"Segoe UI"', 'Arial', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        card:         '0 1px 2px rgba(16,18,29,0.04), 0 4px 16px rgba(16,18,29,0.06)',
        'card-hover': '0 2px 6px rgba(16,18,29,0.08), 0 12px 28px rgba(16,18,29,0.12)',
      },
      keyframes: {
        'fade-in-up': {
          '0%':   { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in-up': 'fade-in-up 0.4s ease-out both',
      },
    },
  },
  plugins: [],
};
export default config;

/** @type {import('tailwindcss').Config} */
// Paleta e fonte extraidas de docs/designs/painel-prototipo.html (mesma do AUTO-1).
module.exports = {
  content: [
    './templates/**/*.html',
    './static/js/**/*.js',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        mint: {
          50: '#F1FBF5', 100: '#E2F6E9', 200: '#BDE9C9', 300: '#94DBAA', 400: '#67C786',
          500: '#3FB063', 600: '#2A8E4A', 700: '#206E39', 800: '#17512A', 900: '#0F361C',
        },
        lavender: {
          50: '#F6F2FC', 100: '#EBE2F7', 200: '#C5B4E3', 300: '#A990D2', 400: '#8D6CC0', 500: '#7050AE',
        },
        sky: {
          50: '#F0F7FC', 100: '#E1EFF8', 200: '#B9D9EB', 300: '#85BFDC', 400: '#549EC4', 500: '#2B7EA9',
        },
        ink: {
          900: '#0A0A0A', 800: '#1A1A1A', 700: '#2D2D2D', 600: '#404040', 500: '#666666',
          400: '#999999', 300: '#C2C2C2', 200: '#E5E5E5', 100: '#F2F2F2', 50: '#FAFAFA',
        },
      },
    },
  },
  plugins: [],
};

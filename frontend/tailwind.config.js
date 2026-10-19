/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  // AdminDashboardPage's KPI_CARDS build a couple of color classes
  // dynamically from data (`bg-${card.color}-500` etc.), which Tailwind's
  // static file scanner can't see — safelisted explicitly instead. Scoped to
  // only the colors/shades actually used that way, to avoid bloating the CSS
  // bundle with every color/shade combination.
  safelist: [{ pattern: /(bg|text)-(red|amber|emerald|brand)-(400|500|600)(\/10)?/, variants: ['dark'] }],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'sans-serif'] },
      colors: {
        brand: {
          50: '#eef2ff',
          100: '#e0e7ff',
          400: '#818cf8',
          500: '#6366f1',
          600: '#4f46e5',
          700: '#4338ca',
          800: '#3730a3',
          900: '#312e81',
        },
      },
    },
  },
  plugins: [],
};

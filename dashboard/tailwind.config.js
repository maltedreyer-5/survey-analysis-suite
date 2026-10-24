// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
// Tailwind configuration of the dashboard. The stylesheet vendor/tailwind.css is
// built from it (no Tailwind script at runtime, so the page makes no external requests):
//   npx tailwindcss@3.4.17 -c tailwind.config.js -i tailwind.input.css -o vendor/tailwind.css --minify
// Class names must appear literally in index.html or js/*.js to be included.
module.exports = {
  content: ["./index.html", "./js/app.js", "./js/charts.js", "./js/i18n.js"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'sans-serif'],
      },
      colors: {
        brand: {
          50: '#eff6ff', 100: '#dbeafe', 200: '#bfdbfe', 300: '#93c5fd', 400: '#60a5fa',
          500: '#3b82f6', 600: '#2563eb', 700: '#1d4ed8', 800: '#1e40af', 900: '#1e3a8a',
        },
      },
    },
  },
};

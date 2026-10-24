# Third-party files in this folder

All files are used unchanged, at the versions named in their file names.

| File | Project | Version | Licence | Source |
|---|---|---|---|---|
| `plotly-2.27.0.min.js` | plotly.js (dist-min) | 2.27.0 | MIT, see `LICENSE.plotly.txt` | npm `plotly.js-dist-min@2.27.0` |
| `alpinejs-3.14.1.min.js` | Alpine.js | 3.14.1 | MIT (Copyright © 2019–2024 Caleb Porzio and contributors; the npm package ships no licence file, see https://github.com/alpinejs/alpine/blob/main/LICENSE.md) | npm `alpinejs@3.14.1` (`dist/cdn.min.js`) |
| `d3-7.9.0.min.js` | D3 | 7.9.0 | ISC, see `LICENSE.d3.txt` | npm `d3@7.9.0` (`dist/d3.min.js`) |
| `fonts/dm-sans-latin-*.woff2` | DM Sans | Fontsource 5.0.18 | SIL Open Font License 1.1, see `fonts/LICENSE.DM-Sans.txt` | npm `@fontsource/dm-sans@5.0.18` |
| `tailwind.css` | Tailwind CSS (built stylesheet) | 3.4.17 | MIT (Copyright © Tailwind Labs, Inc.) | built from `../tailwind.config.js` with `npx tailwindcss@3.4.17 -c tailwind.config.js -i tailwind.input.css -o vendor/tailwind.css --minify` |

Rebuild `tailwind.css` after adding new Tailwind classes to `index.html` or `js/*.js`.

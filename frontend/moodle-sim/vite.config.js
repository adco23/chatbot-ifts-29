import { defineConfig } from 'vite';

// Puerto distinto del widget (5173): así el iframe es cross-origin, como en el campus real.
export default defineConfig({
  server: { port: 5174, strictPort: true },
  preview: { port: 5174, strictPort: true },
});

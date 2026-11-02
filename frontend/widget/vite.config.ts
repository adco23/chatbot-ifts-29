import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Puerto fijo para que moodle-sim lo cargue desde otro origen (cross-origin, como en producción).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  preview: { port: 5173, strictPort: true },
});

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The recorded fixture stays at frontend/fixtures/ and is served statically,
// so the 5.9 MB JSON never enters the bundle graph.
export default defineConfig({
  plugins: [react()],
  publicDir: 'fixtures',
  server: { port: 5173 },
});

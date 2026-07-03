import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// base './' so the built app installs and runs from any self-hosted subpath.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // dev convenience: same-origin /api → the (Pass 2) FastAPI server
      "/api": "http://127.0.0.1:8000",
    },
  },
});

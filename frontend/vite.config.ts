import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // In dev the FastAPI backend runs on :8000; proxying lets the UI use relative /api paths
  // exactly as in production, where FastAPI serves the built UI itself.
  server: { proxy: { "/api": "http://localhost:8000" } },
  // The diagram layout engine (ELK) is a deliberately separate 1.5 MB chunk, loaded only
  // when a diagram first appears.
  build: { chunkSizeWarningLimit: 1600 },
});

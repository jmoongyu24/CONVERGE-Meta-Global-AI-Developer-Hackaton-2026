import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // In dev the FastAPI backend runs on :8000; proxying lets the UI use relative /api paths
  // exactly as in production, where FastAPI serves the built UI itself.
  server: { proxy: { "/api": "http://localhost:8000" } },
});

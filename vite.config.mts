import { defineConfig } from "vite";
import { svelte, vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Vite builds the Svelte UI into ./dist, which the Electron main process
// loads via `mainWindow.loadFile('dist/index.html')`.
//
// `root` points at src/ui so index.html lives with the UI source per
// CLAUDE.md's Project Structure. `base: './'` gives relative asset paths —
// required because Electron loads via file:// URLs.

export default defineConfig({
  root: resolve(__dirname, "src/ui"),
  base: "./",
  plugins: [
    svelte({
      preprocess: vitePreprocess(),
    }),
  ],
  build: {
    outDir: resolve(__dirname, "dist"),
    emptyOutDir: true,
    sourcemap: true,
    minify: false,
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name].js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name].[ext]",
      },
    },
  },
});

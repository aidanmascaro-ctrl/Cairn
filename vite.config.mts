import { defineConfig, type Plugin } from "vite";
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

// The dev server injects <style> tags and talks to its HMR websocket, both of
// which the strict CSP in src/ui/index.html refuses. Relax it for `vite` only;
// the production build keeps the policy exactly as written.
const relaxCspForDevServer: Plugin = {
  name: "cairn-relax-csp-for-dev-server",
  apply: "serve",
  transformIndexHtml: (html) =>
    html
      .replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")
      .replace("connect-src 'none'", "connect-src 'self' ws:"),
};

export default defineConfig({
  root: resolve(__dirname, "src/ui"),
  base: "./",
  plugins: [
    relaxCspForDevServer,
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

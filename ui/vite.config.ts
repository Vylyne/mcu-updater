import vue from "@vitejs/plugin-vue";
import { defineConfig, loadEnv } from "vite";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // App.vue's websocket and /access/info calls are same-origin by design -
  // that's what lets the built UI work unmodified behind Moonraker's own
  // nginx in production. `npm run dev` has nothing to talk to on its own
  // origin, so VITE_MOONRAKER_PROXY_TARGET points the dev server at a real
  // Moonraker instead (e.g. "http://printer.local:7125"). Set it in a local
  // ui/.env.local (untracked - *.local is in .gitignore - it names a printer
  // on your own network and has no business in git history). Unset, dev
  // behaves exactly as it always has: no proxy, nothing to connect to.
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const target = env.VITE_MOONRAKER_PROXY_TARGET;

  return {
    plugins: [vue()],
    server: target
      ? {
          proxy: {
            "/websocket": { target, ws: true },
            "/access": { target },
          },
        }
      : undefined,
    test: {
      environment: "jsdom",
    },
  };
});

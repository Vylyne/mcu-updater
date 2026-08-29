# mcu-updater UI

The standalone embeddable UI for the [mcu-updater](../README.md) Moonraker
agent. See [docs/standalone-ui.md](../docs/standalone-ui.md) in the repo root
for the install, release, and nginx runbook.

```bash
npm ci
npm run dev           # local dev server
npm run lint           # eslint
npm run typecheck      # vue-tsc --noEmit
npm run format:check   # prettier --check
npm run build           # -> dist/
npm run test            # vitest run
```

`npm run dev` talks to whatever's on its own origin — nothing, by default,
since the app's websocket and `/access/info` calls are same-origin (matching
how the built UI is served in production). To develop against a real printer,
set `VITE_MOONRAKER_PROXY_TARGET` in a local `.env.local` (untracked —
`*.local` is in `.gitignore`, and a printer's hostname has no business in git
history):

```ini
# ui/.env.local
VITE_MOONRAKER_PROXY_TARGET=http://your-printer.local:7125
```

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

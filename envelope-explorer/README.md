# The Envelope Explorer — desktop app

An Electron shell around the same interface the browser serves. It adds a native
window, a menu with keyboard shortcuts, remembered geometry, and an explicit
offline state when the backend is not running.

## Run

```bash
cd envelope-explorer
pnpm install          # installs electron
pnpm start            # opens the app against http://127.0.0.1:8802
```

Point it somewhere else with `EXPLORER_URL`:

```bash
EXPLORER_URL=http://127.0.0.1:8802/ pnpm start
```

## Build a macOS app

```bash
pnpm dist             # app bundle in dist/mac
pnpm dmg              # disk image
```

The build configuration lives in `package.json` under `build`. No code signing is
configured; signing and notarisation need an Apple developer identity.

## Shortcuts

| Key | Action |
|---|---|
| ⌘1 … ⌘7 | Feed, Journey, Execution, Why, Gantt, Books, Health |
| ⌘[ | Back (one step, same as Escape) |
| ⌘R | Reconnect to the service |
| ⌘E | Case file for the selected envelope |
| ⌘⇧E | Book report |
| ⌘P | Print — use *Save as PDF* |

## What it is not

The app holds no data and writes nothing. It is a window onto the service, which
is the only thing that reads the lake — and it is read-only by construction.

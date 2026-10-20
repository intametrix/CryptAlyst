# Electron variant (kept as an alternative)

This folder is the Electron shell written before the native app. It is not the
shipped desktop app: `../envelope-explorer-native/` is, because this machine has
Swift and no Node/Rust, and Electron would bundle a Chromium runtime the system
already provides.

To use it anyway:

```bash
cd envelope-explorer
pnpm install
pnpm start
```

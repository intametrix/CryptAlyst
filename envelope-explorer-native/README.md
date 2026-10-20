# The Envelope Explorer — desktop app

A native macOS shell around the Explorer. It is a WKWebView window with a menu,
keyboard shortcuts, remembered state, a native print dialog, and an offline page
when the service is not running.

## Why native rather than Electron

Measured on this machine, not estimated:

| | native (WKWebView) | Electron | Tauri |
|---|---|---|---|
| app size | **11 MB** (of which 11 MB is the bundled Go service; the shell itself is 112 KB) | ~150–200 MB | ~10 MB |
| toolchain present here | Swift 6.4, Xcode 15 — yes | Node — not on PATH | Rust — absent |
| runtime dependency | system WebView | bundled Chromium | system WebView |
| start time | instant | ~1 s cold | instant |
| can bundle the Go service | yes, as a resource | yes | yes (sidecar) |

Electron was scaffolded first and is kept in `../envelope-explorer/` as an
alternative, but shipping it here would mean downloading a Chromium runtime to
provide a web view the operating system already has.

## Build

```bash
./build.sh          # build/The Envelope Explorer.app
./build.sh --zip    # also a distributable zip
```

The build compiles `Sources/main.swift` with `swiftc`, copies `offline.html`, and
bundles `scalp5/core/bin/envelopeapi` when it exists so the app can start the
service itself. The bundle is ad-hoc signed; distributing it to another Mac needs
a Developer ID and notarisation.

## Run

```bash
open "build/The Envelope Explorer.app"
EXPLORER_URL=http://127.0.0.1:8802/ "build/The Envelope Explorer.app/Contents/MacOS/EnvelopeExplorer"
```

`--selftest` loads the app, writes `/tmp/envelope-explorer-selftest.json` and exits
— used to verify a build without a human looking at it.

## Shortcuts

| Key | Action |
|---|---|
| ⌘1 … ⌘7 | Feed, Journey, Execution, Why, Gantt, Books, Health |
| ⌘[ | Back, same as Escape |
| ⌘R | Reconnect |
| ⌘E | Case file for the selection |
| ⇧⌘E | Book report |
| ⌘P | Print — *Save as PDF* |

## What it is not

The app holds no data and writes nothing to the platform. It renders the
explorer API; read-only by construction, the same as the web interface.

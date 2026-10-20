// The Envelope Explorer — a native macOS shell.
//
// Why native rather than Electron: this machine has a Swift toolchain and no
// Node/Rust, the interface is already a web app, and the whole product is one
// operator on one Mac. A WKWebView shell is a ~1 MB bundle instead of ~150 MB,
// starts in a fraction of a second, and can supervise the Go service directly.
//
// The app holds no data. It renders the explorer API and nothing else.

import Cocoa
import WebKit

let defaultURL = "http://127.0.0.1:8802/"
let selftest = CommandLine.arguments.contains("--selftest")

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var service: Process?
    var loaded = false
    var target: String { ProcessInfo.processInfo.environment["EXPLORER_URL"] ?? defaultURL }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        connect()
        if selftest {
            DispatchQueue.main.asyncAfter(deadline: .now() + 25) { self.finish(ok: false, why: "timeout") }
        }
    }

    func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1600, height: 1000)
        window = NSWindow(contentRect: frame,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "The Envelope Explorer"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(calibratedRed: 0.027, green: 0.043, blue: 0.067, alpha: 1)
        window.minSize = NSSize(width: 1100, height: 700)
        window.center()

        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        webView = WKWebView(frame: frame, configuration: config)
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        webView.autoresizingMask = [.width, .height]
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // connect tries the service, then the offline page, and starts the service
    // itself when a bundled binary is present and nothing is listening.
    func connect() {
        guard let url = URL(string: target + "api/stats") else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 2.5
        URLSession.shared.dataTask(with: request) { data, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async {
                if ok {
                    self.load(self.target)
                } else {
                    self.startBundledServiceIfPossible()
                    self.loadOffline()
                }
            }
        }.resume()
    }

    func load(_ address: String) {
        guard let url = URL(string: address) else { return }
        loaded = false
        webView.load(URLRequest(url: url))
    }

    func loadOffline() {
        if let path = Bundle.main.path(forResource: "offline", ofType: "html") {
            webView.loadFileURL(URL(fileURLWithPath: path),
                                allowingReadAccessTo: URL(fileURLWithPath: Bundle.main.resourcePath ?? "/"))
        } else {
            load(self.target)
        }
    }

    // A bundled envelopeapi is started only if present; the rest of the platform
    // is expected to be running (scripts/start.sh), and the offline page says so.
    func startBundledServiceIfPossible() {
        guard service == nil, let bin = Bundle.main.path(forResource: "envelopeapi", ofType: nil) else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: bin)
        p.arguments = ["--port", "8802"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do {
            try p.run()
            service = p
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { self.connect() }
        } catch {
            service = nil
        }
    }

    // MARK: navigation

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded = true
        if selftest { finish(ok: true, why: "loaded") }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if selftest { finish(ok: false, why: error.localizedDescription) } else { loadOffline() }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if selftest { finish(ok: false, why: error.localizedDescription) } else { loadOffline() }
    }

    func finish(ok: Bool, why: String) {
        var payload: [String: Any] = ["ok": ok, "reason": why, "target": target,
                                      "title": webView.title ?? ""]
        if !ok, let current = webView.url?.absoluteString { payload["url"] = current }
        if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted]) {
            try? data.write(to: URL(fileURLWithPath: "/tmp/envelope-explorer-selftest.json"))
        }
        NSApp.terminate(nil)
    }

    // MARK: menu

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About The Envelope Explorer", action: nil, keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Reconnect", action: #selector(reconnect), keyEquivalent: "r")
        appMenu.addItem(withTitle: "Open in browser", action: #selector(openInBrowser), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        let views = ["list": "Feed", "journey": "Journey", "execution": "Execution",
                     "why": "Why", "gantt": "Gantt", "books": "Books", "health": "Health"]
        var n = 1
        for key in ["list", "journey", "execution", "why", "gantt", "books", "health"] {
            let item = NSMenuItem(title: views[key] ?? key, action: #selector(switchTab(_:)), keyEquivalent: "\(n)")
            item.representedObject = key
            item.keyEquivalentModifierMask = [.command]
            viewMenu.addItem(item)
            n += 1
        }
        viewMenu.addItem(.separator())
        let back = NSMenuItem(title: "Back", action: #selector(goBack), keyEquivalent: "[")
        back.keyEquivalentModifierMask = [.command]
        viewMenu.addItem(back)
        viewMenu.addItem(.separator())
        viewMenu.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        viewItem.submenu = viewMenu
        main.addItem(viewItem)

        let evidenceItem = NSMenuItem()
        let evidenceMenu = NSMenu(title: "Evidence")
        let caseItem = NSMenuItem(title: "Case file for selection", action: #selector(caseFile), keyEquivalent: "e")
        caseItem.keyEquivalentModifierMask = [.command]
        evidenceMenu.addItem(caseItem)
        let bookItem = NSMenuItem(title: "Book report", action: #selector(bookReport), keyEquivalent: "e")
        bookItem.keyEquivalentModifierMask = [.command, .shift]
        evidenceMenu.addItem(bookItem)
        evidenceMenu.addItem(.separator())
        let printItem = NSMenuItem(title: "Print…", action: #selector(doPrint), keyEquivalent: "p")
        printItem.keyEquivalentModifierMask = [.command]
        evidenceMenu.addItem(printItem)
        evidenceItem.submenu = evidenceMenu
        main.addItem(evidenceItem)

        NSApp.mainMenu = main
    }

    @objc func reconnect() { connect() }
    @objc func reload() { webView.reload() }
    @objc func openInBrowser() { NSWorkspace.shared.open(URL(string: target)!) }
    @objc func goBack() { webView.evaluateJavaScript("goBack()") }
    @objc func switchTab(_ sender: NSMenuItem) {
        let key = sender.representedObject as? String ?? "list"
        webView.evaluateJavaScript("document.querySelector('.tab[data-view=\"\(key)\"]').click()")
    }
    @objc func caseFile() {
        webView.evaluateJavaScript("(state.sel == null ? '' : '/api/case.html?id=' + state.sel)") { result, _ in
            guard let path = result as? String, !path.isEmpty else {
                let alert = NSAlert()
                alert.messageText = "Select an envelope first"
                alert.informativeText = "The case file is written for one decision or fill."
                alert.runModal()
                return
            }
            self.load(self.target + path)
        }
    }
    @objc func bookReport() {
        webView.evaluateJavaScript("(window.__bookEngine || 'grid')") { result, _ in
            let engine = (result as? String) ?? "grid"
            self.load(self.target + "api/book.html?engine=" + engine)
        }
    }
    @objc func doPrint() {
        let info = NSPrintInfo.shared
        let op = webView.printOperation(with: info)
        op.showsPrintPanel = true
        op.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { service?.terminate() }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()

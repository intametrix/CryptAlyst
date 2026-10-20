// The Envelope Explorer for iPhone and iPad.
//
// Same service, same interface: a WKWebView around the explorer API with a
// compact toolbar for reload and back, sized for both phone and tablet. The app
// holds no data — it renders what the service serves.
import SwiftUI
import WebKit

let explorerURL = URL(string: ProcessInfo.processInfo.environment["EXPLORER_URL"] ?? "http://127.0.0.1:8802/")!

struct WebView: UIViewRepresentable {
    @Binding var loading: Bool
    @Binding var canGoBack: Bool
    let reloadToken: Int

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.allowsBackForwardNavigationGestures = true
        web.isOpaque = false
        web.backgroundColor = UIColor(red: 0.027, green: 0.043, blue: 0.067, alpha: 1)
        web.load(URLRequest(url: explorerURL))
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {
        if context.coordinator.lastReload != reloadToken {
            context.coordinator.lastReload = reloadToken
            web.load(URLRequest(url: explorerURL))
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var parent: WebView
        var lastReload = 0
        init(_ parent: WebView) { self.parent = parent; self.lastReload = parent.reloadToken }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            parent.loading = true
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            parent.loading = false
            parent.canGoBack = webView.canGoBack
        }
        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            parent.loading = false
        }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            parent.loading = false
            // a failed load shows the offline page, which explains how to start the service
            if let path = Bundle.main.path(forResource: "offline", ofType: "html") {
                webView.loadFileURL(URL(fileURLWithPath: path), allowingReadAccessTo: URL(fileURLWithPath: path).deletingLastPathComponent())
            }
        }
    }
}

struct ContentView: View {
    @State private var loading = true
    @State private var canGoBack = false
    @State private var reloadToken = 0

    var body: some View {
        NavigationStack {
            WebView(loading: $loading, canGoBack: $canGoBack, reloadToken: reloadToken)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle("Envelope Explorer")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) {
                        Button {
                            reloadToken += 1
                        } label: { Image(systemName: "arrow.clockwise") }
                    }
                    ToolbarItem(placement: .principal) {
                        Text("The Envelope Explorer").font(.headline)
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        if loading { ProgressView() }
                    }
                }
        }
    }
}

@main
struct EnvelopeExplorerApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

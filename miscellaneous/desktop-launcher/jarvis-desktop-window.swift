import Cocoa
import WebKit

/// Native macOS fallback window when Electron is unavailable.
/// Usage: jarvis-desktop-window [url]
let startURL = CommandLine.arguments.count > 1
  ? CommandLine.arguments[1]
  : "http://127.0.0.1:3000"

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
  var window: NSWindow!
  var webView: WKWebView!
  var retryTimer: Timer?

  func applicationDidFinishLaunching(_ notification: Notification) {
    let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
    let width = min(1300, screen.width - 80)
    let height = min(850, screen.height - 60)
    let rect = NSRect(
      x: screen.midX - width / 2,
      y: screen.midY - height / 2,
      width: width,
      height: height
    )

    window = NSWindow(
      contentRect: rect,
      styleMask: [.titled, .closable, .miniaturizable, .resizable],
      backing: .buffered,
      defer: false
    )
    window.title = "J.A.R.V.I.S. Workspace"
    window.minSize = NSSize(width: 1000, height: 650)
    window.isReleasedWhenClosed = false
    window.center()
    window.setFrameAutosaveName("JarvisDesktopWindow")

    let config = WKWebViewConfiguration()
    config.preferences.setValue(true, forKey: "allowFileAccessFromFileURLs")
    webView = WKWebView(frame: window.contentView?.bounds ?? rect, configuration: config)
    webView.autoresizingMask = [.width, .height]
    webView.navigationDelegate = self
    window.contentView = webView

    loadUI()
    window.makeKeyAndOrderFront(nil)
    NSApp.activate(ignoringOtherApps: true)
  }

  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
    true
  }

  func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
    window.makeKeyAndOrderFront(nil)
    return true
  }

  func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
    scheduleRetry()
  }

  func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
    scheduleRetry()
  }

  func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
    retryTimer?.invalidate()
    retryTimer = nil
  }

  private func loadUI() {
    guard let url = URL(string: startURL) else { return }
    webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10))
  }

  private func scheduleRetry() {
    retryTimer?.invalidate()
    retryTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] timer in
      guard let self else {
        timer.invalidate()
        return
      }
      self.loadUI()
    }
  }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()

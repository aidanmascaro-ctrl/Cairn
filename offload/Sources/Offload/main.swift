import AppKit
import OffloadCore
import ServiceManagement
import SwiftUI

/// Menu-bar only app: a status item that opens a popover, and pops it open by itself
/// whenever a camera card is plugged in. While a copy runs the icon grows into a pill
/// with a progress ring and a countdown, then shrinks back to the card icon.
final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private let model = AppModel()
    private var statusItem: NSStatusItem!
    private let popover = NSPopover()
    private var refreshTimer: Timer?

    private static let refreshInterval: TimeInterval = 0.5
    private static let ringSize: CGFloat = 14
    private static let iconPointSize: CGFloat = 15.5
    private static let ringColor = NSColor.systemPink

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.action = #selector(toggle)
        statusItem.button?.target = self
        statusItem.button?.imagePosition = .imageLeading
        statusItem.button?.wantsLayer = true
        showIdleIcon()

        popover.behavior = .transient
        // Popover follows the SwiftUI content's size. SwiftUI eases that size frame by
        // frame (AnimatedSize), so the popover's own resize animation is switched off
        // while it's open — two animations fighting is what made it jump.
        popover.delegate = self
        let host = NSHostingController(rootView: PopoverView(model: model, library: model.library))
        host.sizingOptions = [.preferredContentSize]
        popover.contentViewController = host
        popover.animates = true

        model.onCardInserted = { [weak self] in self?.show() }
        model.start()
        if model.rootPath == nil || !model.cards.isEmpty { show() }

        refreshTimer = Timer.scheduledTimer(withTimeInterval: Self.refreshInterval, repeats: true) { [weak self] _ in
            self?.refreshStatusItem()
        }
        registerLoginItemOnce()
    }

    // MARK: Start at login

    private static let loginItemKey = "loginItemRegistered"

    /// Adds Offload to Login Items the first time it runs from an Applications folder
    /// (not a build folder that gets wiped). Only once: if you switch it off in
    /// System Settings → General → Login Items, it stays off.
    private func registerLoginItemOnce() {
        guard Bundle.main.bundlePath.contains("/Applications/"),
              !UserDefaults.standard.bool(forKey: Self.loginItemKey)
        else { return }
        do {
            try SMAppService.mainApp.register()
            UserDefaults.standard.set(true, forKey: Self.loginItemKey)
        } catch {
            NSLog("Offload: couldn't add itself to Login Items: \(error.localizedDescription)")
        }
    }

    // MARK: Menu-bar pill

    private func refreshStatusItem() {
        guard let progress = model.activeCopy else { return showIdleIcon() }
        guard let button = statusItem.button else { return }
        button.image = Self.ring(progress: progress.fraction)
        let label = progress.secondsRemaining().map(Format.clock) ?? "\(Int(progress.fraction * 100))%"
        button.attributedTitle = NSAttributedString(string: " \(label)", attributes: [
            .font: NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize - 1, weight: .semibold),
        ])
        button.layer?.backgroundColor = NSColor.black.withAlphaComponent(0.55).cgColor
        button.layer?.cornerRadius = (button.bounds.height - 2) / 2
    }

    private func showIdleIcon() {
        guard let button = statusItem.button, button.title != "" || button.image?.isTemplate != true else { return }
        // A touch larger than the default so it sits level with the other menu-bar icons.
        let icon = NSImage(systemSymbolName: "sdcard", accessibilityDescription: "Offload")?
            .withSymbolConfiguration(.init(pointSize: Self.iconPointSize, weight: .regular))
        icon?.isTemplate = true
        button.image = icon
        button.title = ""
        button.layer?.backgroundColor = nil
    }

    /// A small progress ring, like a parking-app countdown: faint track + coloured arc from 12 o'clock.
    private static func ring(progress: Double) -> NSImage {
        let size = NSSize(width: ringSize, height: ringSize)
        let image = NSImage(size: size, flipped: false) { rect in
            let lineWidth: CGFloat = 2
            let inset = rect.insetBy(dx: lineWidth / 2, dy: lineWidth / 2)
            let track = NSBezierPath(ovalIn: inset)
            track.lineWidth = lineWidth
            NSColor.white.withAlphaComponent(0.25).setStroke()
            track.stroke()

            let arc = NSBezierPath()
            let center = NSPoint(x: rect.midX, y: rect.midY)
            let end = 90 - 360 * CGFloat(min(max(progress, 0.01), 1))
            arc.appendArc(withCenter: center, radius: inset.width / 2, startAngle: 90, endAngle: end, clockwise: true)
            arc.lineWidth = lineWidth
            arc.lineCapStyle = .round
            ringColor.setStroke()
            arc.stroke()
            return true
        }
        image.isTemplate = false
        return image
    }

    // MARK: Popover

    func popoverDidShow(_ notification: Notification) { popover.animates = false }
    func popoverWillClose(_ notification: Notification) { popover.animates = true }

    @objc private func toggle() {
        popover.isShown ? popover.performClose(nil) : show()
    }

    private func show() {
        guard let button = statusItem.button, !popover.isShown else { return }
        NSApp.activate(ignoringOtherApps: true)
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()

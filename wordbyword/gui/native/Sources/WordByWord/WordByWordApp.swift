import AppKit
import SwiftUI

@main
struct WordByWordApp: App {
    @NSApplicationDelegateAdaptor(StudioAppDelegate.self) private var delegate
    @StateObject private var model = StudioModel()

    var body: some Scene {
        Window("WordByWord", id: "studio") {
            StudioView(model: model)
                .frame(minWidth: 1000, minHeight: 680)
                .environment(\.colorScheme, .light)
                .onAppear { delegate.model = model }
        }
        .defaultSize(width: 1180, height: 820)
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button(model.t("Choose Workspace…")) { model.chooseWorkspace() }
                    .keyboardShortcut("o", modifiers: [.command, .shift])
                    .disabled(model.isRunning)
            }
            CommandMenu(model.t("Workspace")) {
                Button(model.t("Refresh Files")) { model.refresh() }
                    .keyboardShortcut("r", modifiers: [.command])
                    .disabled(model.isRunning)
                Button(model.t("Show Run Journal")) { model.showsLog = true }
                    .keyboardShortcut("j", modifiers: [.command])
                Divider()
                Button(model.t("Reveal in Finder")) { model.reveal(path: model.workspace.path) }
                Button(model.t("Review Folder Layout")) { model.showMigrationPlan() }
                    .disabled(model.isRunning)
            }
        }
    }
}

@MainActor
final class StudioAppDelegate: NSObject, NSApplicationDelegate {
    weak var model: StudioModel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.appearance = NSAppearance(named: .aqua)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        sender.windows.first?.makeKeyAndOrderFront(nil)
        sender.activate(ignoringOtherApps: true)
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model, model.isRunning else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = model.t("A workflow is still running.")
        alert.informativeText = model.t("Stopping waits for the worker to clean up. Files already written may remain.")
        alert.addButton(withTitle: model.t("Keep Running"))
        alert.addButton(withTitle: model.t("Stop and Quit"))
        guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
        model.cancelRun()
        Task { @MainActor in
            while model.isRunning {
                try? await Task.sleep(for: .milliseconds(200))
            }
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

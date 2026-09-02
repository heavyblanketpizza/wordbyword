import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

@MainActor
final class StudioModel: ObservableObject {
    @Published var tab: StudioTab = .dictionary
    @Published var grade = "grade2"
    @Published var workspace: URL
    @Published var snapshot: WorkspaceSnapshot?
    @Published var settings = StudioSettings() {
        didSet {
            if isRunning, !isRestoringSettings, settings != oldValue {
                isRestoringSettings = true
                settings = oldValue
                isRestoringSettings = false
            }
        }
    }
    @Published var isLoading = false
    @Published var isRunning = false
    @Published var isCancelling = false
    @Published var error: String?
    @Published var notice: String?
    @Published var logs: [LogEntry] = []
    @Published var lastResult: RunResult?
    @Published var showsLog = false
    @Published var preview: PreviewDocument?
    @Published var distribution: DictionaryDistribution?
    @Published var selectedReport = ""
    @Published var pendingAction: StudioAction?
    @Published private(set) var activeAction: StudioAction?
    @Published private var progressValue: Double?

    private let runtime: StudioRuntime
    private var readClient: BridgeClient?
    private var runClient: BridgeClient?
    private var readID = UUID()
    private var workspaceID = UUID()
    private var runID: UUID?
    private var isSavingSettings = false
    private var isRestoringSettings = false
    private var settingsError: String?
    private var reviewedMigration: (workspace: String, canApply: Bool, count: Int)?

    var language: StudioLanguage { settings.language }
    func t(_ english: String) -> String { language.text(english) }
    func f(_ english: String, _ arguments: CVarArg...) -> String {
        language.formatted(english, arguments: arguments)
    }

    func displayPath(_ path: String) -> String {
        guard !path.isEmpty else { return path }
        let root = workspace.standardizedFileURL.path
        let target = (path.hasPrefix("/")
                      ? URL(fileURLWithPath: path)
                      : workspace.appendingPathComponent(path)).standardizedFileURL.path
        if target == root { return "~" }
        let prefix = root.hasSuffix("/") ? root : root + "/"
        guard target.hasPrefix(prefix) else { return path }
        return "~/" + target.dropFirst(prefix.count)
    }

    var progress: Double? { isRunning ? progressValue : nil }
    var isDictionaryBuildRunning: Bool { isRunning && activeAction == .buildDictionary }
    var phase: String {
        if isCancelling { return "Waiting for the process to stop" }
        if isRunning { return logs.last?.phase.replacingOccurrences(of: "_", with: " ") ?? "Starting" }
        if isLoading { return "Reading workspace" }
        if error != nil { return "Needs attention" }
        if let lastResult { return lastResult.succeeded ? "Completed" : lastResult.status.capitalized }
        return "Ready when you are"
    }

    init(runtime: StudioRuntime = .resolve(), autoRefresh: Bool = true) {
        self.runtime = runtime
        self.workspace = runtime.workspace
        if autoRefresh { loadSettingsAndRefresh() }
    }

    func refresh() {
        guard !isRunning, !isSavingSettings else { return }
        let (id, client, root, targetGrade) = beginRead()
        error = nil
        Task { [weak self] in
            do {
                var result: WorkspaceSnapshot?
                for try await packet in client.packets(for: BridgeRequest(operation: "snapshot", grade: targetGrade)) {
                    if case .snapshot(let value) = packet { result = value }
                }
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                guard let result, result.grade == targetGrade,
                      URL(fileURLWithPath: result.workspace).standardizedFileURL.resolvingSymlinksInPath()
                        == root.standardizedFileURL.resolvingSymlinksInPath() else {
                    throw StudioFailure("The workspace response did not match the selected folder and grade.")
                }
                self.snapshot = result
                if !result.files["archive", default: []].contains(where: { $0.path == self.selectedReport }) {
                    self.selectedReport = ""
                }
                self.error = self.settingsError
                self.finishRead(id)
            } catch {
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                self.error = error.localizedDescription
                self.finishRead(id)
            }
        }
    }

    func changeGrade(_ value: String) {
        guard value == "grade2", grade != value, !isRunning, !isSavingSettings else { return }
        grade = "grade2"
        refresh()
    }

    func chooseWorkspace() {
        guard !isRunning, !isSavingSettings else { return }
        let panel = NSOpenPanel()
        panel.title = t("Choose a WordByWord workspace")
        panel.prompt = t("Use workspace")
        panel.message = t("Choose the folder containing your dictionary, classroom, and vocabulary folders. A new folder stays empty until you import or run a workflow.")
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        panel.directoryURL = workspace
        panel.begin { [weak self] response in
            Task { @MainActor in
                guard let self, response == .OK, let url = panel.url,
                      !self.isRunning, !self.isSavingSettings else { return }
                self.readClient?.cancel()
                self.readID = UUID()
                self.workspaceID = UUID()
                self.workspace = url.standardizedFileURL
                self.snapshot = nil
                self.settings = StudioSettings()
                self.settingsError = nil
                self.error = nil
                self.notice = nil
                self.preview = nil
                self.distribution = nil
                self.pendingAction = nil
                self.selectedReport = ""
                self.lastResult = nil
                self.logs = []
                self.reviewedMigration = nil
                self.loadSettingsAndRefresh()
            }
        }
    }

    func importFiles(group: String) {
        guard !isRunning, !isLoading, !isSavingSettings else { return }
        guard let snapshot, !snapshot.legacyLayout else {
            error = "Organize the legacy workspace before importing files, so sources go into the correct grade folders."
            return
        }
        let extensions: [String]
        switch group {
        case "sources": extensions = ["pdf", "txt", "md"]
        case "class_sources": extensions = ["pdf"]
        case "archive": extensions = ["docx", "pdf"]
        default:
            error = "This collection does not accept imported files."
            return
        }
        let panel = NSOpenPanel()
        panel.title = t(group == "archive" ? "Import archived handouts" : "Import source material")
        panel.message = t("Files are copied into the selected grade. Originals are left untouched and existing files are never overwritten.")
        panel.prompt = t("Import copies")
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.allowedContentTypes = extensions.compactMap { UTType(filenameExtension: $0) }
        let targetWorkspace = workspace
        let targetGrade = grade
        panel.begin { [weak self] response in
            Task { @MainActor in
                guard let self, response == .OK, !panel.urls.isEmpty,
                      !self.isRunning, !self.isLoading,
                      self.workspace == targetWorkspace, self.grade == targetGrade else { return }
                self.start(request: BridgeRequest(operation: "import", grade: targetGrade,
                                                  destination: group, files: panel.urls.map(\.path)),
                           title: "Import files")
            }
        }
    }

    func loadPreview(path: String) {
        guard !isRunning, !isSavingSettings else { return }
        let (id, client, root, targetGrade) = beginRead()
        error = nil
        Task { [weak self] in
            do {
                var result: PreviewDocument?
                for try await packet in client.packets(for: BridgeRequest(operation: "preview", grade: targetGrade, path: path)) {
                    if case .preview(let value) = packet { result = value }
                }
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                self.preview = result
                self.finishRead(id)
            } catch {
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                self.error = error.localizedDescription
                self.finishRead(id)
            }
        }
    }

    var hasDictionaryDistribution: Bool { dictionaryDistributionArtifact() != nil }

    func requestDictionaryBuild() {
        guard !isRunning, !isLoading, !isSavingSettings else { return }
        if snapshot?.legacyLayout == true {
            notice = "Organize the current files into the Grade 2 folder before extracting vocabulary. The visualization remains available for existing saved dictionaries."
            showMigrationPlan()
        } else {
            requestRun(.buildDictionary)
        }
    }

    func loadDictionaryDistribution(requireFinal: Bool = false) {
        guard !isRunning, !isSavingSettings else { return }
        guard let artifact = dictionaryDistributionArtifact(requireFinal: requireFinal) else {
            error = requireFinal
                ? "The build completed without a saved stage-15 final dictionary, so the visualization could not be opened."
                : "Build dictionary stages 00–15 before opening the vocabulary visualization."
            return
        }
        let (id, client, root, targetGrade) = beginRead()
        error = nil
        Task { [weak self] in
            do {
                var result: DictionaryDistribution?
                for try await packet in client.packets(for: BridgeRequest(
                    operation: "distribution", grade: targetGrade, path: artifact.path
                )) {
                    if case .distribution(let value) = packet { result = value }
                }
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                guard let result, result.path == artifact.path else {
                    throw StudioFailure("The vocabulary visualization did not match the selected dictionary artifact.")
                }
                self.distribution = result
                self.error = self.settingsError
                self.finishRead(id)
            } catch {
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                self.error = error.localizedDescription
                self.finishRead(id)
            }
        }
    }

    func showMigrationPlan() {
        guard !isRunning, !isSavingSettings else { return }
        let (id, client, root, targetGrade) = beginRead()
        reviewedMigration = nil
        error = nil
        Task { [weak self] in
            do {
                var result: PreviewDocument?
                for try await packet in client.packets(for: BridgeRequest(operation: "migration_plan")) {
                    if case .preview(let value) = packet { result = value }
                }
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                self.preview = result
                self.reviewedMigration = (root.path, result?.canApply == true, result?.plannedCount ?? 0)
                self.finishRead(id)
            } catch {
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                self.error = error.localizedDescription
                self.finishRead(id)
            }
        }
    }

    func arguments(for action: StudioAction) throws -> [String] {
        guard let snapshot, snapshot.grade == grade,
              URL(fileURLWithPath: snapshot.workspace).standardizedFileURL.resolvingSymlinksInPath()
                == workspace.standardizedFileURL.resolvingSymlinksInPath() else {
            throw StudioFailure("Wait for the selected workspace to finish loading.")
        }
        if action == .migrateWorkspace {
            guard let reviewedMigration, reviewedMigration.workspace == workspace.path,
                  reviewedMigration.canApply, reviewedMigration.count > 0 else {
                throw StudioFailure("Review the workspace migration plan first. Conflicts must be resolved before applying it.")
            }
        }
        return try StudioCommandBuilder.arguments(for: action, snapshot: snapshot,
                                                   settings: settings, selectedReport: selectedReport)
    }

    func requestRun(_ action: StudioAction) {
        guard !isRunning, !isLoading, !isSavingSettings else { return }
        do {
            _ = try arguments(for: action)
            error = nil
            pendingAction = action
        } catch { self.error = error.localizedDescription }
    }

    func confirmRun() {
        guard let action = pendingAction, !isRunning, !isLoading, !isSavingSettings else { return }
        pendingAction = nil
        do {
            let arguments = try arguments(for: action)
            start(request: BridgeRequest(operation: "run", arguments: arguments), title: action.title, action: action)
        } catch { self.error = error.localizedDescription }
    }

    func cancelRun() {
        guard isRunning, !isCancelling else { return }
        isCancelling = true
        append(LogEntry(phase: "Stopping", detail: "Interrupt requested. Waiting for Python to exit and finish writing its current status."))
        runClient?.cancel()
    }

    func saveSettings() {
        guard !isRunning, !isLoading, !isSavingSettings else { return }
        do {
            let value = try settings.validated()
            let root = workspace
            let id = workspaceID
            isSavingSettings = true
            isLoading = true
            Task { [weak self] in
                do {
                    try await Task.detached(priority: .userInitiated) { try value.write(to: root) }.value
                    guard let self, self.workspaceID == id else { return }
                    self.settings = value
                    self.settingsError = nil
                    self.error = nil
                    self.notice = "Settings saved for this workspace. Credentials are never stored here."
                    self.isSavingSettings = false
                    self.isLoading = false
                } catch {
                    guard let self, self.workspaceID == id else { return }
                    self.error = self.f("Settings could not be saved: %@", self.t(error.localizedDescription))
                    self.isSavingSettings = false
                    self.isLoading = false
                }
            }
        } catch { self.error = error.localizedDescription }
    }

    func reveal(path: String) {
        guard !path.isEmpty, Int(path) == nil else { return }
        var url = (path.hasPrefix("/") ? URL(fileURLWithPath: path)
                   : workspace.appendingPathComponent(path)).standardizedFileURL
        let manager = FileManager.default
        while !manager.fileExists(atPath: url.path), url.path != "/" {
            url.deleteLastPathComponent()
        }
        var directory: ObjCBool = false
        if manager.fileExists(atPath: url.path, isDirectory: &directory) {
            if directory.boolValue { NSWorkspace.shared.open(url) }
            else { NSWorkspace.shared.activateFileViewerSelecting([url]) }
        }
    }

    private func loadSettingsAndRefresh() {
        let root = workspace
        let id = workspaceID
        isLoading = true
        Task { [weak self] in
            do {
                let value = try await Task.detached(priority: .utility) { try StudioSettings.read(from: root) }.value
                guard let self, self.workspaceID == id else { return }
                self.settings = value
                self.settingsError = nil
                self.refresh()
            } catch {
                guard let self, self.workspaceID == id else { return }
                self.settingsError = self.f("Saved settings could not be loaded. Defaults are shown. %@", self.t(error.localizedDescription))
                self.refresh()
            }
        }
    }

    private func makeClient() -> BridgeClient {
        BridgeClient(python: runtime.python, moduleRoot: runtime.moduleRoot, workspace: workspace)
    }

    private func beginRead() -> (UUID, BridgeClient, URL, String) {
        readClient?.cancel()
        let id = UUID()
        let client = makeClient()
        readID = id
        readClient = client
        isLoading = true
        return (id, client, workspace, grade)
    }

    private func isCurrentRead(_ id: UUID, root: URL, grade: String) -> Bool {
        readID == id && workspace == root && self.grade == grade
    }

    private func finishRead(_ id: UUID) {
        guard readID == id else { return }
        isLoading = false
        readClient = nil
    }

    private func start(request: BridgeRequest, title: String, action: StudioAction? = nil) {
        guard !isRunning, !isSavingSettings else { return }
        readClient?.cancel()
        readID = UUID()
        isLoading = false
        let client = makeClient()
        let id = UUID()
        runID = id
        runClient = client
        activeAction = action
        isRunning = true
        isCancelling = false
        progressValue = nil
        error = nil
        notice = nil
        distribution = nil
        lastResult = nil
        logs = [LogEntry(phase: "Starting", detail: title)]
        showsLog = true
        Task { [weak self] in
            var result: RunResult?
            var failure: String?
            do {
                for try await packet in client.packets(for: request) {
                    guard let self, self.runID == id else { continue }
                    switch packet {
                    case .event(let event):
                        self.append(LogEntry(event: event))
                        self.progressValue = event.total > 0
                            ? min(1, max(0, Double(event.current) / Double(event.total))) : nil
                    case .result(let value): result = value
                    default: break
                    }
                }
            } catch { failure = error.localizedDescription }
            guard let self, self.runID == id else { return }
            self.lastResult = failure == nil ? result : nil
            self.runClient = nil
            self.runID = nil
            self.isRunning = false
            self.activeAction = nil
            self.isCancelling = false
            self.progressValue = nil
            self.reviewedMigration = nil
            if let failure {
                self.error = failure
                self.append(LogEntry(phase: "Failed", detail: failure, status: "failed", error: failure))
            } else if let result {
                let completion = LogEntry(phase: result.succeeded ? "Complete" : result.status.capitalized,
                                          detail: result.detail, status: result.status,
                                          artifacts: result.artifacts, error: result.error)
                if let last = self.logs.last, last.phase.lowercased() == "complete",
                   last.status == completion.status, last.status != "running",
                   last.detail == completion.detail, last.error == completion.error {
                    // The core emits its terminal event before the result packet.
                    // Retain one summary with the final result's authoritative data.
                    self.logs[self.logs.count - 1] = completion
                } else {
                    self.append(completion)
                }
                if result.succeeded { self.notice = result.detail }
                else if result.status == "cancelled" { self.notice = result.detail }
                else { self.error = result.error.isEmpty ? result.detail : result.error }
            }
            // Refresh after both success and failure; partial outputs may have been saved.
            let operationError = self.error
            self.refreshAfterRun(preserving: operationError,
                                 showDistribution: action == .buildDictionary && result?.succeeded == true)
        }
    }

    private func refreshAfterRun(preserving operationError: String?, showDistribution: Bool = false) {
        let (id, client, root, targetGrade) = beginRead()
        Task { [weak self] in
            do {
                var result: WorkspaceSnapshot?
                for try await packet in client.packets(for: BridgeRequest(operation: "snapshot", grade: targetGrade)) {
                    if case .snapshot(let value) = packet { result = value }
                }
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                if let result, result.grade == targetGrade,
                   URL(fileURLWithPath: result.workspace).standardizedFileURL.resolvingSymlinksInPath()
                    == root.standardizedFileURL.resolvingSymlinksInPath() {
                    self.snapshot = result
                    if !result.files["archive", default: []].contains(where: { $0.path == self.selectedReport }) {
                        self.selectedReport = ""
                    }
                } else {
                    throw StudioFailure("The refreshed workspace did not match the selected folder and grade.")
                }
                self.error = operationError ?? self.settingsError
                self.finishRead(id)
                if showDistribution, operationError == nil { self.loadDictionaryDistribution(requireFinal: true) }
            } catch {
                guard let self, self.isCurrentRead(id, root: root, grade: targetGrade) else { return }
                self.error = operationError ?? error.localizedDescription
                self.finishRead(id)
            }
        }
    }

    private func append(_ entry: LogEntry) {
        logs.append(entry)
        if logs.count > 500 { logs.removeFirst(logs.count - 500) }
    }

    private func dictionaryDistributionArtifact(requireFinal: Bool = false) -> StudioArtifact? {
        if requireFinal {
            return snapshot?.artifacts.first { $0.id == "final_dictionary" && $0.exists }
        }
        let priority = [
            "final_dictionary", "meaning_dictionary_candidates", "generated_example_dictionary",
            "linked_dictionary_candidates", "cleaned_dictionary_candidates", "dictionary_candidates",
        ]
        let available = Dictionary(uniqueKeysWithValues: (snapshot?.artifacts ?? [])
            .filter(\.exists).map { ($0.id, $0) })
        return priority.compactMap { available[$0] }.first
    }
}

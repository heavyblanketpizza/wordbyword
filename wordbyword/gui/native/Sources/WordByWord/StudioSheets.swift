import AppKit
import SwiftUI

struct SettingsSheet: View {
    @ObservedObject var model: StudioModel
    let close: () -> Void
    @State private var draft: StudioSettings
    @State private var validationError: String?

    init(model: StudioModel, close: @escaping () -> Void) {
        self.model = model
        self.close = close
        _draft = State(initialValue: model.settings)
    }

    var body: some View {
        VStack(spacing: 0) {
            SheetHeading(eyebrow: "STUDIO SETTINGS", title: "Your tools. Your defaults.", close: close)
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 12) {
                        SectionLabel("LANGUAGE MODEL")
                        settingRow("Provider") {
                            Menu {
                                ForEach(["ollama", "openai", "anthropic", "google"], id: \.self) { provider in
                                    Button(providerName(provider)) {
                                        if draft.provider != provider {
                                            draft.provider = provider
                                            draft.model = provider == "ollama" ? "gemma4:31b" : ""
                                        }
                                    }
                                }
                            } label: {
                                Text(providerName(draft.provider))
                            }
                            .menuStyle(.borderlessButton)
                            .padding(.horizontal, 11)
                            .frame(maxWidth: .infinity, minHeight: 36, alignment: .leading)
                            .overlay { RoundedRectangle(cornerRadius: 5).strokeBorder(StudioTheme.ink, lineWidth: 1) }
                        }
                        settingRow("Model") {
                            studioField("Model name", text: $draft.model)
                                .accessibilityLabel("Language model name")
                        }
                        settingRow("Base URL") {
                            studioField("Provider default", text: $draft.baseURL)
                                .accessibilityLabel("Model provider base URL")
                        }
                        Text(providerHelp)
                            .font(StudioType.detail)
                            .foregroundStyle(StudioTheme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Rectangle().fill(StudioTheme.hairline).frame(height: 1)
                    HStack(alignment: .top, spacing: 24) {
                        VStack(alignment: .leading, spacing: 14) {
                            SectionLabel("DICTIONARY")
                            MonoToggle("Include numbers", isOn: $draft.includeNumbers)
                            MonoToggle("Include punctuation", isOn: $draft.includePunctuation)
                            MonoToggle("Force a fresh build", isOn: $draft.force,
                                       detail: "Ignore saved checkpoints and regenerate existing outputs.")
                        }
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                        Rectangle().fill(StudioTheme.hairline).frame(width: 1, height: 158)
                        VStack(alignment: .leading, spacing: 14) {
                            SectionLabel("CLASSROOM")
                            MonoToggle("Parse PDFs with PaddleOCR-VL", isOn: $draft.parsePDF,
                                       detail: "Uses the project's separate OCR environment.")
                            VStack(spacing: 12) {
                                Stepper("Render · \(draft.renderDPI) DPI", value: $draft.renderDPI, in: 72...600, step: 36)
                                Stepper("Handout · \(draft.bodyFontSize) pt", value: $draft.bodyFontSize, in: 11...15)
                            }
                            .font(.system(size: 12.5, weight: .medium))
                        }
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                    if let validationError {
                        Label(validationError, systemImage: "exclamationmark.circle")
                            .font(StudioType.detail)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 22)
            }
            .frame(maxHeight: 460)
            SheetFooter {
                Text("Saved only in this workspace.\nAPI keys are never stored here.")
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                Spacer()
                Button("Cancel", action: close)
                    .buttonStyle(MonochromeButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button("Save defaults") {
                    do {
                        model.settings = try draft.validated()
                        model.saveSettings()
                        close()
                    } catch { validationError = error.localizedDescription }
                }
                .buttonStyle(MonochromeButtonStyle(primary: true))
                .keyboardShortcut(.defaultAction)
            }
        }
        .frame(width: 760)
    }

    private func settingRow<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 16) {
            Text(title).font(.system(size: 13, weight: .medium)).frame(width: 86, alignment: .leading)
            content()
        }
    }

    private func studioField(_ placeholder: String, text: Binding<String>) -> some View {
        TextField(placeholder, text: text)
            .font(.system(size: 13, design: .monospaced))
            .textFieldStyle(.plain)
            .padding(.horizontal, 11)
            .frame(height: 36)
            .overlay { RoundedRectangle(cornerRadius: 5).strokeBorder(StudioTheme.ink, lineWidth: 1) }
    }

    private func providerName(_ provider: String) -> String {
        switch provider {
        case "ollama": "Ollama · local"
        case "openai": "OpenAI"
        case "anthropic", "claude": "Anthropic"
        case "google", "gemini": "Google"
        default: provider.capitalized
        }
    }

    private var providerHelp: String {
        switch draft.provider {
        case "ollama":
            "Start Ollama and use a model you have already pulled. Leave the base URL blank to use its default address."
        case "openai":
            "Uses OPENAI_API_KEY from the launch environment. Source content is sent to the configured provider when you run a model-backed task."
        case "anthropic", "claude":
            "Uses ANTHROPIC_API_KEY from the launch environment. Source content is sent to the configured provider when you run a model-backed task."
        default:
            "Uses GOOGLE_API_KEY or GEMINI_API_KEY from the launch environment. Source content is sent to the configured provider when you run a model-backed task."
        }
    }
}

struct RunReviewSheet: View {
    @ObservedObject var model: StudioModel
    let action: StudioAction
    let close: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            SheetHeading(eyebrow: "REVIEW & RUN", title: action.title + ".", close: close)
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: action.destructive ? "exclamationmark.triangle" : "info.circle")
                        .font(.system(size: 19, weight: .medium))
                    Text(action.detail)
                        .font(StudioType.body)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(15)
                .background(StudioTheme.faint)
                .clipShape(RoundedRectangle(cornerRadius: 7))
                VStack(spacing: 0) {
                    reviewRow("WORKSPACE", model.workspace.path)
                    if action != .migrateWorkspace {
                        reviewRow("TARGET", "\(model.grade.replacingOccurrences(of: "grade", with: "Grade ")) · \(model.snapshot?.gradeLabel ?? "")")
                        if usesLanguageModel {
                            reviewRow("PROVIDER", model.settings.provider.uppercased())
                            reviewRow("MODEL", model.settings.model)
                        } else {
                            reviewRow("EXECUTION", action == .parseSources ? "Document parsing · no language model" : "Local processing · no language model")
                        }
                        if (try? model.arguments(for: action).contains("--force")) == true {
                            reviewRow("REBUILD", "Force enabled · saved checkpoints will not be reused")
                        }
                    }
                }
                .overlay { RoundedRectangle(cornerRadius: 7).strokeBorder(StudioTheme.hairline, lineWidth: 1) }
                DisclosureGroup("Command details") {
                    ScrollView {
                        Text(commandPreview)
                            .font(.system(size: 11.5, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.top, 10)
                    }
                    .frame(maxHeight: 130)
                }
                .font(.system(size: 12, weight: .medium))
                Text("You can stop a running task from the run journal. Files already written may remain.")
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(28)
            SheetFooter {
                Button("Back", action: close)
                    .buttonStyle(MonochromeButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button {
                    model.confirmRun()
                } label: {
                    Label(action.title, systemImage: "play.fill")
                }
                .buttonStyle(MonochromeButtonStyle(primary: true))
                .keyboardShortcut(.defaultAction)
                .disabled(model.isRunning || model.isLoading)
            }
        }
        .frame(width: 670)
    }

    private func reviewRow(_ label: String, _ value: String) -> some View {
        HStack(spacing: 12) {
            SectionLabel(label).frame(width: 102, alignment: .leading)
            Text(value).font(.system(size: 12.5, weight: .medium))
                .lineLimit(2)
                .truncationMode(.middle)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 14)
        .frame(minHeight: 41)
    }

    private var commandPreview: String {
        guard let arguments = try? model.arguments(for: action) else { return "Configuration unavailable." }
        return "wordbyword " + arguments.map { argument in
            argument.contains(where: \.isWhitespace) ? "'" + argument.replacingOccurrences(of: "'", with: "'\\''") + "'" : argument
        }.joined(separator: " ")
    }

    private var usesLanguageModel: Bool {
        switch action {
        case .prepareDictionary, .parseSources, .buildUsage, .updateLexicon, .migrateWorkspace,
             .linkExamples, .assignMeanings, .previewDuplicates, .applyDuplicates,
             .previewSourceDuplicates, .applySourceDuplicates:
            return false
        default:
            return true
        }
    }
}

struct ArtifactsSheet: View {
    @ObservedObject var model: StudioModel
    let close: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            SheetHeading(eyebrow: "BUILD ARTIFACTS", title: "Nothing behind a curtain.", close: close)
            HStack {
                Text("Inspect the saved files at each stage. Availability does not imply a verified checkpoint.")
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                Spacer()
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 16)
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(model.snapshot?.artifacts ?? []) { artifact in
                        Button { model.loadPreview(path: artifact.path) } label: {
                            HStack(spacing: 14) {
                                Text(String(artifact.name.prefix(2)))
                                    .font(StudioType.mono(13, weight: .bold))
                                    .frame(width: 30, height: 34)
                                    .background(artifact.exists ? StudioTheme.ink : StudioTheme.faint)
                                    .foregroundStyle(artifact.exists ? .white : StudioTheme.muted)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(artifact.label).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                                    Text(artifact.name)
                                        .font(.system(size: 11.5, design: .monospaced))
                                        .foregroundStyle(StudioTheme.muted)
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                                Spacer(minLength: 10)
                                StatusBadge(artifact.exists ? "AVAILABLE" : "NOT WRITTEN", filled: artifact.exists)
                                Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold))
                            }
                            .padding(.horizontal, 28)
                            .frame(height: 62)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .disabled(!artifact.exists || model.isLoading)
                        Rectangle().fill(StudioTheme.hairline).frame(height: 1).padding(.horizontal, 28)
                    }
                }
            }
            .frame(height: 430)
            SheetFooter {
                Button("Reveal dictionary folder") {
                    if let path = model.snapshot?.paths["dictionary_output_dir"] { model.reveal(path: path) }
                }
                .buttonStyle(MonochromeButtonStyle())
                Spacer()
                Button("Done", action: close)
                    .buttonStyle(MonochromeButtonStyle(primary: true))
                    .keyboardShortcut(.cancelAction)
            }
        }
        .frame(width: 780)
    }
}

struct PreviewSheet: View {
    @ObservedObject var model: StudioModel
    let preview: PreviewDocument
    let close: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            SheetHeading(eyebrow: preview.isMigration ? "WORKSPACE PLAN" : "FILE PREVIEW",
                         title: preview.isMigration ? "A place for every grade." : preview.name,
                         close: close)
            if !preview.isMigration {
                Text(preview.path)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(StudioTheme.muted)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 28)
                    .padding(.vertical, 12)
            }
            ScrollView {
                Text(preview.text.isEmpty ? "This file has no previewable text." : preview.text)
                    .font(.system(size: 12.5, design: .monospaced))
                    .lineSpacing(5)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .padding(28)
            }
            .frame(height: 410)
            .background(StudioTheme.faint)
            if preview.truncated {
                Text("This is a bounded preview. Open the original file to inspect the rest.")
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                    .padding(.horizontal, 28)
                    .padding(.vertical, 12)
            }
            SheetFooter {
                Button("Close", action: close)
                    .buttonStyle(MonochromeButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Spacer()
                if preview.isMigration {
                    Text("\(preview.plannedCount ?? 0) planned changes")
                        .font(StudioType.detail).foregroundStyle(StudioTheme.muted)
                    Button("Review & apply") { model.requestRun(.migrateWorkspace) }
                        .buttonStyle(MonochromeButtonStyle(primary: true))
                        .disabled(preview.canApply != true || (preview.plannedCount ?? 0) == 0 ||
                                  model.isRunning || model.isLoading)
                } else {
                    Button("Reveal") { model.reveal(path: preview.path) }
                        .buttonStyle(MonochromeButtonStyle())
                    Button("Open original") { NSWorkspace.shared.open(URL(fileURLWithPath: preview.path)) }
                        .buttonStyle(MonochromeButtonStyle(primary: true))
                }
            }
        }
        .frame(width: 820)
    }
}

struct JournalSheet: View {
    @ObservedObject var model: StudioModel
    let close: () -> Void
    @State private var followLatest = true

    var body: some View {
        VStack(spacing: 0) {
            SheetHeading(eyebrow: "RUN JOURNAL", title: journalTitle, close: close)
            HStack(spacing: 14) {
                if model.isRunning {
                    if let progress = model.progress {
                        ProgressView(value: progress).progressViewStyle(.linear).frame(width: 220)
                        Text("\(Int(progress * 100))% · current stage").font(StudioType.detail)
                    } else {
                        ProgressView().controlSize(.small)
                        Text(model.isCancelling ? "Waiting for cleanup and process exit…" : "Working on the current stage…")
                            .font(StudioType.detail)
                    }
                } else {
                    StatusBadge(model.lastResult?.succeeded == true ? "COMPLETE" :
                                    model.lastResult != nil || model.logs.last?.status == "failed"
                                    ? "REVIEW RESULT" : "NO ACTIVE RUN",
                                filled: model.lastResult?.succeeded == true)
                    if let result = model.lastResult {
                        Text("Exit \(result.exitCode)").font(StudioType.detail)
                    }
                }
                Spacer()
                if !model.isRunning, !savedOutputs.isEmpty {
                    Menu {
                        ForEach(savedOutputs, id: \.0) { key, path in
                            Button(key.replacingOccurrences(of: "_", with: " ").capitalized) {
                                model.reveal(path: path)
                            }
                        }
                    } label: {
                        Label("Saved outputs (\(savedOutputs.count))", systemImage: "folder")
                    }
                    .menuStyle(.borderlessButton)
                    .font(StudioType.detail)
                    .fixedSize()
                    .accessibilityLabel("Saved run outputs")
                    .help("Reveal files written by this run")
                }
                Toggle("Follow latest", isOn: $followLatest)
                    .toggleStyle(.checkbox)
                    .font(StudioType.detail)
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 14)
            Rectangle().fill(StudioTheme.ink).frame(height: 1)
            ScrollViewReader { proxy in
                ScrollView {
                    if model.logs.isEmpty {
                        EmptyState(icon: "text.alignleft", title: "A clear record of the work.",
                                   detail: "Start a workflow to see its real progress, saved artifacts, and any errors here.")
                            .padding(28)
                    } else {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            ForEach(model.logs) { entry in
                                logRow(entry).id(entry.id)
                            }
                        }
                    }
                }
                .onChange(of: model.logs.last?.id) { _, _ in
                    if followLatest, let last = model.logs.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
                .onChange(of: followLatest) { _, following in
                    if following, let last = model.logs.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
            .frame(height: 380)
            .background(StudioTheme.faint)
            SheetFooter {
                Button("Copy log") {
                    let log = model.logs.map { "[\($0.phase)] \($0.detail)" }.joined(separator: "\n")
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(log, forType: .string)
                }
                .buttonStyle(MonochromeButtonStyle())
                .disabled(model.logs.isEmpty)
                Text("\(model.logs.count) recent events")
                    .font(StudioType.detail).foregroundStyle(StudioTheme.muted)
                Spacer()
                if model.isRunning {
                    Button(model.isCancelling ? "Stopping…" : "Stop run") { model.cancelRun() }
                        .buttonStyle(MonochromeButtonStyle())
                        .disabled(model.isCancelling)
                }
                Button(model.isRunning ? "Keep working" : "Done", action: close)
                    .buttonStyle(MonochromeButtonStyle(primary: true))
                    .keyboardShortcut(.cancelAction)
            }
        }
        .frame(width: 860)
    }

    private var journalTitle: String {
        if model.isCancelling { return "Stopping with care." }
        if model.isRunning { return "The work, as it happens." }
        if let result = model.lastResult {
            if result.exitCode == 130 { return "The run was stopped." }
            return result.succeeded ? "The run is complete." : "Let's see what happened."
        }
        if model.logs.last?.status == "failed" { return "Let's see what happened." }
        return "The work, as it happens."
    }

    private var savedOutputs: [(String, String)] {
        (model.lastResult?.artifacts ?? [:]).sorted { $0.key < $1.key }.compactMap { key, value in
            guard !value.contains("://"),
                  value.contains("/") || ["csv", "json", "jsonl", "docx", "pdf"].contains(URL(fileURLWithPath: value).pathExtension) else {
                return nil
            }
            return (key, value)
        }
    }

    private func logRow(_ entry: LogEntry) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Text(entry.timestamp, format: .dateTime.hour().minute().second())
                .font(.system(size: 10.5, design: .monospaced))
                .foregroundStyle(StudioTheme.muted)
                .frame(width: 88, alignment: .leading)
            Text(entry.phase.replacingOccurrences(of: "_", with: " ").uppercased())
                .font(StudioType.condensed(10.5, weight: .bold))
                .frame(width: 98, alignment: .leading)
            Text(entry.detail)
                .font(.system(size: 12.5, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 12)
        .overlay(alignment: .bottom) {
            Rectangle().fill(StudioTheme.hairline).frame(height: 1).padding(.horizontal, 28)
        }
    }
}

private struct SheetHeading: View {
    let eyebrow: String
    let title: String
    let close: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel(eyebrow)
                Text(title).font(StudioType.mono(21, weight: .bold))
                    .lineLimit(2).truncationMode(.middle)
                    .accessibilityAddTraits(.isHeader)
            }
            Spacer()
            IconButton(title: "Close dialog", systemName: "xmark", action: close)
        }
        .padding(28)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) { Rectangle().fill(StudioTheme.ink).frame(height: 1.5) }
    }
}

private struct SheetFooter<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        HStack(spacing: 12, content: content)
            .padding(.horizontal, 28)
            .padding(.vertical, 20)
            .background(.white)
            .overlay(alignment: .top) { Rectangle().fill(StudioTheme.ink).frame(height: 1) }
    }
}

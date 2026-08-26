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
            SheetHeading(eyebrow: language.text("STUDIO SETTINGS"),
                         title: language.text("Your tools. Your defaults."), close: close)
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 12) {
                        SectionLabel(language.text("INTERFACE"))
                        settingRow(language.text("Language")) {
                            Picker(language.text("Interface language"), selection: $draft.language) {
                                ForEach(StudioLanguage.allCases, id: \.self) { language in
                                    Text(language.nativeName).tag(language)
                                }
                            }
                            .pickerStyle(.segmented)
                            .labelsHidden()
                            .accessibilityLabel(language.text("Interface language"))
                        }
                        Text(language.text("Documents, model output, and engine diagnostics keep their original language."))
                            .font(StudioType.detail)
                            .foregroundStyle(StudioTheme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Rectangle().fill(StudioTheme.hairline).frame(height: 1)
                    VStack(alignment: .leading, spacing: 12) {
                        SectionLabel(language.text("LANGUAGE MODEL"))
                        settingRow(language.text("Provider")) {
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
                        settingRow(language.text("Model")) {
                            studioField(language.text("Model name"), text: $draft.model)
                                .accessibilityLabel(language.text("Language model name"))
                        }
                        settingRow(language.text("Base URL")) {
                            studioField(language.text("Provider default"), text: $draft.baseURL)
                                .accessibilityLabel(language.text("Model provider base URL"))
                        }
                        Text(providerHelp)
                            .font(StudioType.detail)
                            .foregroundStyle(StudioTheme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Rectangle().fill(StudioTheme.hairline).frame(height: 1)
                    HStack(alignment: .top, spacing: 24) {
                        VStack(alignment: .leading, spacing: 14) {
                            SectionLabel(language.text("DICTIONARY"))
                            MonoToggle(language.text("Include numbers"), isOn: $draft.includeNumbers)
                            MonoToggle(language.text("Include punctuation"), isOn: $draft.includePunctuation)
                            MonoToggle(language.text("Force a fresh build"), isOn: $draft.force,
                                       detail: language.text("Ignore saved checkpoints and regenerate existing outputs."))
                        }
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                        Rectangle().fill(StudioTheme.hairline).frame(width: 1, height: 158)
                        VStack(alignment: .leading, spacing: 14) {
                            SectionLabel(language.text("CLASSROOM"))
                            MonoToggle(language.text("Parse PDFs with PaddleOCR-VL"), isOn: $draft.parsePDF,
                                       detail: language.text("Uses the project's separate OCR environment."))
                            VStack(spacing: 12) {
                                Stepper(language.format("Render · %d DPI", draft.renderDPI), value: $draft.renderDPI, in: 72...600, step: 36)
                                Stepper(language.format("Handout · %d pt", draft.bodyFontSize), value: $draft.bodyFontSize, in: 11...15)
                            }
                            .font(.system(size: 12.5, weight: .medium))
                        }
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                    if let validationError {
                        Label(language.text(validationError), systemImage: "exclamationmark.circle")
                            .font(StudioType.detail)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 22)
            }
            .frame(maxHeight: 460)
            SheetFooter {
                Text(language.text("Saved only in this workspace.\nAPI keys are never stored here."))
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                Spacer()
                Button(language.text("Cancel"), action: close)
                    .buttonStyle(MonochromeButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button(language.text("Save defaults")) {
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
        .environment(\.studioLanguage, language)
        .environment(\.locale, language.locale)
    }

    private var language: StudioLanguage { draft.language }

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
        case "ollama": language.text("Ollama · local")
        case "openai": "OpenAI"
        case "anthropic", "claude": "Anthropic"
        case "google", "gemini": "Google"
        default: provider.capitalized
        }
    }

    private var providerHelp: String {
        switch draft.provider {
        case "ollama":
            language.text("Start Ollama and use a model you have already pulled. Leave the base URL blank to use its default address.")
        case "openai":
            language.text("Uses OPENAI_API_KEY from the launch environment. Source content is sent to the configured provider when you run a model-backed task.")
        case "anthropic", "claude":
            language.text("Uses ANTHROPIC_API_KEY from the launch environment. Source content is sent to the configured provider when you run a model-backed task.")
        default:
            language.text("Uses GOOGLE_API_KEY or GEMINI_API_KEY from the launch environment. Source content is sent to the configured provider when you run a model-backed task.")
        }
    }
}

struct RunReviewSheet: View {
    @ObservedObject var model: StudioModel
    let action: StudioAction
    let close: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            SheetHeading(eyebrow: model.t("REVIEW & RUN"), title: model.t(action.title) + ".", close: close)
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: action.destructive ? "exclamationmark.triangle" : "info.circle")
                        .font(.system(size: 19, weight: .medium))
                    Text(model.t(action.detail))
                        .font(StudioType.body)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(15)
                .background(StudioTheme.faint)
                .clipShape(RoundedRectangle(cornerRadius: 7))
                VStack(spacing: 0) {
                    reviewRow(model.t("WORKSPACE"), model.workspace.path)
                    if action != .migrateWorkspace {
                        reviewRow(model.t("TARGET"), targetGradeLabel)
                        if usesLanguageModel {
                            reviewRow(model.t("PROVIDER"), model.settings.provider.uppercased())
                            reviewRow(model.t("MODEL"), model.settings.model)
                        } else {
                            reviewRow(model.t("EXECUTION"), model.t(action == .parseSources ? "Document parsing · no language model" : "Local processing · no language model"))
                        }
                        if (try? model.arguments(for: action).contains("--force")) == true {
                            reviewRow(model.t("REBUILD"), model.t("Force enabled · saved checkpoints will not be reused"))
                        }
                    }
                }
                .overlay { RoundedRectangle(cornerRadius: 7).strokeBorder(StudioTheme.hairline, lineWidth: 1) }
                DisclosureGroup(model.t("Command details")) {
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
                Text(model.t("You can stop a running task from the run journal. Files already written may remain."))
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(28)
            SheetFooter {
                Button(model.t("Back"), action: close)
                    .buttonStyle(MonochromeButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button {
                    model.confirmRun()
                } label: {
                    Label(model.t(action.title), systemImage: "play.fill")
                }
                .buttonStyle(MonochromeButtonStyle(primary: true))
                .keyboardShortcut(.defaultAction)
                .disabled(model.isRunning || model.isLoading)
            }
        }
        .frame(width: 670)
        .environment(\.studioLanguage, model.language)
        .environment(\.locale, model.language.locale)
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
        guard let arguments = try? model.arguments(for: action) else { return model.t("Configuration unavailable.") }
        return "wordbyword " + arguments.map { argument in
            argument.contains(where: \.isWhitespace) ? "'" + argument.replacingOccurrences(of: "'", with: "'\\''") + "'" : argument
        }.joined(separator: " ")
    }

    private var targetGradeLabel: String {
        let grade = model.f("Grade %d", Int(model.grade.replacingOccurrences(of: "grade", with: "")) ?? 1)
        guard let label = model.snapshot?.gradeLabel, !label.isEmpty, label != grade else { return grade }
        return grade + " · " + label
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
            SheetHeading(eyebrow: model.t("BUILD ARTIFACTS"), title: model.t("Nothing behind a curtain."), close: close)
            HStack {
                Text(model.t("Inspect the saved files at each stage. Availability does not imply a verified checkpoint."))
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
                                    .font(StudioType.mono(13, weight: .bold, language: model.language))
                                    .frame(width: 30, height: 34)
                                    .background(artifact.exists ? StudioTheme.ink : StudioTheme.faint)
                                    .foregroundStyle(artifact.exists ? .white : StudioTheme.muted)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(model.language.artifactLabel(artifact)).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                                    Text(artifact.name)
                                        .font(.system(size: 11.5, design: .monospaced))
                                        .foregroundStyle(StudioTheme.muted)
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                                Spacer(minLength: 10)
                                StatusBadge(model.t(artifact.exists ? "AVAILABLE" : "NOT WRITTEN"), filled: artifact.exists)
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
                Button(model.t("Reveal dictionary folder")) {
                    if let path = model.snapshot?.paths["dictionary_output_dir"] { model.reveal(path: path) }
                }
                .buttonStyle(MonochromeButtonStyle())
                Spacer()
                Button(model.t("Done"), action: close)
                    .buttonStyle(MonochromeButtonStyle(primary: true))
                    .keyboardShortcut(.cancelAction)
            }
        }
        .frame(width: 780)
        .environment(\.studioLanguage, model.language)
        .environment(\.locale, model.language.locale)
    }
}

struct PreviewSheet: View {
    @ObservedObject var model: StudioModel
    let preview: PreviewDocument
    let close: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            SheetHeading(eyebrow: model.t(preview.isMigration ? "WORKSPACE PLAN" : "FILE PREVIEW"),
                         title: preview.isMigration ? model.t("A place for every grade.") : preview.name,
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
                Text(preview.text.isEmpty ? model.t("This file has no previewable text.") : model.language.previewText(preview))
                    .font(.system(size: 12.5, design: .monospaced))
                    .lineSpacing(5)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .padding(28)
            }
            .frame(height: 410)
            .background(StudioTheme.faint)
            if preview.truncated {
                Text(model.t("This is a bounded preview. Open the original file to inspect the rest."))
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                    .padding(.horizontal, 28)
                    .padding(.vertical, 12)
            }
            SheetFooter {
                Button(model.t("Close"), action: close)
                    .buttonStyle(MonochromeButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Spacer()
                if preview.isMigration {
                    Text(model.f("%d planned changes", preview.plannedCount ?? 0))
                        .font(StudioType.detail).foregroundStyle(StudioTheme.muted)
                    Button(model.t("Review & apply")) { model.requestRun(.migrateWorkspace) }
                        .buttonStyle(MonochromeButtonStyle(primary: true))
                        .disabled(preview.canApply != true || (preview.plannedCount ?? 0) == 0 ||
                                  model.isRunning || model.isLoading)
                } else {
                    Button(model.t("Reveal")) { model.reveal(path: preview.path) }
                        .buttonStyle(MonochromeButtonStyle())
                    Button(model.t("Open original")) { NSWorkspace.shared.open(URL(fileURLWithPath: preview.path)) }
                        .buttonStyle(MonochromeButtonStyle(primary: true))
                }
            }
        }
        .frame(width: 820)
        .environment(\.studioLanguage, model.language)
        .environment(\.locale, model.language.locale)
    }
}

struct JournalSheet: View {
    @ObservedObject var model: StudioModel
    let close: () -> Void
    @State private var followLatest = true

    var body: some View {
        VStack(spacing: 0) {
            SheetHeading(eyebrow: model.t("RUN JOURNAL"), title: journalTitle, close: close)
            HStack(spacing: 14) {
                if model.isRunning {
                    if let progress = model.progress {
                        ProgressView(value: progress).progressViewStyle(.linear).frame(width: 220)
                        Text(model.f("%d%% · current stage", Int(progress * 100))).font(StudioType.detail)
                    } else {
                        ProgressView().controlSize(.small)
                        Text(model.t(model.isCancelling ? "Waiting for cleanup and process exit…" : "Working on the current stage…"))
                            .font(StudioType.detail)
                    }
                } else {
                    StatusBadge(model.t(model.lastResult?.succeeded == true ? "COMPLETE" :
                                    model.lastResult != nil || model.logs.last?.status == "failed"
                                    ? "REVIEW RESULT" : "NO ACTIVE RUN"),
                                filled: model.lastResult?.succeeded == true)
                    if let result = model.lastResult {
                        Text(model.f("Exit %d", result.exitCode)).font(StudioType.detail)
                    }
                }
                Spacer()
                if !model.isRunning, !savedOutputs.isEmpty {
                    Menu {
                        ForEach(savedOutputs, id: \.0) { key, path in
                            Button(model.t(key.replacingOccurrences(of: "_", with: " ").capitalized)) {
                                model.reveal(path: path)
                            }
                        }
                    } label: {
                        Label(model.f("Saved outputs (%d)", savedOutputs.count), systemImage: "folder")
                    }
                    .menuStyle(.borderlessButton)
                    .font(StudioType.detail)
                    .fixedSize()
                    .accessibilityLabel(model.t("Saved run outputs"))
                    .help(model.t("Reveal files written by this run"))
                }
                Toggle(model.t("Follow latest"), isOn: $followLatest)
                    .toggleStyle(.checkbox)
                    .font(StudioType.detail)
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 14)
            Text(model.t("Engine details are shown in their original language."))
                .font(StudioType.detail)
                .foregroundStyle(StudioTheme.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 28)
                .padding(.bottom, 12)
            Rectangle().fill(StudioTheme.ink).frame(height: 1)
            ScrollViewReader { proxy in
                ScrollView {
                    if model.logs.isEmpty {
                        EmptyState(icon: "text.alignleft", title: model.t("A clear record of the work."),
                                   detail: model.t("Start a workflow to see its real progress, saved artifacts, and any errors here."))
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
                Button(model.t("Copy log")) {
                    let log = model.logs.map { "[\($0.phase)] \($0.detail)" }.joined(separator: "\n")
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(log, forType: .string)
                }
                .buttonStyle(MonochromeButtonStyle())
                .disabled(model.logs.isEmpty)
                Text(model.f("%d recent events", model.logs.count))
                    .font(StudioType.detail).foregroundStyle(StudioTheme.muted)
                Spacer()
                if model.isRunning {
                    Button(model.t(model.isCancelling ? "Stopping…" : "Stop run")) { model.cancelRun() }
                        .buttonStyle(MonochromeButtonStyle())
                        .disabled(model.isCancelling)
                }
                Button(model.t(model.isRunning ? "Keep working" : "Done"), action: close)
                    .buttonStyle(MonochromeButtonStyle(primary: true))
                    .keyboardShortcut(.cancelAction)
            }
        }
        .frame(width: 860)
        .environment(\.studioLanguage, model.language)
        .environment(\.locale, model.language.locale)
    }

    private var journalTitle: String {
        if model.isCancelling { return model.t("Stopping with care.") }
        if model.isRunning { return model.t("The work, as it happens.") }
        if let result = model.lastResult {
            if result.exitCode == 130 { return model.t("The run was stopped.") }
            return model.t(result.succeeded ? "The run is complete." : "Let's see what happened.")
        }
        if model.logs.last?.status == "failed" { return model.t("Let's see what happened.") }
        return model.t("The work, as it happens.")
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
            Text(model.language.phase(entry.phase))
                .font(StudioType.condensed(10.5, weight: .bold, language: model.language))
                .frame(width: 98, alignment: .leading)
            Text(entry.workflow == "studio" ? model.t(entry.detail) : entry.detail)
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
    @Environment(\.studioLanguage) private var language
    let eyebrow: String
    let title: String
    let close: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel(eyebrow)
                Text(title).font(StudioType.mono(21, weight: .bold, language: language))
                    .lineLimit(2).truncationMode(.middle)
                    .accessibilityAddTraits(.isHeader)
            }
            Spacer()
            IconButton(title: language.text("Close dialog"), systemName: "xmark", action: close)
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

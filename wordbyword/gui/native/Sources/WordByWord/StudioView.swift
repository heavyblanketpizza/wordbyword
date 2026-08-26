import AppKit
import SwiftUI

struct StudioView: View {
    @ObservedObject var model: StudioModel
    @State private var sheet: StudioSheet?

    private enum StudioSheet {
        case settings, outputs, journal, preview, confirmation
    }

    var body: some View {
        VStack(spacing: 0) {
            titleBar
            navigation
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    pageHeading
                    if model.snapshot?.legacyLayout == true { legacyNotice }
                    if let notice = model.notice { noticeBanner(notice) }
                    WorkflowContent(model: model, showOutputs: { sheet = .outputs })
                }
                .padding(.horizontal, 36)
                .padding(.top, 22)
                .padding(.bottom, 14)
                .frame(maxWidth: .infinity, alignment: .topLeading)
            }
            .scrollIndicators(.automatic)
            .id(model.tab)
            footer
        }
        .background(StudioTheme.canvas)
        .foregroundStyle(StudioTheme.ink)
        .tint(StudioTheme.ink)
        .ignoresSafeArea(.container, edges: .top)
        .onReceive(model.$preview) { if $0 != nil { sheet = .preview } }
        .onReceive(model.$pendingAction) { if $0 != nil { sheet = .confirmation } }
        .onReceive(model.$showsLog) { if $0 { sheet = .journal } }
        .sheet(isPresented: Binding(
            get: { sheet != nil },
            set: { if !$0 { dismissSheet() } }
        )) { sheetContent }
        .alert("This needs your attention.", isPresented: Binding(
            get: { model.error != nil },
            set: { if !$0 { model.error = nil } }
        )) {
            Button("OK") { model.error = nil }
        } message: {
            Text(model.error ?? "")
        }
    }

    private var titleBar: some View {
        HStack(spacing: 9) {
            Text("WordByWord").font(.system(size: 13, weight: .semibold))
            Text("Korean vocabulary studio")
                .font(.system(size: 12))
                .foregroundStyle(StudioTheme.muted)
            Spacer()
            Button { model.chooseWorkspace() } label: {
                HStack(spacing: 6) {
                    Image(systemName: "folder").font(.system(size: 11))
                    Text(model.workspace.lastPathComponent)
                        .lineLimit(1)
                        .font(.system(size: 11, weight: .medium))
                    Image(systemName: "chevron.down").font(.system(size: 8, weight: .semibold))
                }
            }
            .buttonStyle(.plain)
            .help("Choose a different workspace")
            .accessibilityLabel("Choose workspace")
            .disabled(model.isRunning || model.isLoading)
        }
        .padding(.leading, 88)
        .padding(.trailing, 28)
        .frame(height: 32)
        .overlay(alignment: .bottom) {
            Rectangle().fill(StudioTheme.hairline).frame(height: 1)
        }
    }

    private var navigation: some View {
        HStack(alignment: .bottom, spacing: 7) {
            ForEach(StudioTab.allCases) { tab in
                Button { model.tab = tab } label: {
                    HStack(spacing: 8) {
                        Text(tab.number)
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .opacity(model.tab == tab ? 0.72 : 0.58)
                        Text(tab.title.uppercased())
                            .font(.system(size: 13.5, weight: .semibold))
                    }
                    .padding(.horizontal, 14)
                    .frame(height: 40)
                    .foregroundStyle(model.tab == tab ? .white : StudioTheme.ink)
                    .background(model.tab == tab ? StudioTheme.ink : .white, in: FolderTabShape())
                    .overlay { FolderTabShape().strokeBorder(StudioTheme.ink, lineWidth: 1.5) }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityAddTraits(model.tab == tab ? .isSelected : [])
            }
            Spacer(minLength: 10)
            Menu {
                ForEach(1...12, id: \.self) { number in
                    Button("Grade \(number)") { model.changeGrade("grade\(number)") }
                }
            } label: {
                Text("GRADE \(gradeNumber)")
                    .font(StudioType.condensed(11.5, weight: .bold))
            }
            .menuStyle(.borderlessButton)
            .padding(.horizontal, 10)
            .frame(height: 32)
            .background(.white)
            .overlay { RoundedRectangle(cornerRadius: 5).strokeBorder(StudioTheme.ink, lineWidth: 1) }
            .fixedSize()
            .padding(.bottom, 7)
            .disabled(model.isRunning || model.isLoading)
            .help("Choose the target school grade")
            .accessibilityLabel("Target grade \(gradeNumber)")
            IconButton(title: "Studio settings", systemName: "slider.horizontal.3") { sheet = .settings }
                .padding(.bottom, 7)
                .disabled(model.isRunning || model.isLoading)
        }
        .padding(.horizontal, 36)
        .frame(height: 64, alignment: .bottom)
        .overlay(alignment: .bottom) { Rectangle().fill(StudioTheme.ink).frame(height: 1.5) }
    }

    private var pageHeading: some View {
        HStack(alignment: .center, spacing: 24) {
            VStack(alignment: .leading, spacing: 8) {
                SectionLabel(model.tab.eyebrow)
                Text(model.tab.headline)
                    .font(StudioType.display)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                Text(model.tab.subtitle)
                    .font(StudioType.body)
                    .foregroundStyle(StudioTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 12)
            VStack(alignment: .trailing, spacing: 3) {
                Text(metricValue.formatted())
                    .font(StudioType.mono(30, weight: .bold))
                    .monospacedDigit()
                SectionLabel(metricLabel)
            }
            .accessibilityElement(children: .combine)
        }
    }

    private var legacyNotice: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: "square.stack.3d.up")
                .font(.system(size: 18, weight: .medium))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text("Your existing workspace, preserved.")
                    .font(.system(size: 13, weight: .semibold))
                Text("Browse your files here. Review the grade-folder plan before starting new work.")
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Button("Review layout") { model.showMigrationPlan() }
                .buttonStyle(MonochromeButtonStyle(compact: true))
                .disabled(model.isRunning || model.isLoading)
        }
        .padding(10)
        .background(StudioTheme.faint)
        .overlay {
            RoundedRectangle(cornerRadius: 7).strokeBorder(StudioTheme.hairline, lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: 7))
    }

    private func noticeBanner(_ text: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "info.circle")
            Text(text).font(StudioType.detail).textSelection(.enabled)
            Spacer()
            Button { model.notice = nil } label: {
                Image(systemName: "xmark").font(.system(size: 10, weight: .bold))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Dismiss notice")
        }
        .padding(12)
        .overlay { RoundedRectangle(cornerRadius: 6).strokeBorder(StudioTheme.ink, lineWidth: 1) }
    }

    private var footer: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 7) {
                    if model.isRunning || model.isLoading {
                        ProgressView().controlSize(.mini)
                    } else {
                        Rectangle().fill(StudioTheme.ink).frame(width: 7, height: 7)
                    }
                    Text(footerStatus)
                        .font(StudioType.condensed(11.5, weight: .bold))
                        .lineLimit(1)
                }
                Text(model.settings.model.isEmpty ? "Set a model in Studio settings" :
                        "\(model.settings.provider.uppercased())  /  \(model.settings.model)")
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                    .lineLimit(1)
            }
            .frame(maxWidth: 300, alignment: .leading)
            Spacer(minLength: 12)
            Button { model.showsLog = true } label: {
                Label("Run journal", systemImage: "text.alignleft")
            }
            .buttonStyle(MonochromeButtonStyle())
            if model.isRunning {
                Button(model.isCancelling ? "Stopping…" : "Stop run") { model.cancelRun() }
                    .buttonStyle(MonochromeButtonStyle(primary: true))
                    .disabled(model.isCancelling)
            } else if model.snapshot?.legacyLayout == true {
                Button {
                    model.showMigrationPlan()
                } label: {
                    Label("Review workspace", systemImage: "arrow.right")
                }
                .buttonStyle(MonochromeButtonStyle(primary: true))
                .disabled(model.isLoading)
            } else {
                Button { model.requestRun(primaryAction) } label: {
                    Label(primaryAction.title, systemImage: "play.fill")
                }
                .buttonStyle(MonochromeButtonStyle(primary: true))
                .disabled(!canRun)
                .help(canRun ? "Review the configuration before running" : emptyRunHint)
            }
        }
        .padding(.horizontal, 36)
        .frame(height: 78)
        .background(.white)
        .overlay(alignment: .top) { Rectangle().fill(StudioTheme.ink).frame(height: 1) }
    }

    @ViewBuilder
    private var sheetContent: some View {
        Group {
            switch sheet {
            case .settings:
                SettingsSheet(model: model, close: dismissSheet)
            case .outputs:
                ArtifactsSheet(model: model, close: dismissSheet)
            case .journal:
                JournalSheet(model: model, close: dismissSheet)
            case .preview:
                if let preview = model.preview {
                    PreviewSheet(model: model, preview: preview, close: dismissSheet)
                }
            case .confirmation:
                if let action = model.pendingAction {
                    RunReviewSheet(model: model, action: action, close: dismissSheet)
                }
            case nil:
                EmptyView()
            }
        }
        .background(.white)
        .foregroundStyle(StudioTheme.ink)
        .tint(StudioTheme.ink)
        .environment(\.colorScheme, .light)
    }

    private func dismissSheet() {
        if case .some(.journal) = sheet,
           model.lastResult?.succeeded == false || model.logs.last?.status == "failed" {
            // Closing the journal acknowledges its visible failure message.
            model.error = nil
        }
        sheet = nil
        model.preview = nil
        model.pendingAction = nil
        model.showsLog = false
    }

    private var gradeNumber: String { String(model.grade.dropFirst(5)) }
    private var metricValue: Int {
        model.snapshot?.counts[metricKey] ?? 0
    }
    private var metricKey: String {
        switch model.tab {
        case .dictionary: "words"
        case .classroom: "handouts"
        case .vocabulary: "archived"
        case .reports: "reports"
        }
    }
    private var metricLabel: String {
        switch model.tab {
        case .dictionary: metricValue == 1 ? "dictionary entry" : "dictionary entries"
        case .classroom: metricValue == 1 ? "editable handout" : "editable handouts"
        case .vocabulary: metricValue == 1 ? "archived printout" : "archived printouts"
        case .reports: metricValue == 1 ? "after-class report" : "after-class reports"
        }
    }
    private var primaryAction: StudioAction {
        switch model.tab {
        case .dictionary: .buildDictionary
        case .classroom: .buildClassroom
        case .vocabulary: .buildUsage
        case .reports: .generateReport
        }
    }
    private var canRun: Bool {
        guard !model.isLoading, !model.isRunning, let snapshot = model.snapshot else { return false }
        switch model.tab {
        case .dictionary: return (snapshot.counts["sources"] ?? 0) > 0
        case .classroom: return (snapshot.counts["class_sources"] ?? 0) > 0
        case .vocabulary: return (snapshot.counts["archived"] ?? 0) > 0
        case .reports: return !model.selectedReport.isEmpty
        }
    }
    private var emptyRunHint: String {
        switch model.tab {
        case .dictionary: "Add source documents to begin."
        case .classroom: "Add a source PDF to begin."
        case .vocabulary: "Add a used class printout to begin."
        case .reports: "Select an archived class printout."
        }
    }
    private var footerStatus: String {
        if model.isCancelling { return "STOP REQUESTED · WAITING FOR CLEANUP" }
        if model.isRunning { return model.phase.isEmpty ? "WORKING" : model.phase.uppercased() }
        if model.isLoading { return "READING WORKSPACE" }
        if model.error != nil { return "NEEDS ATTENTION" }
        if model.logs.last?.status == "failed" { return "LAST RUN NEEDS ATTENTION" }
        if let result = model.lastResult {
            return result.succeeded ? "LAST RUN COMPLETE" :
                result.exitCode == 130 ? "LAST RUN STOPPED" : "LAST RUN NEEDS ATTENTION"
        }
        if model.snapshot?.legacyLayout == true { return "BROWSING EXISTING FILES" }
        if !canRun { return emptyRunHint.uppercased() }
        return "READY WHEN YOU ARE"
    }
}

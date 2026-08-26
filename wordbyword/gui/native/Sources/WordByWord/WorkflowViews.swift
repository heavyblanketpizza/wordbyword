import AppKit
import SwiftUI

struct WorkflowContent: View {
    @ObservedObject var model: StudioModel
    let showOutputs: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            PathRegister(model: model, rows: pathRows)
            switch model.tab {
            case .dictionary:
                HStack(alignment: .top, spacing: 18) {
                    FilePanel(model: model, group: "sources", title: "Source library",
                              icon: "books.vertical", allowsImport: true)
                    CheckpointPanel(model: model, showOutputs: showOutputs)
                        .frame(width: 324)
                }
            case .classroom:
                HStack(alignment: .top, spacing: 18) {
                    FilePanel(model: model, group: "class_sources", title: "Source readings",
                              icon: "doc.text", allowsImport: true)
                    FilePanel(model: model, group: "handouts", title: "Ready for class",
                              icon: "printer")
                }
            case .vocabulary:
                HStack(alignment: .top, spacing: 18) {
                    FilePanel(model: model, group: "archive", title: "Class archive",
                              icon: "archivebox", allowsImport: true)
                    VocabularyPanel(model: model).frame(width: 324)
                }
            case .reports:
                HStack(alignment: .top, spacing: 18) {
                    FilePanel(model: model, group: "archive", title: "Choose a class printout",
                              icon: "checklist", selectsReport: true)
                    FilePanel(model: model, group: "reports", title: "Reading reports",
                              icon: "doc.text.magnifyingglass")
                }
            }
            HStack(alignment: .center, spacing: 12) {
                Text(workflowHint)
                    .font(StudioType.detail)
                    .foregroundStyle(StudioTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 20)
                Menu {
                    actions
                } label: {
                    Label(model.t("More actions"), systemImage: "ellipsis")
                        .font(.system(size: 12, weight: .semibold))
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .disabled(model.isRunning || model.isLoading || model.snapshot == nil ||
                          model.snapshot?.legacyLayout == true)
                .accessibilityLabel(model.f("More %@ actions", model.t(model.tab.title)))
            }
            if let warnings = model.snapshot?.warnings, !warnings.isEmpty {
                DisclosureGroup {
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(warnings, id: \.self) { warning in
                            Text(model.t(warning))
                                .font(StudioType.detail)
                                .foregroundStyle(StudioTheme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(.top, 5)
                } label: {
                    Text(model.f("Workspace notes (%d)", warnings.count))
                        .font(StudioType.detail)
                        .foregroundStyle(StudioTheme.muted)
                }
            }
        }
    }

    private var pathRows: [(String, String)] {
        switch model.tab {
        case .dictionary:
            [("SOURCE", "dictionary_input_dir"), ("DICTIONARY", "dictionary_output_dir"),
             ("REFERENCE", "reference_dictionary_path")]
        case .classroom:
            [("SOURCE", "class_source_dir"), ("REWRITTEN", "rewrite_output_root"),
             ("HANDOUTS", "print_output_root")]
        case .vocabulary:
            [("CLASS ARCHIVE", "archive_root"), ("LIVE LEXICON", "live_lexicon_path")]
        case .reports:
            [("CLASS ARCHIVE", "archive_root"), ("REPORTS", "after_class_report_output_root")]
        }
    }

    private var workflowHint: String {
        switch model.tab {
        case .dictionary:
            model.t("New sources? Preview their managed filenames in More actions. Full builds resume verified checkpoints.")
        case .classroom:
            model.t("PDF → parsed text → grade-friendly Korean → editable DOCX. Existing successful handouts are reused.")
        case .vocabulary:
            model.t("Name the archive, collect usage signals, then update your live vocabulary. Original printouts stay intact.")
        case .reports:
            model.selectedReport.isEmpty
                ? model.t("Select one managed, dated printout on the left. Reports are written in Korean for an adult reader.")
                : model.f("Selected: %@", URL(fileURLWithPath: model.selectedReport).lastPathComponent)
        }
    }

    @ViewBuilder
    private var actions: some View {
        switch model.tab {
        case .dictionary:
            actionButton(.previewSourceNames)
            actionButton(.applySourceNames)
            Menu(model.t("Duplicate sources")) {
                actionButton(.previewSourceDuplicates)
                actionButton(.applySourceDuplicates)
            }
            Divider()
            actionButton(.prepareDictionary)
            Menu(model.t("Repair a saved build")) {
                actionButton(.cleanExamples)
                actionButton(.cleanCandidates)
                actionButton(.linkExamples)
                actionButton(.generateExamples)
                actionButton(.retryExamples)
                actionButton(.assignMeanings)
                actionButton(.generateMeanings)
            }
        case .classroom:
            actionButton(.parseSources)
            actionButton(.rewriteSources)
            actionButton(.rewriteWholeSources)
            actionButton(.printHandouts)
        case .vocabulary:
            actionButton(.previewArchiveNames)
            actionButton(.applyArchiveNames)
            Divider()
            actionButton(.previewDuplicates)
            actionButton(.applyDuplicates)
            Divider()
            actionButton(.buildUsage)
            actionButton(.updateLexicon)
        case .reports:
            actionButton(.previewArchiveNames)
            actionButton(.applyArchiveNames)
        }
    }

    private func actionButton(_ action: StudioAction) -> some View {
        Button(model.t(action.title)) { model.requestRun(action) }
    }
}

private struct PathRegister: View {
    @ObservedObject var model: StudioModel
    let rows: [(String, String)]
    @State private var expanded = false

    private var visibleRows: [(String, String)] {
        expanded ? rows : Array(rows.prefix(1))
    }

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(visibleRows.enumerated()), id: \.offset) { index, row in
                HStack(spacing: 12) {
                    Image(systemName: row.1.contains("path") ? "doc" : "folder")
                        .font(.system(size: 16, weight: .medium))
                        .frame(width: 24)
                        .accessibilityHidden(true)
                    SectionLabel(model.t(row.0)).frame(width: 108, alignment: .leading)
                    Text(displayPath(row.1))
                        .font(StudioType.condensed(12.5, language: model.language))
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .help(model.snapshot?.paths[row.1] ?? "")
                    Button {
                        if let path = model.snapshot?.paths[row.1] { model.reveal(path: path) }
                    } label: {
                        Image(systemName: "arrow.up.right")
                            .font(.system(size: 12, weight: .semibold))
                            .frame(width: 28, height: 28)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help(model.f("Reveal %@ in Finder", model.t(row.0).lowercased()))
                    .accessibilityLabel(model.f("Reveal %@ in Finder", model.t(row.0).lowercased()))
                    .disabled(model.snapshot == nil)
                    if index == 0, rows.count > 1 {
                        Rectangle().fill(StudioTheme.hairline).frame(width: 1, height: 20)
                        Button { expanded.toggle() } label: {
                            HStack(spacing: 7) {
                                Text(model.t("Output locations"))
                                    .font(.system(size: 11, weight: .medium))
                                Image(systemName: expanded ? "chevron.up" : "chevron.down")
                                    .font(.system(size: 9, weight: .semibold))
                            }
                            .frame(height: 28)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(model.t(expanded ? "Hide output locations" : "Show output locations"))
                    }
                }
                .padding(.horizontal, 14)
                .frame(height: 44)
                if index < visibleRows.count - 1 {
                    Rectangle().fill(StudioTheme.hairline).frame(height: 1)
                }
            }
        }
        .overlay { RoundedRectangle(cornerRadius: 7).strokeBorder(StudioTheme.hairline, lineWidth: 1) }
        .onChange(of: model.tab) { _, _ in expanded = false }
    }

    private func displayPath(_ key: String) -> String {
        guard let path = model.snapshot?.paths[key] else {
            return model.t(model.isLoading ? "Reading workspace…" : "Not available")
        }
        let prefix = (model.snapshot?.workspace ?? model.workspace.path) + "/"
        return path.hasPrefix(prefix) ? String(path.dropFirst(prefix.count)) : path
    }
}

struct FilePanel: View {
    @ObservedObject var model: StudioModel
    let group: String
    let title: String
    let icon: String
    var allowsImport = false
    var selectsReport = false

    private var files: [StudioFile] { model.snapshot?.files[group] ?? [] }
    private var duplicateNames: Set<String> {
        Set(Dictionary(grouping: files, by: \.name).filter { $0.value.count > 1 }.keys)
    }

    var body: some View {
        let repeatedNames = duplicateNames
        StudioPanel(title: model.t(title), icon: icon,
                    badge: model.f(files.count == 1 ? "%d FILE" : "%d FILES", files.count)) {
            VStack(spacing: 0) {
                HStack(spacing: 8) {
                    SectionLabel(formats)
                    Spacer()
                    if allowsImport {
                        Button { model.importFiles(group: group) } label: {
                            Label(model.t("Add files"), systemImage: "plus")
                        }
                        .buttonStyle(MonochromeButtonStyle(compact: true))
                        .disabled(model.isRunning || model.isLoading || model.snapshot?.legacyLayout == true)
                        .help(model.t(model.snapshot?.legacyLayout == true
                              ? "Review the workspace layout before importing new files." : "Copy files into this workspace"))
                    } else {
                        IconButton(title: model.f("Refresh %@", model.t(title).lowercased()), systemName: "arrow.clockwise") {
                            model.refresh()
                        }
                        .disabled(model.isLoading || model.isRunning)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                Rectangle().fill(StudioTheme.hairline).frame(height: 1)
                if files.isEmpty {
                    EmptyState(icon: emptyIcon, title: emptyTitle, detail: emptyDetail)
                        .padding(20)
                        .frame(minHeight: 148, alignment: .topLeading)
                } else {
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(files) { file in
                                FileRecordRow(file: file,
                                              context: repeatedNames.contains(file.name) ||
                                                ["material.docx", "after_class_report.docx"].contains(file.name)
                                                ? URL(fileURLWithPath: file.path).deletingLastPathComponent().lastPathComponent : nil,
                                              selected: selectsReport && model.selectedReport == file.path) {
                                    if selectsReport { model.selectedReport = file.path }
                                    else { model.loadPreview(path: file.path) }
                                }
                                .contextMenu {
                                    Button(model.t("Preview")) { model.loadPreview(path: file.path) }
                                    Button(model.t("Reveal in Finder")) { model.reveal(path: file.path) }
                                }
                                if file.id != files.last?.id {
                                    Rectangle().fill(StudioTheme.hairline).frame(height: 1)
                                        .padding(.horizontal, 14)
                                }
                            }
                        }
                    }
                    .frame(height: min(max(CGFloat(files.count) * 56 - 1, 148), 210))
                    .scrollIndicators(.visible)
                }
                if group == "sources", let summary = model.snapshot?.summary,
                   (summary["extracted_pages"] ?? 0) > 0 {
                    HStack(spacing: 5) {
                        Text(model.f("%@ pages · %@ sentences in the saved build",
                                     (summary["extracted_pages"] ?? 0).formatted(.number.locale(model.language.locale)),
                                     (summary["sentences"] ?? 0).formatted(.number.locale(model.language.locale))))
                        Spacer(minLength: 0)
                    }
                    .font(.system(size: 11))
                    .foregroundStyle(StudioTheme.muted)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(StudioTheme.faint)
                }
            }
        }
    }

    private var formats: String {
        switch group {
        case "sources": "PDF / TXT / MD"
        case "class_sources": model.t("SOURCE PDFS")
        case "archive": selectsReport ? model.t("SELECT ONE PRINTOUT") : "DOCX / PDF"
        default: model.t("EDITABLE DOCX")
        }
    }
    private var emptyIcon: String { allowsImport ? "doc.badge.plus" : "tray" }
    private var emptyTitle: String {
        switch group {
        case "sources": model.t("A dictionary starts with a source.")
        case "class_sources": model.t("Bring a reading to class.")
        case "handouts": model.t("Your next handout goes here.")
        case "archive": model.t("Keep what you teach.")
        default: model.t("A little reflection goes a long way.")
        }
    }
    private var emptyDetail: String {
        switch group {
        case "sources": model.t("Add a book, document, or text file. Your originals are copied, never moved.")
        case "class_sources": model.t("Add a PDF to parse, rewrite, and turn into a Korean classroom handout.")
        case "handouts": model.t("Completed printouts will appear here, ready to review and open in Word.")
        case "archive": model.t("Add a used class printout in Vocabulary to build a record of words taught.")
        default: model.t("Generate a report from one archived class printout. Finished reports appear here.")
        }
    }
}

struct FileRecordRow: View {
    @Environment(\.studioLanguage) private var language
    let file: StudioFile
    var context: String? = nil
    var selected = false
    let action: () -> Void

    private var displayName: String {
        context.map { "\($0) / \(file.name)" } ?? file.name
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                ZStack {
                    RoundedRectangle(cornerRadius: 5)
                        .fill(selected ? StudioTheme.ink : StudioTheme.canvas)
                    RoundedRectangle(cornerRadius: 5).strokeBorder(StudioTheme.ink, lineWidth: 1)
                    Image(systemName: selected ? "checkmark" : "doc.text")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(selected ? .white : StudioTheme.ink)
                }
                .frame(width: 29, height: 33)
                VStack(alignment: .leading, spacing: 4) {
                    Text(displayName)
                        .font(.system(size: 13, weight: .medium))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Text(language.format("%@  ·  %@", file.kind.uppercased(),
                                         Int64(file.size).formatted(.byteCount(style: .file).locale(language.locale))))
                        .font(.system(size: 11))
                        .foregroundStyle(StudioTheme.muted)
                }
                Spacer(minLength: 8)
                Image(systemName: selected ? "checkmark" : "chevron.right")
                    .font(.system(size: 10, weight: .semibold))
            }
            .padding(.horizontal, 14)
            .frame(height: 55)
            .contentShape(Rectangle())
            .background(selected ? StudioTheme.faint : StudioTheme.canvas)
        }
        .buttonStyle(.plain)
        .foregroundStyle(StudioTheme.ink)
        .accessibilityLabel(displayName)
        .accessibilityValue(selected ? language.text("Selected") : file.kind)
        .help(file.path)
    }
}

private struct CheckpointPanel: View {
    @ObservedObject var model: StudioModel
    let showOutputs: () -> Void
    private let groups: [(String, String, ClosedRange<Int>)] = [
        ("01", "Read & tokenize", 0...4),
        ("02", "Select candidates", 5...9),
        ("03", "Clean & link", 10...12),
        ("04", "Examples & meanings", 13...15)
    ]

    var body: some View {
        StudioPanel(title: model.t("Saved checkpoints"), icon: "square.stack.3d.up") {
            VStack(spacing: 0) {
                ForEach(groups, id: \.0) { group in
                    HStack(spacing: 10) {
                        Text(group.0)
                            .font(StudioType.condensed(11, weight: .bold, language: model.language))
                            .frame(width: 24, height: 24)
                            .overlay { Rectangle().strokeBorder(StudioTheme.ink, lineWidth: 1) }
                        VStack(alignment: .leading, spacing: 4) {
                            Text(model.t(group.1)).font(.system(size: 12.5, weight: .semibold))
                            Text(model.f("%02d — %02d", group.2.lowerBound, group.2.upperBound))
                                .font(StudioType.condensed(10.5, language: model.language))
                                .foregroundStyle(StudioTheme.muted)
                        }
                        Spacer(minLength: 8)
                        Text(model.f("%d/%d", savedCount(group.2), group.2.count))
                            .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                    }
                    .padding(.horizontal, 14)
                    .frame(height: 48)
                    if group.0 != "04" {
                        Rectangle().fill(StudioTheme.hairline).frame(height: 1)
                            .padding(.horizontal, 14)
                    }
                }
                Button { showOutputs() } label: {
                    HStack {
                        Text(model.t("Inspect build artifacts")).font(.system(size: 12, weight: .semibold))
                        Spacer()
                        Image(systemName: "arrow.right").font(.system(size: 12, weight: .medium))
                    }
                    .padding(.horizontal, 14)
                    .frame(height: 42)
                    .background(StudioTheme.faint)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(model.snapshot == nil || model.isLoading)
            }
        }
    }

    private func savedCount(_ range: ClosedRange<Int>) -> Int {
        let existing = model.snapshot?.artifacts.filter(\.exists) ?? []
        return range.filter { number in
            existing.contains { $0.name.hasPrefix(String(format: "%02d_", number)) }
        }.count
    }
}

private struct VocabularyPanel: View {
    @ObservedObject var model: StudioModel

    var body: some View {
        StudioPanel(title: model.t("Close the learning loop"), icon: "arrow.triangle.2.circlepath") {
            VStack(alignment: .leading, spacing: 0) {
                actionRow("01", title: "Name the archive",
                          detail: "Review names before applying them.", action: .previewArchiveNames)
                Divider().padding(.horizontal, 14)
                actionRow("02", title: "Collect usage signals",
                          detail: "Learn which words appeared in class.", action: .buildUsage)
                Divider().padding(.horizontal, 14)
                actionRow("03", title: "Update the live lexicon",
                          detail: "Bring classroom evidence forward.", action: .updateLexicon)
            }
        }
    }

    private func actionRow(_ number: String, title: String, detail: String, action: StudioAction) -> some View {
        Button { model.requestRun(action) } label: {
            HStack(alignment: .top, spacing: 10) {
                Text(number).font(StudioType.condensed(11, weight: .bold, language: model.language)).padding(.top, 2)
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.t(title)).font(.system(size: 12.5, weight: .semibold))
                    Text(model.t(detail)).font(.system(size: 11.5))
                        .foregroundStyle(StudioTheme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 4)
                Image(systemName: "arrow.right").font(.system(size: 11, weight: .medium))
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(model.isRunning || model.isLoading || model.snapshot?.legacyLayout == true)
    }
}

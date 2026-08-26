import Foundation

enum StudioTab: String, CaseIterable, Identifiable, Sendable {
    case dictionary, classroom, vocabulary, reports

    var id: String { rawValue }
    var number: String {
        switch self {
        case .dictionary: return "01"
        case .classroom: return "02"
        case .vocabulary: return "03"
        case .reports: return "04"
        }
    }
    var title: String {
        switch self {
        case .dictionary: return "Dictionary"
        case .classroom: return "Classroom"
        case .vocabulary: return "Vocabulary"
        case .reports: return "Reports"
        }
    }
    var eyebrow: String {
        switch self {
        case .dictionary: return "THE DICTIONARY WORKBENCH"
        case .classroom: return "MADE FOR THE CLASSROOM"
        case .vocabulary: return "WORDS IN USE"
        case .reports: return "AFTER THE LESSON"
        }
    }
    var headline: String {
        switch self {
        case .dictionary: return "Every word starts here."
        case .classroom: return "From source to classroom."
        case .vocabulary: return "Let the vocabulary grow."
        case .reports: return "Make the reading visible."
        }
    }
    var subtitle: String {
        switch self {
        case .dictionary: return "Build a Korean dictionary from source documents, one grade at a time."
        case .classroom: return "Parse, rewrite, and print reading material for your selected grade."
        case .vocabulary: return "Connect archived lessons to the words your students actually encounter."
        case .reports: return "Create a reading report from one archived class handout."
        }
    }
    var icon: String {
        switch self {
        case .dictionary: return "text.book.closed"
        case .classroom: return "doc.richtext"
        case .vocabulary: return "character.book.closed"
        case .reports: return "doc.text.magnifyingglass"
        }
    }
}

struct WorkspaceSnapshot: Decodable, Sendable {
    let workspace: String
    let grade: String
    let gradeLabel: String
    let legacyLayout: Bool
    let warnings: [String]
    let paths: [String: String]
    let runPaths: [String: String]
    let counts: [String: Int]
    let files: [String: [StudioFile]]
    let artifacts: [StudioArtifact]
    let summary: [String: Int]
}

struct StudioFile: Decodable, Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let path: String
    let kind: String
    let size: Int
    let modified: String
}

struct StudioArtifact: Decodable, Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let label: String
    let path: String
    let exists: Bool
    let size: Int
}

struct PreviewDocument: Decodable, Identifiable, Sendable {
    let name: String
    let path: String
    let text: String
    let truncated: Bool
    var canApply: Bool? = nil
    var plannedCount: Int? = nil
    var byteCount: Int? = nil

    var id: String { path + "#" + name }
    var isMigration: Bool { canApply != nil || plannedCount != nil }
}

struct RunResult: Decodable, Sendable {
    let exitCode: Int
    let status: String
    let detail: String
    let artifacts: [String: String]
    let error: String

    var succeeded: Bool { exitCode == 0 && status != "failed" && status != "cancelled" && error.isEmpty }
}

struct CommandEvent: Decodable, Sendable {
    let workflow: String
    let phase: String
    let detail: String
    let status: String
    let current: Int
    let total: Int
    let artifacts: [String: String]
    let error: String
}

struct LogEntry: Identifiable, Sendable {
    let id = UUID()
    let timestamp = Date()
    let workflow: String
    let phase: String
    let detail: String
    let status: String
    let current: Int
    let total: Int
    let artifacts: [String: String]
    let error: String

    init(workflow: String = "studio", phase: String, detail: String, status: String = "running",
         current: Int = 0, total: Int = 0, artifacts: [String: String] = [:], error: String = "") {
        self.workflow = workflow
        self.phase = phase
        self.detail = String(detail.prefix(8_192))
        self.status = status
        self.current = current
        self.total = total
        self.artifacts = artifacts
        self.error = String(error.prefix(8_192))
    }

    init(event: CommandEvent) {
        self.init(workflow: event.workflow, phase: event.phase, detail: event.detail,
                  status: event.status, current: event.current, total: event.total,
                  artifacts: event.artifacts, error: event.error)
    }
}

struct StudioSettings: Codable, Equatable, Sendable {
    var language: StudioLanguage = .english
    var provider = "ollama"
    var model = "gemma4:31b"
    var baseURL = ""
    var includeNumbers = false
    var includePunctuation = false
    var force = false
    var parsePDF = true
    var renderDPI = 144
    var bodyFontSize = 13

    init() {}

    private enum CodingKeys: String, CodingKey {
        case language, provider, model, baseURL, includeNumbers, includePunctuation, force
        case parsePDF, renderDPI, bodyFontSize
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        // Older workspaces have no language key. An unknown future language also
        // falls back to English without discarding the user's model settings.
        language = StudioLanguage(rawValue: try values.decodeIfPresent(String.self, forKey: .language) ?? "en") ?? .english
        provider = try values.decode(String.self, forKey: .provider)
        model = try values.decode(String.self, forKey: .model)
        baseURL = try values.decode(String.self, forKey: .baseURL)
        includeNumbers = try values.decode(Bool.self, forKey: .includeNumbers)
        includePunctuation = try values.decode(Bool.self, forKey: .includePunctuation)
        force = try values.decode(Bool.self, forKey: .force)
        parsePDF = try values.decode(Bool.self, forKey: .parsePDF)
        renderDPI = try values.decode(Int.self, forKey: .renderDPI)
        bodyFontSize = try values.decode(Int.self, forKey: .bodyFontSize)
    }

    func validated() throws -> StudioSettings {
        var value = self
        value.provider = provider.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        value.model = model.trimmingCharacters(in: .whitespacesAndNewlines)
        value.baseURL = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ["ollama", "openai", "anthropic", "claude", "google", "gemini"].contains(value.provider) else {
            throw StudioFailure("Choose a supported model provider.")
        }
        guard !value.model.isEmpty, value.model.count <= 200,
              value.model.rangeOfCharacter(from: .controlCharacters) == nil else {
            throw StudioFailure("Enter a model name without control characters (up to 200 characters).")
        }
        if !value.baseURL.isEmpty {
            guard let url = URLComponents(string: value.baseURL),
                  ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
                  let host = url.host, !host.isEmpty,
                  url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else {
                throw StudioFailure("Use an HTTP or HTTPS base URL without credentials, query parameters, or a fragment. API keys belong in environment variables.")
            }
        }
        guard (72...600).contains(value.renderDPI) else {
            throw StudioFailure("PDF render resolution must be between 72 and 600 DPI.")
        }
        guard (11...15).contains(value.bodyFontSize) else {
            throw StudioFailure("Handout body type must be between 11 and 15 points.")
        }
        return value
    }

    static func read(from workspace: URL) throws -> StudioSettings {
        let path = workspace.appendingPathComponent(".wordbyword/studio-settings.json")
        guard FileManager.default.fileExists(atPath: path.path) else { return StudioSettings() }
        let attributes = try FileManager.default.attributesOfItem(atPath: path.path)
        guard (attributes[.size] as? NSNumber)?.intValue ?? 0 <= 65_536 else {
            throw StudioFailure("The saved studio settings file is too large to read.")
        }
        return try JSONDecoder().decode(StudioSettings.self, from: Data(contentsOf: path)).validated()
    }

    func write(to workspace: URL) throws {
        let value = try validated()
        let root = workspace.standardizedFileURL.resolvingSymlinksInPath()
        let directory = root.appendingPathComponent(".wordbyword", isDirectory: true)
        let destination = directory.appendingPathComponent("studio-settings.json")
        let manager = FileManager.default
        for path in [directory, destination] {
            if let attributes = try? manager.attributesOfItem(atPath: path.path),
               attributes[.type] as? FileAttributeType == .typeSymbolicLink {
                throw StudioFailure("Studio settings cannot be saved through a symbolic link.")
            }
        }
        var isDirectory: ObjCBool = false
        guard manager.fileExists(atPath: root.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw StudioFailure("The workspace folder no longer exists.")
        }
        var settingsDirectory: ObjCBool = false
        if manager.fileExists(atPath: directory.path, isDirectory: &settingsDirectory) {
            guard settingsDirectory.boolValue else {
                throw StudioFailure("The .wordbyword settings path is not a folder.")
            }
        } else {
            try manager.createDirectory(at: directory, withIntermediateDirectories: false)
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(value).write(to: destination, options: .atomic)
    }
}

struct StudioFailure: LocalizedError, Sendable {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

enum StudioAction: String, CaseIterable, Identifiable, Sendable {
    case buildDictionary, prepareDictionary, previewSourceNames, applySourceNames
    case buildClassroom, parseSources, rewriteSources, printHandouts
    case previewArchiveNames, applyArchiveNames, previewDuplicates, applyDuplicates
    case buildUsage, updateLexicon, generateReport, migrateWorkspace
    case cleanExamples, cleanCandidates, generateExamples, retryExamples, linkExamples
    case assignMeanings, generateMeanings, rewriteWholeSources
    case previewSourceDuplicates, applySourceDuplicates

    var id: String { rawValue }
    var title: String {
        switch self {
        case .buildDictionary: return "Build dictionary"
        case .prepareDictionary: return "Prepare dictionary"
        case .previewSourceNames: return "Preview source names"
        case .applySourceNames: return "Apply source names"
        case .buildClassroom: return "Build class printouts"
        case .parseSources: return "Parse source materials"
        case .rewriteSources: return "Rewrite source materials"
        case .printHandouts: return "Print handouts"
        case .previewArchiveNames: return "Preview archive names"
        case .applyArchiveNames: return "Apply archive names"
        case .previewDuplicates: return "Review duplicate files"
        case .applyDuplicates: return "Quarantine duplicates"
        case .buildUsage: return "Build usage signals"
        case .updateLexicon: return "Update live lexicon"
        case .generateReport: return "Generate reading report"
        case .migrateWorkspace: return "Organize grade workspaces"
        case .cleanExamples: return "Clean example sentences"
        case .cleanCandidates: return "Clean dictionary candidates"
        case .generateExamples: return "Generate missing examples"
        case .retryExamples: return "Retry failed examples"
        case .linkExamples: return "Link cleaned examples"
        case .assignMeanings: return "Assign reference meanings"
        case .generateMeanings: return "Generate missing meanings"
        case .rewriteWholeSources: return "Rewrite whole documents"
        case .previewSourceDuplicates: return "Review source duplicates"
        case .applySourceDuplicates: return "Quarantine source duplicates"
        }
    }
    var detail: String {
        switch self {
        case .buildDictionary:
            return "Run dictionary stages 00–15 for the selected grade. Later stages use your configured model and may incur provider charges. Existing checkpoints are reused unless force rebuild is on."
        case .prepareDictionary:
            return "Run stages 00–09 without a language model. This removes existing stages 10–15 for the selected grade, including the final dictionary. Rebuild the full dictionary to restore those outputs."
        case .previewSourceNames:
            return "Classify source files with your configured model and review managed filenames. No files are renamed. Provider charges may apply."
        case .applySourceNames:
            return "Classify and rename source files into the project's managed naming format. Review the name preview first. Provider charges may apply."
        case .buildClassroom:
            return "Parse source material, rewrite it with grade vocabulary, and create editable DOCX handouts. PDF parsing may require the OCR environment. Model calls may incur provider charges."
        case .parseSources:
            return "Create structured source bundles for the selected grade. PDF parsing uses the configured OCR environment when enabled. Existing parse bundles may be replaced."
        case .rewriteSources:
            return "Rewrite all parsed bundles with vocabulary guidance and your configured model. Existing rewrites may be replaced. Provider charges may apply."
        case .printHandouts:
            return "Create editable DOCX handouts from rewritten material. Existing handouts may be replaced. Model calls may incur provider charges."
        case .previewArchiveNames:
            return "Classify archived DOCX and PDF handouts and review their managed filenames. No files are renamed. Provider charges may apply."
        case .applyArchiveNames:
            return "Classify and rename archived handouts into the project's managed naming format. Review the name preview first. Provider charges may apply."
        case .previewDuplicates:
            return "Compare archived handouts for exact duplicates and show the proposed quarantine plan. No files are moved."
        case .applyDuplicates:
            return "Move recommended exact duplicates into the archive's hidden quarantine folder. Quarantined files are retained, not deleted. Review the duplicate plan first."
        case .buildUsage:
            return "Rebuild usage-signal artifacts from managed archived handouts for the selected grade. Existing usage artifacts may be replaced."
        case .updateLexicon:
            return "Merge the final dictionary and usage signals into this grade's canonical and supplemental live lexicons. Existing lexicon outputs will be updated."
        case .generateReport:
            return "Create an adult-facing Korean reading report from the selected archived handout. The configured model is used; provider charges may apply."
        case .migrateWorkspace:
            return "Apply the reviewed migration plan across this workspace. Existing legacy files will move into grade-specific folders; no source content is edited. Unresolved conflicts prevent the migration."
        case .cleanExamples:
            return "Use the configured model to clean saved example sentences (stage 10). Existing stage outputs are updated. Provider charges may apply."
        case .cleanCandidates:
            return "Use the configured model to clean saved dictionary candidates (stage 11). Existing stage outputs are updated. Provider charges may apply."
        case .generateExamples:
            return "Generate missing examples for the saved dictionary candidates (stage 13). Existing stage outputs are updated. Provider charges may apply."
        case .retryExamples:
            return "Retry only unresolved stage-13 example failures, updating saved examples in place. Provider charges may apply."
        case .linkExamples:
            return "Link saved cleaned examples to dictionary candidates (stage 12). Existing stage output is replaced. No model call is needed."
        case .assignMeanings:
            return "Match saved vocabulary against the reference dictionary (stage 14). Existing stage output is replaced. No model call is needed."
        case .generateMeanings:
            return "Generate missing Korean meanings and update the final dictionary (stage 15). Provider charges may apply."
        case .rewriteWholeSources:
            return "Rewrite each parsed source as a whole document without vocabulary guidance. Results go into the whole-document rewrite folder. Existing rewrites may be replaced; provider charges may apply."
        case .previewSourceDuplicates:
            return "Compare dictionary source files for exact duplicates and show the proposed quarantine plan. No files are moved."
        case .applySourceDuplicates:
            return "Move recommended exact source duplicates into the source archive's hidden quarantine folder. Files are retained, not deleted. Review the duplicate plan first."
        }
    }
    var destructive: Bool {
        [.prepareDictionary, .applySourceNames, .applyArchiveNames, .applyDuplicates,
         .applySourceDuplicates, .migrateWorkspace].contains(self)
    }
    var mutatesWorkspace: Bool {
        ![.previewSourceNames, .previewArchiveNames, .previewDuplicates, .previewSourceDuplicates].contains(self)
    }
}

enum StudioCommandBuilder {
    static func arguments(for action: StudioAction, snapshot: WorkspaceSnapshot,
                          settings: StudioSettings, selectedReport: String) throws -> [String] {
        if action == .migrateWorkspace { return ["--migrate-grade-workspaces", "--apply"] }
        guard (1...12).map({ "grade\($0)" }).contains(snapshot.grade) else {
            throw StudioFailure("Choose a grade from 1 through 12.")
        }
        guard !snapshot.legacyLayout || !action.mutatesWorkspace else {
            throw StudioFailure("Review and organize the legacy workspace before making changes. Your existing files remain available to browse.")
        }
        let settings = try settings.validated()
        func path(_ key: String) throws -> String {
            guard let value = snapshot.runPaths[key], value.hasPrefix("/") else {
                throw StudioFailure("The workspace did not provide a valid \(key) path. Refresh and try again.")
            }
            return value
        }
        var flags = ["--target-grade", snapshot.grade, "--llm-provider", settings.provider,
                     "--llm-model", settings.model]
        if !settings.baseURL.isEmpty { flags += ["--llm-base-url", settings.baseURL] }
        switch action {
        case .buildDictionary, .prepareDictionary:
            flags += [action == .buildDictionary ? "--build-dictionary" : "--prep-dictionary",
                      "--input", try path("dictionary_input_dir"),
                      "--output", try path("dictionary_output_dir"),
                      "--reference-dictionary", try path("reference_dictionary_path")]
            if settings.force { flags.append("--force") }
        case .previewSourceNames, .applySourceNames:
            flags += ["--rename-dictionary-source-archive", try path("dictionary_input_dir")]
            if action == .applySourceNames { flags.append("--apply-dictionary-source-renames") }
        case .previewSourceDuplicates, .applySourceDuplicates:
            flags += ["--resolve-dictionary-source-duplicates", try path("dictionary_input_dir")]
            if action == .applySourceDuplicates { flags.append("--yes") }
        case .cleanExamples, .cleanCandidates, .generateExamples, .retryExamples, .linkExamples,
             .assignMeanings, .generateMeanings:
            let operation: String
            switch action {
            case .cleanExamples: operation = "--clean-example-sentences"
            case .cleanCandidates: operation = "--clean-dictionary-candidates"
            case .generateExamples: operation = "--generate-missing-examples"
            case .retryExamples: operation = "--retry-generated-examples"
            case .linkExamples: operation = "--link-cleaned-examples"
            case .assignMeanings: operation = "--add-reference-meanings"
            default: operation = "--generate-missing-meanings"
            }
            flags += [operation, "--input", try path("dictionary_input_dir"),
                      "--output", try path("dictionary_output_dir"),
                      "--reference-dictionary", try path("reference_dictionary_path")]
        case .buildClassroom:
            flags += ["--build-class-printouts", try path("class_source_dir"),
                      "--source-output-root", try path("source_output_root"),
                      "--rewrite-output-root", try path("rewrite_output_root"),
                      "--print-output-root", try path("print_output_root"),
                      "--output", try path("dictionary_output_dir"),
                      "--live-lexicon", try path("live_lexicon_path")]
            if settings.force { flags.append("--force-class-printouts") }
        case .parseSources:
            flags += ["--parse-source-materials", try path("class_source_dir"),
                      "--source-output-root", try path("source_output_root")]
        case .rewriteSources:
            flags += ["--rewrite-vocabulary-guided-source-materials", try path("source_output_root"),
                      "--rewrite-output-root", try path("rewrite_output_root"),
                      "--output", try path("dictionary_output_dir")]
        case .rewriteWholeSources:
            flags += ["--rewrite-whole-source-materials", try path("source_output_root"),
                      "--rewrite-output-root", try path("whole_rewrite_output_root")]
        case .printHandouts:
            flags += ["--print-class-materials", try path("rewrite_output_root"),
                      "--print-output-root", try path("print_output_root")]
        case .previewArchiveNames, .applyArchiveNames:
            flags += ["--rename-class-session-archive", try path("archive_root")]
            if action == .applyArchiveNames { flags.append("--apply-archive-renames") }
        case .previewDuplicates, .applyDuplicates:
            flags += ["--resolve-archive-duplicates", try path("archive_root")]
            if action == .applyDuplicates { flags.append("--yes") }
        case .buildUsage:
            flags += ["--build-usage-signals", "--usage-signal-input-root", try path("archive_root"),
                      "--usage-signal-output-root", try path("usage_signal_output_root")]
        case .updateLexicon:
            flags += ["--update-live-lexicon", "--canonical-dictionary",
                      URL(fileURLWithPath: try path("dictionary_output_dir")).appendingPathComponent("15_final_dictionary.jsonl").path,
                      "--usage-candidates", try path("usage_candidates_path"),
                      "--live-lexicon", try path("live_lexicon_path"),
                      "--supplemental-lexicon", try path("supplemental_lexicon_path"),
                      "--unmatched-usage-output", try path("unmatched_usage_output_path")]
        case .generateReport:
            guard !selectedReport.isEmpty,
                  snapshot.files["archive", default: []].contains(where: { $0.path == selectedReport }),
                  ["docx", "pdf"].contains(URL(fileURLWithPath: selectedReport).pathExtension.lowercased()) else {
                throw StudioFailure("Select an archived DOCX or PDF handout for the reading report.")
            }
            flags += ["--generate-after-class-reading-report", selectedReport,
                      "--after-class-report-output-root", try path("after_class_report_output_root")]
        case .migrateWorkspace: break
        }
        if [.buildDictionary, .prepareDictionary, .buildUsage].contains(action) {
            if settings.includeNumbers { flags.append("--include-numbers") }
            if settings.includePunctuation { flags.append("--include-punctuation") }
        }
        if [.buildClassroom, .parseSources].contains(action) {
            flags += ["--pdf-render-dpi", String(settings.renderDPI)]
            if !settings.parsePDF { flags.append("--skip-pdf-parse") }
        }
        if [.buildClassroom, .printHandouts].contains(action) {
            flags += ["--print-body-font-size", String(settings.bodyFontSize)]
        }
        return flags
    }
}

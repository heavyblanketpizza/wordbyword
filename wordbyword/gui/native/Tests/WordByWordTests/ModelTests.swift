import Foundation
import XCTest
@testable import WordByWord

final class ModelTests: XCTestCase {
    private let pathKeys = [
        "dictionary_input_dir", "dictionary_output_dir", "reference_dictionary_path", "class_source_dir",
        "source_output_root", "whole_rewrite_output_root", "rewrite_output_root", "print_output_root",
        "archive_root", "usage_signal_output_root", "usage_candidates_path", "live_lexicon_dir",
        "live_lexicon_path", "supplemental_lexicon_path", "unmatched_usage_output_path", "after_class_report_output_root"
    ]

    private func snapshot(grade: String = "grade2", legacy: Bool = false, root: String = "/workspace",
                          report: String = "/workspace/archive_root/grade2/lesson.docx",
                          artifacts: [StudioArtifact] = [], counts: [String: Int] = [:]) -> WorkspaceSnapshot {
        let canonical = Dictionary(uniqueKeysWithValues: pathKeys.map { ($0, "\(root)/\($0)/\(grade)") })
        let browsing = Dictionary(uniqueKeysWithValues: pathKeys.map { ($0, "/legacy/\($0)") })
        return WorkspaceSnapshot(workspace: root, grade: grade, gradeLabel: "2학년", legacyLayout: legacy,
                                 warnings: [], paths: browsing, runPaths: canonical, counts: counts,
                                 files: ["archive": [StudioFile(id: report, name: "lesson.docx", path: report,
                                                                kind: "DOCX", size: 12, modified: "")]],
                                 artifacts: artifacts, summary: [:])
    }

    func testEveryActionUsesExpectedCLICommandAndCanonicalPaths() throws {
        let commands: [StudioAction: String] = [
            .buildDictionary: "--build-dictionary", .prepareDictionary: "--prep-dictionary",
            .previewSourceNames: "--rename-dictionary-source-archive", .applySourceNames: "--rename-dictionary-source-archive",
            .buildClassroom: "--build-class-printouts", .parseSources: "--parse-source-materials",
            .rewriteSources: "--rewrite-vocabulary-guided-source-materials", .printHandouts: "--print-class-materials",
            .previewArchiveNames: "--rename-class-session-archive", .applyArchiveNames: "--rename-class-session-archive",
            .previewDuplicates: "--resolve-archive-duplicates", .applyDuplicates: "--resolve-archive-duplicates",
            .buildUsage: "--build-usage-signals", .updateLexicon: "--update-live-lexicon",
            .generateReport: "--generate-after-class-reading-report", .migrateWorkspace: "--migrate-grade-workspaces",
            .cleanExamples: "--clean-example-sentences", .cleanCandidates: "--clean-dictionary-candidates",
            .generateExamples: "--generate-missing-examples", .retryExamples: "--retry-generated-examples",
            .linkExamples: "--link-cleaned-examples", .assignMeanings: "--add-reference-meanings",
            .generateMeanings: "--generate-missing-meanings", .rewriteWholeSources: "--rewrite-whole-source-materials",
            .previewSourceDuplicates: "--resolve-dictionary-source-duplicates",
            .applySourceDuplicates: "--resolve-dictionary-source-duplicates"
        ]
        let source = snapshot()
        var koreanSettings = StudioSettings()
        koreanSettings.language = .korean
        for action in StudioAction.allCases {
            let flags = try StudioCommandBuilder.arguments(for: action, snapshot: source,
                                                           settings: StudioSettings(), selectedReport: source.files["archive"]![0].path)
            let koreanFlags = try StudioCommandBuilder.arguments(for: action, snapshot: source,
                                                                 settings: koreanSettings, selectedReport: source.files["archive"]![0].path)
            XCTAssertEqual(koreanFlags, flags, "Interface language changed arguments for \(action)")
            XCTAssertTrue(flags.contains(try XCTUnwrap(commands[action])), "\(action)")
            XCTAssertFalse(flags.contains(where: { $0.hasPrefix("/legacy/") }), "\(action)")
            if action != .migrateWorkspace {
                XCTAssertEqual(Array(flags.prefix(2)), ["--target-grade", "grade2"], "\(action)")
            } else {
                XCTAssertEqual(flags, ["--migrate-grade-workspaces", "--apply"])
            }
        }
    }

    func testPreviewActionsNeverApplyTheirPlans() throws {
        for action in [StudioAction.previewSourceNames, .previewArchiveNames, .previewDuplicates, .previewSourceDuplicates] {
            let flags = try StudioCommandBuilder.arguments(for: action, snapshot: snapshot(),
                                                           settings: StudioSettings(), selectedReport: "")
            XCTAssertFalse(flags.contains("--yes"))
            XCTAssertFalse(flags.contains("--apply-dictionary-source-renames"))
            XCTAssertFalse(flags.contains("--apply-archive-renames"))
        }
        for (action, flag) in [(StudioAction.applySourceNames, "--apply-dictionary-source-renames"),
                              (.applyArchiveNames, "--apply-archive-renames"),
                              (.applyDuplicates, "--yes"), (.applySourceDuplicates, "--yes")] {
            let flags = try StudioCommandBuilder.arguments(for: action, snapshot: snapshot(),
                                                           settings: StudioSettings(), selectedReport: "")
            XCTAssertTrue(flags.contains(flag))
        }
    }

    func testLegacyWorkspaceBlocksEveryOrdinaryMutation() throws {
        for action in StudioAction.allCases where action != .migrateWorkspace {
            if action.mutatesWorkspace {
                XCTAssertThrowsError(try StudioCommandBuilder.arguments(for: action, snapshot: snapshot(legacy: true),
                                                                         settings: StudioSettings(), selectedReport: ""), "\(action)")
            } else {
                XCTAssertNoThrow(try StudioCommandBuilder.arguments(for: action, snapshot: snapshot(legacy: true),
                                                                     settings: StudioSettings(), selectedReport: ""), "\(action)")
            }
        }
        XCTAssertTrue(StudioAction.prepareDictionary.destructive)
        XCTAssertTrue(StudioAction.prepareDictionary.detail.contains("removes existing stages 10–15"))
        XCTAssertEqual(StudioAction.buildDictionary.title, "Build dictionary 00–15")
        XCTAssertTrue(StudioAction.buildDictionary.detail.contains("stages 00–15"))
        XCTAssertTrue(StudioAction.buildDictionary.detail.contains("vocabulary distribution"))
        XCTAssertFalse(StudioAction.buildDictionary.detail.localizedCaseInsensitiveContains("charge"))
        XCTAssertFalse(StudioLanguage.korean.text(StudioAction.buildDictionary.detail).contains("이용 요금"))
    }

    func testWorkflowSpecificSettingsMapWithoutLeakingForceFlags() throws {
        var settings = StudioSettings()
        settings.force = true
        settings.includeNumbers = true
        settings.includePunctuation = true
        settings.parsePDF = false
        settings.renderDPI = 288
        settings.bodyFontSize = 15
        settings.provider = "openai"
        settings.model = "chosen-model"
        settings.baseURL = "https://example.com/v1"
        for action in StudioAction.allCases where action != .migrateWorkspace {
            let flags = try StudioCommandBuilder.arguments(for: action, snapshot: snapshot(), settings: settings,
                                                           selectedReport: "/workspace/archive_root/grade2/lesson.docx")
            XCTAssertEqual(flags.contains("--force"), [.buildDictionary, .prepareDictionary].contains(action))
            XCTAssertEqual(flags.contains("--force-class-printouts"), action == .buildClassroom)
            XCTAssertEqual(flags.contains("--skip-pdf-parse"), [.buildClassroom, .parseSources].contains(action))
            if [.buildClassroom, .parseSources].contains(action) {
                let index = try XCTUnwrap(flags.firstIndex(of: "--pdf-render-dpi"))
                XCTAssertEqual(flags[index + 1], "288")
            }
            if [.buildClassroom, .printHandouts].contains(action) {
                let index = try XCTUnwrap(flags.firstIndex(of: "--print-body-font-size"))
                XCTAssertEqual(flags[index + 1], "15")
            }
            XCTAssertTrue(flags.contains("https://example.com/v1"))
        }
    }

    func testInvalidGradesAndReportsAreRejected() throws {
        XCTAssertThrowsError(try StudioCommandBuilder.arguments(for: .buildDictionary, snapshot: snapshot(grade: "grade99"),
                                                                 settings: StudioSettings(), selectedReport: ""))
        for report in ["", "/outside/lesson.docx", "/workspace/archive_root/grade2/lesson.txt"] {
            XCTAssertThrowsError(try StudioCommandBuilder.arguments(for: .generateReport, snapshot: snapshot(),
                                                                     settings: StudioSettings(), selectedReport: report))
        }
        XCTAssertThrowsError(try StudioCommandBuilder.arguments(for: .generateReport,
                                                                 snapshot: snapshot(report: "/workspace/archive_root/grade2/lesson.txt"),
                                                                 settings: StudioSettings(), selectedReport: "/workspace/archive_root/grade2/lesson.txt"))
    }

    func testSettingsValidateAndDoNotStoreCredentials() throws {
        for address in ["ftp://localhost", "https://secret@example.com", "https://example.com?api_key=secret", "http://", "https://example.com/#secret"] {
            var settings = StudioSettings()
            settings.baseURL = address
            XCTAssertThrowsError(try settings.validated(), address)
        }
        var settings = StudioSettings()
        settings.model = "   "
        XCTAssertThrowsError(try settings.validated())
        settings.model = " gemma4:31b "
        settings.provider = " OLLAMA "
        settings.baseURL = " http://localhost:11434 "
        XCTAssertEqual(try settings.validated().model, "gemma4:31b")
        settings.renderDPI = 0
        XCTAssertThrowsError(try settings.validated())
        settings.renderDPI = 144
        settings.bodyFontSize = 22
        XCTAssertThrowsError(try settings.validated())
    }

    func testSettingsOnlyWriteOnExplicitSaveAndCanBeUpdated() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        XCTAssertEqual(try StudioSettings.read(from: root), StudioSettings())
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: root.path), [])
        var settings = StudioSettings()
        settings.includeNumbers = true
        settings.language = .korean
        try settings.write(to: root)
        XCTAssertEqual(try StudioSettings.read(from: root), settings)
        settings.model = "second-model"
        settings.language = .english
        try settings.write(to: root)
        XCTAssertEqual(try StudioSettings.read(from: root), settings)
        let contents = try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent(".wordbyword").path)
        XCTAssertEqual(contents, ["studio-settings.json"])
    }

    func testLegacyAndUnknownLanguageSettingsPreserveCustomizedFieldsWithoutRewriting() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let directory = root.appendingPathComponent(".wordbyword")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        let destination = directory.appendingPathComponent("studio-settings.json")
        var expected = StudioSettings()
        expected.provider = "openai"
        expected.model = "custom-model"
        expected.baseURL = "https://example.com/v1"
        expected.includeNumbers = true
        expected.includePunctuation = true
        expected.force = true
        expected.parsePDF = false
        expected.renderDPI = 288
        expected.bodyFontSize = 15

        let storedLanguages: [String?] = [nil, "future-language"]
        for language in storedLanguages {
            // This is the pre-localization file schema, independent of the new encoder.
            var stored: [String: Any] = [
                "provider": "openai", "model": "custom-model", "baseURL": "https://example.com/v1",
                "includeNumbers": true, "includePunctuation": true, "force": true,
                "parsePDF": false, "renderDPI": 288, "bodyFontSize": 15
            ]
            if let language { stored["language"] = language }
            let data = try JSONSerialization.data(withJSONObject: stored)
            try data.write(to: destination)
            XCTAssertEqual(try StudioSettings.read(from: root), expected, language ?? "Missing language")
            XCTAssertEqual(try Data(contentsOf: destination), data, "Reading settings must not migrate the file implicitly")
        }
    }

    func testEveryTabAndActionHasKoreanPresentationWithoutChangingEnglish() {
        let keys = StudioTab.allCases.flatMap { [$0.title, $0.eyebrow, $0.headline, $0.subtitle] }
            + StudioAction.allCases.flatMap { [$0.title, $0.detail] }
        for key in keys {
            XCTAssertEqual(StudioLanguage.english.text(key), key)
            let translated = StudioLanguage.korean.text(key)
            XCTAssertNotEqual(translated, key, "Missing Korean translation: \(key)")
            XCTAssertNotNil(translated.range(of: "[가-힣]", options: .regularExpression), key)
        }
    }

    func testLocalizationFormatsValuesAndPreservesUnknownContent() throws {
        let korean = StudioLanguage.korean
        XCTAssertEqual(StudioLanguage.english.format("Grade %d", 3), "Grade 3")
        XCTAssertEqual(korean.format("Grade %d", 3), "3학년")
        XCTAssertEqual(korean.format("%d FILES", 0), "파일 0개")
        XCTAssertEqual(korean.format("%@ pages · %@ sentences in the saved build", "1,234", "2,160"),
                       "저장된 생성 결과: 1,234쪽 · 2,160문장")
        let phaseLabels = [
            "prepare": "준비", "run": "실행", "cleanup": "마무리 정리", "plan": "계획 수립",
            "rename": "이름 정리", "render": "페이지 렌더링", "select": "선별", "ocr": "문자 인식"
        ]
        for (phase, label) in phaseLabels {
            XCTAssertEqual(korean.phase(phase), label)
            XCTAssertEqual(StudioLanguage.english.phase(phase), phase.uppercased())
        }

        let path = "/workspace/Completed/100%_읽기.docx"
        XCTAssertEqual(korean.format("Selected: %@", path), "선택한 파일: " + path)
        for raw in [path, "A future engine diagnostic", "Connection refused: /workspace/Complete/source.pdf",
                    "원문 문장: Complete is a word, not an interface label."] {
            XCTAssertEqual(korean.text(raw), raw)
            XCTAssertEqual(StudioLanguage.english.text(raw), raw)
        }

        let rawPDF = "PDF document · 1,234 bytes\n\nOpen the original file to view its pages."
        let pdf = PreviewDocument(name: "reading.PDF", path: "/workspace/reading.PDF", text: rawPDF,
                                  truncated: false, byteCount: 1234)
        XCTAssertEqual(korean.previewText(pdf), "PDF 문서 · 1,234바이트\n\n페이지를 보려면 원본 파일을 여세요.")
        XCTAssertEqual(StudioLanguage.english.previewText(pdf), rawPDF)
        for extensionName in ["txt", "docx", "jsonl"] {
            let document = PreviewDocument(name: "reading.\(extensionName)", path: "/workspace/reading.\(extensionName)",
                                           text: "Complete", truncated: false, byteCount: 1234)
            XCTAssertEqual(korean.previewText(document), "Complete", "Document content must not use interface translations")
        }
        let oldResponse = try JSONSerialization.data(withJSONObject: [
            "name": "reading.pdf", "path": "/workspace/reading.pdf", "text": rawPDF, "truncated": false
        ])
        let oldPDF = try JSONDecoder().decode(PreviewDocument.self, from: oldResponse)
        XCTAssertEqual(korean.previewText(oldPDF), rawPDF, "An old PDF response must remain readable without metadata")
        XCTAssertNil(oldPDF.table)

        let tableResponse = try JSONSerialization.data(withJSONObject: [
            "name": "08_dictionary_candidates.jsonl", "path": "/workspace/08_dictionary_candidates.jsonl",
            "text": "raw", "truncated": false,
            "table": ["format": "JSONL", "columns": ["display", "count"],
                      "rows": [["물", "47"]], "truncated": false]
        ])
        let tablePreview = try JSONDecoder().decode(PreviewDocument.self, from: tableResponse)
        XCTAssertEqual(tablePreview.table?.format, "JSONL")
        XCTAssertEqual(tablePreview.table?.columns, ["display", "count"])
        XCTAssertEqual(tablePreview.table?.rows, [["물", "47"]])
        XCTAssertEqual(korean.text("Table"), "표")
        XCTAssertEqual(korean.format("Showing %d rows · %d columns", 1, 2), "행 1개 · 열 2개 표시")
    }

    func testInterfaceTypographyUsesTheRequestedScale() {
        XCTAssertEqual(StudioType.scale, 1.25)
    }

    func testMigrationLocalizesExplanationsButPreservesPathsAndRawDiagnostics() {
        let raw = [
            "GRADE WORKSPACE MIGRATION", "", "2 proposed move(s).", "No files have been changed.", "",
            "build-dictionary/original/Complete.pdf",
            "  → build-dictionary/original/grade3/Complete.pdf",
            "  clear grade token", "",
            "build-dictionary/No files have been changed..json",
            "  → build-dictionary/output/grade2/No files have been changed..json",
            "  legacy grade2 dictionary output", "",
            "Migration cannot be applied safely:",
            "  [Errno 13] Permission denied: '/workspace/Complete.pdf'",
            "\tUnknown diagnostic: Failed → /workspace/Read"
        ].joined(separator: "\n")
        let expected = [
            "학년별 작업 폴더 정리 계획", "", "2개 항목을 이동할 예정입니다.", "아직 변경된 파일은 없습니다.", "",
            "build-dictionary/original/Complete.pdf",
            "  → build-dictionary/original/grade3/Complete.pdf",
            "  파일명에서 학년 확인", "",
            "build-dictionary/No files have been changed..json",
            "  → build-dictionary/output/grade2/No files have been changed..json",
            "  기존 2학년 사전 결과", "",
            "다음 문제를 해결해야 정리 계획을 적용할 수 있습니다:",
            "  [Errno 13] Permission denied: '/workspace/Complete.pdf'",
            "\tUnknown diagnostic: Failed → /workspace/Read"
        ].joined(separator: "\n")
        let preview = PreviewDocument(name: "Workspace migration", path: "/workspace", text: raw,
                                      truncated: false, canApply: false, plannedCount: 2)
        XCTAssertEqual(StudioLanguage.korean.previewText(preview), expected)
        XCTAssertEqual(StudioLanguage.english.migrationText(raw), raw)
    }

    func testArtifactLocalizationUsesStableIDsWithSafeFallbacks() {
        let labels = [
            "summary": "실행 정보, 수량, 단계 목록",
            "generated_example_failures": "예문 생성 실패 기록",
            "final_dictionary": "부족한 뜻풀이를 보완한 최종 사전"
        ]
        for (id, expected) in labels {
            let artifact = StudioArtifact(id: id, name: "renamed-output.jsonl",
                                          label: "A future backend description for \(id)",
                                          path: "/workspace/Completed/renamed-output.jsonl", exists: true, size: 42)
            XCTAssertEqual(StudioLanguage.korean.artifactLabel(artifact), expected)
            XCTAssertEqual(StudioLanguage.english.artifactLabel(artifact), artifact.label)
        }
        let unknown = StudioArtifact(id: "future_artifact", name: "15_final_dictionary.jsonl", label: "Complete",
                                     path: "/workspace/15_final_dictionary.jsonl", exists: false, size: 0)
        XCTAssertEqual(StudioLanguage.korean.artifactLabel(unknown), unknown.label)
    }

    func testSettingsRejectSymbolicLinkDestinations() throws {
        let root = try temporaryDirectory()
        let other = try temporaryDirectory()
        defer {
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: other)
        }
        try FileManager.default.createSymbolicLink(at: root.appendingPathComponent(".wordbyword"), withDestinationURL: other)
        XCTAssertThrowsError(try StudioSettings().write(to: root))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: other.path), [])
    }

    func testPythonVenvSymlinksAreNeverResolved() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let python = root.appendingPathComponent("python")
        try FileManager.default.createSymbolicLink(at: python, withDestinationURL: URL(fileURLWithPath: "/usr/bin/python3"))
        let runtime = StudioRuntime.resolve(arguments: ["WordByWord", "--workspace", root.path, "--python", python.path])
        XCTAssertEqual(runtime.python.path, python.path)
        XCTAssertNotEqual(runtime.python.path, runtime.python.resolvingSymlinksInPath().path)
        XCTAssertEqual(runtime.workspace.path, root.path)
    }

    func testRuntimeSeparatesBundledCodeRootFromSelectedDataWorkspace() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let bundle = try runtimeBundle(in: root, configuration: [
            "python": "/installed/project/.venv/bin/python",
            "workspace": "/original/data",
            "module_root": "/installed/project"
        ])
        let runtime = StudioRuntime.resolve(arguments: ["WordByWord", "--workspace", "/different/data"],
                                            bundle: bundle, currentDirectory: root)
        XCTAssertEqual(runtime.workspace.path, "/different/data")
        XCTAssertEqual(runtime.moduleRoot.path, "/installed/project")
        XCTAssertEqual(runtime.python.path, "/installed/project/.venv/bin/python")
    }

    func testRuntimeAcceptsLegacyBundlesWithoutModuleRoot() throws {
        let root = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let bundle = try runtimeBundle(in: root, configuration: [
            "python": "/original/project/.venv/bin/python", "workspace": "/original/project"
        ])
        let runtime = StudioRuntime.resolve(arguments: ["WordByWord", "--workspace", "/different/data"],
                                            bundle: bundle, currentDirectory: root)
        XCTAssertEqual(runtime.workspace.path, "/different/data")
        XCTAssertEqual(runtime.moduleRoot.path, "/original/project")
    }

    func testNativeDiagnosticsRedactEnvironmentSecretsURLsAndHeaders() {
        let input = """
        Connection refused: synthetic-environment-secret
        GET https://username:synthetic-url-password@example.test/v1?api_key=synthetic-query-secret&model=test
        Authorization: Bearer synthetic-header-secret
        Proxy-Authorization: Basic c3ludGhldGljOnBhc3N3b3Jk
        {"x-api-key": "synthetic-json-secret"}
        token header Bearer synthetic-bare-secret
        password=synthetic-assigned-secret
        """
        let output = StudioDiagnostics.redacted(input, environment: ["EXAMPLE_API_KEY": "synthetic-environment-secret"])
        for secret in ["synthetic-environment-secret", "synthetic-url-password", "synthetic-query-secret",
                       "synthetic-header-secret", "c3ludGhldGljOnBhc3N3b3Jk", "synthetic-json-secret",
                       "synthetic-bare-secret", "synthetic-assigned-secret"] {
            XCTAssertFalse(output.contains(secret))
        }
        XCTAssertTrue(output.contains("Connection refused"))
        XCTAssertTrue(output.contains("example.test/v1"))
        XCTAssertTrue(output.contains("[redacted]"))
    }

    @MainActor
    func testModelGuardsRunningWorkAndRequiresMigrationReview() throws {
        let runtime = StudioRuntime(workspace: URL(fileURLWithPath: "/workspace"),
                                    python: URL(fileURLWithPath: "/usr/bin/python3"),
                                    moduleRoot: URL(fileURLWithPath: "/workspace"))
        let model = StudioModel(runtime: runtime, autoRefresh: false)
        model.snapshot = snapshot()
        XCTAssertThrowsError(try model.arguments(for: .migrateWorkspace))
        model.isRunning = true
        model.changeGrade("grade4")
        model.settings.model = "changed"
        model.requestRun(.buildDictionary)
        XCTAssertEqual(model.grade, "grade2")
        XCTAssertEqual(model.settings.model, "gemma4:31b")
        XCTAssertNil(model.pendingAction)
        model.isRunning = false
        model.requestRun(.buildDictionary)
        XCTAssertEqual(model.pendingAction, .buildDictionary)
    }

    @MainActor
    func testSuccessfulDictionaryBuildRefreshesAndOpensVisualization() async throws {
        let script = """
        import json, os, sys
        request = json.load(sys.stdin)
        root = sys.argv[-1]
        final_path = os.path.join(root, 'dictionary_output_dir', 'grade2', '15_final_dictionary.jsonl')
        if request['operation'] == 'run':
            with open(os.path.join(root, 'run-request.json'), 'w') as output:
                json.dump(request, output)
            print(json.dumps({"type":"result", "data":{"exit_code":0, "status":"complete",
              "detail":"full dictionary complete", "artifacts":{"final_dictionary":final_path}, "error":""}}), flush=True)
        elif request['operation'] == 'snapshot':
            artifact = {"id":"final_dictionary", "name":"15_final_dictionary.jsonl", "label":"Final dictionary",
                        "path":final_path, "exists":True, "size":200}
            print(json.dumps({"type":"snapshot", "data":{"workspace":root, "grade":request['grade'],
              "grade_label":"2학년", "legacy_layout":False, "warnings":[], "paths":{}, "run_paths":{},
              "counts":{"sources":1,"words":3}, "files":{}, "artifacts":[artifact], "summary":{}}}), flush=True)
        elif request['operation'] == 'distribution':
            assert request['path'] == final_path
            print(json.dumps({"type":"distribution", "data":{"artifact_id":"final_dictionary",
              "name":"15_final_dictionary.jsonl", "path":final_path, "stage":"15", "entry_count":3,
              "learning_signal":{"bins":[1,1,1],"minimum":-0.5,"median":0.0,"maximum":0.5,
                                   "axis_labels":["-0.50","0.00","0.50"]},
              "occurrence_count":{"bins":[1,1,1],"minimum":1,"median":5,"maximum":20,
                                    "axis_labels":["1","4","20"]}}}), flush=True)
        else:
            raise AssertionError(request)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = StudioRuntime(workspace: fixture.root, python: fixture.root.appendingPathComponent("test-python"),
                                    moduleRoot: fixture.root)
        let model = StudioModel(runtime: runtime, autoRefresh: false)
        model.snapshot = snapshot(grade: "grade2", root: fixture.root.path, counts: ["sources": 1])

        model.requestDictionaryBuild()
        XCTAssertEqual(model.pendingAction, .buildDictionary)
        model.confirmRun()
        try await waitUntil { !model.isRunning && !model.isLoading && model.distribution != nil }

        let requestData = try Data(contentsOf: fixture.root.appendingPathComponent("run-request.json"))
        let request = try XCTUnwrap(try JSONSerialization.jsonObject(with: requestData) as? [String: Any])
        let arguments = try XCTUnwrap(request["arguments"] as? [String])
        XCTAssertTrue(arguments.contains("--build-dictionary"))
        XCTAssertFalse(arguments.contains("--show-distribution"))
        XCTAssertTrue(model.lastResult?.succeeded == true)
        XCTAssertEqual(model.distribution?.stage, "15")
        XCTAssertEqual(model.distribution?.entryCount, 3)
        XCTAssertEqual(model.distribution?.learningSignal.bins, [1, 1, 1])
    }

    @MainActor
    func testSuccessfulBuildDoesNotVisualizeAnOlderIntermediateArtifactAsStageFifteen() async throws {
        let script = """
        import json, os, sys
        request = json.load(sys.stdin)
        root = sys.argv[-1]
        if request['operation'] == 'run':
            print(json.dumps({"type":"result", "data":{"exit_code":0, "status":"complete",
              "detail":"full dictionary complete", "artifacts":{}, "error":""}}), flush=True)
        elif request['operation'] == 'snapshot':
            path = os.path.join(root, '08_dictionary_candidates.jsonl')
            artifact = {"id":"dictionary_candidates", "name":"08_dictionary_candidates.jsonl",
                        "label":"Candidates", "path":path, "exists":True, "size":100}
            print(json.dumps({"type":"snapshot", "data":{"workspace":root, "grade":request['grade'],
              "grade_label":"2학년", "legacy_layout":False, "warnings":[], "paths":{}, "run_paths":{},
              "counts":{"sources":1,"words":0}, "files":{}, "artifacts":[artifact], "summary":{}}}), flush=True)
        else:
            open(os.path.join(root, 'unexpected-distribution-request'), 'w').close()
            raise AssertionError(request)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = StudioRuntime(workspace: fixture.root, python: fixture.root.appendingPathComponent("test-python"),
                                    moduleRoot: fixture.root)
        let model = StudioModel(runtime: runtime, autoRefresh: false)
        model.snapshot = snapshot(grade: "grade2", root: fixture.root.path, counts: ["sources": 1])

        model.requestDictionaryBuild()
        model.confirmRun()
        try await waitUntil { !model.isRunning && !model.isLoading && model.error != nil }

        XCTAssertTrue(model.lastResult?.succeeded == true)
        XCTAssertNil(model.distribution)
        XCTAssertTrue(model.error?.contains("stage-15") == true)
        XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.root
            .appendingPathComponent("unexpected-distribution-request").path))
    }

    func testBridgeDrainsStderrAndFragmentedPacketsBeforeFinishing() async throws {
        let script = """
        import json, sys, time
        sys.stdin.read()
        event = json.dumps({"type":"event", "data":{"workflow":"test", "phase":"reading", "detail":"한국어", "status":"running", "current":1, "total":2, "artifacts":{}, "error":""}}, ensure_ascii=False).encode()
        sys.stdout.buffer.write(event[:17]); sys.stdout.buffer.flush()
        time.sleep(0.02)
        sys.stdout.buffer.write(event[17:] + b'\\n'); sys.stdout.buffer.flush()
        sys.stderr.write('x' * 150000); sys.stderr.flush()
        print(json.dumps({"type":"result", "data":{"exit_code":0, "status":"completed", "detail":"Done", "artifacts":{}, "error":""}}), end='', flush=True)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        var packets: [BridgePacket] = []
        for try await packet in fixture.client.packets(for: BridgeRequest(operation: "run", arguments: [])) {
            packets.append(packet)
        }
        XCTAssertEqual(packets.count, 2)
        guard case .event(let event) = packets[0], case .result(let result) = packets[1] else {
            return XCTFail("Expected streamed event followed by completion")
        }
        XCTAssertEqual(event.detail, "한국어")
        XCTAssertTrue(result.succeeded)
    }

    func testBridgeDecodesDistributionAsItsOwnTerminalPacket() async throws {
        let script = """
        import json, sys
        request = json.load(sys.stdin)
        assert request['operation'] == 'distribution'
        print(json.dumps({"type":"distribution", "data":{
          "artifact_id":"final_dictionary", "name":"15_final_dictionary.jsonl", "path":request['path'],
          "stage":"15", "entry_count":3,
          "learning_signal":{"bins":[1,2], "minimum":-0.5, "median":0.0, "maximum":0.5, "axis_labels":["-0.50","0.00","0.50"]},
          "occurrence_count":{"bins":[2,1], "minimum":1, "median":5, "maximum":20, "axis_labels":["1","4","20"]}
        }}), flush=True)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let path = fixture.root.appendingPathComponent("15_final_dictionary.jsonl").path
        var packets: [BridgePacket] = []
        for try await packet in fixture.client.packets(for: BridgeRequest(
            operation: "distribution", grade: "grade1", path: path
        )) { packets.append(packet) }
        XCTAssertEqual(packets.count, 1)
        guard case .distribution(let distribution) = packets[0] else {
            return XCTFail("Expected a distribution packet")
        }
        XCTAssertEqual(distribution.entryCount, 3)
        XCTAssertEqual(distribution.learningSignal.bins, [1, 2])
        XCTAssertEqual(distribution.occurrenceCount.maximum, 20)
    }

    func testBridgeRejectsMalformedMissingAndFalseSuccessResponses() async throws {
        let scenarios: [(String, String)] = [
            ("import sys\nsys.stdin.read()\nprint('not-json', flush=True)", "invalid response"),
            ("import sys\nsys.stdin.read()", "without a completion response"),
            ("import sys\nsys.stdin.read()\nprint('" + completedPacket + "', flush=True)\nsys.exit(3)", "exited unexpectedly"),
            ("import sys\nsys.stdin.read()\nprint('" + completedPacket + "', flush=True)\nprint('" + completedPacket + "', flush=True)", "more than one completion")
        ]
        for (source, expected) in scenarios {
            let fixture = try bridgeFixture(source)
            defer { try? FileManager.default.removeItem(at: fixture.root) }
            do {
                for try await _ in fixture.client.packets(for: BridgeRequest(operation: "run", arguments: [])) {}
                XCTFail("Expected failure for \(expected)")
            } catch {
                XCTAssertTrue(error.localizedDescription.contains(expected), error.localizedDescription)
            }
        }
    }

    func testFatalSubprocessDiagnosticsAreRedactedBeforeBecomingErrors() async throws {
        let script = """
        import os, sys
        sys.stdin.read()
        sys.stderr.write('Connection refused: ' + os.environ['WBW_TEST_API_KEY'] + '\\n')
        sys.stderr.write('Authorization: Bearer synthetic-stderr-header-secret\\n')
        sys.stderr.flush()
        sys.exit(1)
        """
        let fixture = try bridgeFixture(script, environment: ["WBW_TEST_API_KEY": "synthetic-stderr-environment-secret"])
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        do {
            for try await _ in fixture.client.packets(for: BridgeRequest(operation: "run", arguments: [])) {}
            XCTFail("Expected the worker failure")
        } catch {
            XCTAssertFalse(error.localizedDescription.contains("synthetic-stderr-environment-secret"))
            XCTAssertFalse(error.localizedDescription.contains("synthetic-stderr-header-secret"))
            XCTAssertTrue(error.localizedDescription.contains("Connection refused"))
            XCTAssertTrue(error.localizedDescription.contains("[redacted]"))
        }
    }

    func testBoundedStderrDiscardsPartialCredentialLines() async throws {
        let script = """
        import os, sys
        sys.stdin.read()
        secret = os.environ['WBW_TEST_API_KEY']
        sys.stderr.write('x' * 1000 + secret + 'z' * (32768 - 12))
        sys.stderr.flush()
        sys.exit(1)
        """
        let secret = "synthetic-long-secret-that-crosses-buffer-boundary"
        let fixture = try bridgeFixture(script, environment: ["WBW_TEST_API_KEY": secret])
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        do {
            for try await _ in fixture.client.packets(for: BridgeRequest(operation: "run", arguments: [])) {}
            XCTFail("Expected the worker failure")
        } catch {
            XCTAssertFalse(error.localizedDescription.contains(String(secret.suffix(12))))
            XCTAssertTrue(error.localizedDescription.contains("without a completion response"))
        }
    }

    func testCancellationWaitsForProcessExitAndKeepsFinalPackets() async throws {
        let script = """
        import json, signal, sys, time
        def stopped(signum, frame):
            time.sleep(0.25)
            print(json.dumps({"type":"result", "data":{"exit_code":130, "status":"cancelled", "detail":"Stopped", "artifacts":{}, "error":""}}), flush=True)
            sys.exit(130)
        signal.signal(signal.SIGINT, stopped)
        sys.stdin.read()
        print(json.dumps({"type":"event", "data":{"workflow":"test", "phase":"ready", "detail":"Waiting", "status":"running", "current":0, "total":0, "artifacts":{}, "error":""}}), flush=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline: time.sleep(0.02)
        sys.exit(2)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        var requestedAt: Date?
        var result: RunResult?
        for try await packet in fixture.client.packets(for: BridgeRequest(operation: "run", arguments: [])) {
            if case .event = packet {
                requestedAt = Date()
                fixture.client.cancel()
            }
            if case .result(let value) = packet { result = value }
        }
        XCTAssertGreaterThanOrEqual(Date().timeIntervalSince(try XCTUnwrap(requestedAt)), 0.20)
        XCTAssertEqual(result?.exitCode, 130)
        XCTAssertEqual(result?.status, "cancelled")
    }

    @MainActor
    func testAutoRefreshLoadsPersistedKoreanSettingsAndSnapshot() async throws {
        let script = """
        import json, sys
        request = json.load(sys.stdin)
        assert request['operation'] == 'snapshot'
        print(json.dumps({"type":"snapshot", "data":{"workspace":sys.argv[-1], "grade":request['grade'], "grade_label":"2학년", "legacy_layout":False, "warnings":[], "paths":{}, "run_paths":{}, "counts":{"words":42}, "files":{}, "artifacts":[], "summary":{}}}), flush=True)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        var saved = StudioSettings()
        saved.language = .korean
        saved.model = "saved-startup-model"
        try saved.write(to: fixture.root)
        let runtime = StudioRuntime(workspace: fixture.root, python: fixture.root.appendingPathComponent("test-python"),
                                    moduleRoot: fixture.root)
        let model = StudioModel(runtime: runtime, autoRefresh: true)
        XCTAssertTrue(model.isLoading)
        try await waitUntil { !model.isLoading }
        XCTAssertNil(model.error)
        XCTAssertEqual(model.language, .korean)
        XCTAssertEqual(model.settings, saved)
        let result = try XCTUnwrap(model.snapshot)
        XCTAssertEqual(result.workspace, fixture.root.path)
        XCTAssertEqual(result.grade, "grade2")
        XCTAssertEqual(result.counts["words"], 42)
    }

    @MainActor
    func testAppGradeCannotBeChangedFromGradeTwo() {
        let runtime = StudioRuntime(workspace: URL(fileURLWithPath: "/workspace"),
                                    python: URL(fileURLWithPath: "/usr/bin/python3"),
                                    moduleRoot: URL(fileURLWithPath: "/workspace"))
        let model = StudioModel(runtime: runtime, autoRefresh: false)
        model.changeGrade("grade1")
        XCTAssertEqual(model.grade, "grade2")
        XCTAssertThrowsError(try StudioCommandBuilder.arguments(
            for: .buildDictionary,
            snapshot: snapshot(grade: "grade1"),
            settings: StudioSettings(),
            selectedReport: ""
        ))
    }

    @MainActor
    func testModelStaysBusyUntilCancelledChildHasExited() async throws {
        let script = """
        import json, signal, sys, time
        request = json.load(sys.stdin)
        if request['operation'] == 'snapshot':
            print(json.dumps({"type":"snapshot", "data":{"workspace":sys.argv[-1], "grade":request['grade'], "grade_label":request['grade'], "legacy_layout":False, "warnings":[], "paths":{}, "run_paths":{}, "counts":{}, "files":{}, "artifacts":[], "summary":{}}}), flush=True)
            sys.exit(0)
        def stopped(signum, frame):
            time.sleep(0.3)
            print(json.dumps({"type":"result", "data":{"exit_code":130, "status":"cancelled", "detail":"Stopped", "artifacts":{}, "error":""}}), flush=True)
            sys.exit(130)
        signal.signal(signal.SIGINT, stopped)
        print(json.dumps({"type":"event", "data":{"workflow":"test", "phase":"ready", "detail":"Waiting", "status":"running", "current":0, "total":0, "artifacts":{}, "error":""}}), flush=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline: time.sleep(0.02)
        sys.exit(2)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = StudioRuntime(workspace: fixture.root, python: fixture.root.appendingPathComponent("test-python"), moduleRoot: fixture.root)
        let model = StudioModel(runtime: runtime, autoRefresh: false)
        model.snapshot = snapshot(grade: "grade2", root: fixture.root.path, counts: ["sources": 1])
        model.requestDictionaryBuild()
        XCTAssertEqual(model.pendingAction, .buildDictionary)
        model.confirmRun()
        try await waitUntil { model.logs.contains(where: { $0.phase == "ready" }) }
        XCTAssertTrue(model.isDictionaryBuildRunning)
        XCTAssertEqual(model.activeAction, .buildDictionary)
        model.requestDictionaryBuild()
        XCTAssertNil(model.pendingAction)
        model.cancelRun()
        XCTAssertTrue(model.isRunning)
        XCTAssertTrue(model.isCancelling)
        XCTAssertTrue(model.isDictionaryBuildRunning)
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertTrue(model.isRunning)
        try await waitUntil { !model.isRunning && !model.isLoading }
        XCTAssertEqual(model.lastResult?.status, "cancelled")
        XCTAssertFalse(model.isCancelling)
        XCTAssertFalse(model.isDictionaryBuildRunning)
        XCTAssertNil(model.activeAction)
        XCTAssertNil(model.error)
    }

    @MainActor
    func testIdenticalTerminalSummaryIsCoalescedWithoutDroppingDistinctEvents() async throws {
        let script = """
        import json, sys
        request = json.load(sys.stdin)
        if request['operation'] == 'snapshot':
            print(json.dumps({"type":"snapshot", "data":{"workspace":sys.argv[-1], "grade":request['grade'], "grade_label":request['grade'], "legacy_layout":False, "warnings":[], "paths":{}, "run_paths":{}, "counts":{}, "files":{}, "artifacts":[], "summary":{}}}), flush=True)
            sys.exit(0)
        for phase, detail, status, error in [('write', 'Saved', 'running', ''), ('source', 'Recovered source failure', 'failed', 'read failed'), ('complete', 'Saved', 'complete', '')]:
            print(json.dumps({"type":"event", "data":{"workflow":"test", "phase":phase, "detail":detail, "status":status, "current":0, "total":0, "artifacts":{"early":"partial"}, "error":error}}), flush=True)
        print(json.dumps({"type":"result", "data":{"exit_code":0, "status":"complete", "detail":"Saved", "artifacts":{"final":"output"}, "error":""}}), flush=True)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = StudioRuntime(workspace: fixture.root, python: fixture.root.appendingPathComponent("test-python"), moduleRoot: fixture.root)
        let model = StudioModel(runtime: runtime, autoRefresh: false)
        model.snapshot = snapshot(grade: "grade2", root: fixture.root.path)
        model.requestRun(.previewDuplicates)
        model.confirmRun()
        try await waitUntil { !model.isRunning && !model.isLoading }
        XCTAssertEqual(model.logs.filter { $0.status == "complete" && $0.detail == "Saved" }.count, 1)
        XCTAssertEqual(model.logs.last?.artifacts, ["final": "output"])
        XCTAssertTrue(model.logs.contains { $0.phase == "write" && $0.detail == "Saved" && $0.status == "running" })
        XCTAssertTrue(model.logs.contains { $0.detail == "Recovered source failure" && $0.error == "read failed" })
    }

    @MainActor
    private func waitUntil(_ predicate: () -> Bool) async throws {
        for _ in 0..<250 {
            if predicate() { return }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        throw StudioFailure("The native test timed out waiting for a subprocess state change.")
    }

    private var completedPacket: String {
        #"{"type":"result","data":{"exit_code":0,"status":"completed","detail":"Done","artifacts":{},"error":""}}"#
    }

    private func temporaryDirectory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("wordbyword-model-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func runtimeBundle(in root: URL, configuration: [String: String]) throws -> Bundle {
        let url = root.appendingPathComponent("RuntimeFixture.bundle")
        let contents = url.appendingPathComponent("Contents")
        let resources = contents.appendingPathComponent("Resources")
        try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        let info = ["CFBundleIdentifier": "com.wordbyword.tests.\(UUID().uuidString)",
                    "CFBundlePackageType": "BNDL", "CFBundleName": "RuntimeFixture"]
        try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
            .write(to: contents.appendingPathComponent("Info.plist"))
        try JSONSerialization.data(withJSONObject: configuration).write(to: resources.appendingPathComponent("runtime.json"))
        return try XCTUnwrap(Bundle(url: url))
    }

    private func bridgeFixture(_ source: String, environment: [String: String] = [:]) throws -> (root: URL, client: BridgeClient) {
        let root = try temporaryDirectory()
        let executable = root.appendingPathComponent("test-python")
        try ("#!/usr/bin/python3\n" + source + "\n").write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
        let launchEnvironment = ProcessInfo.processInfo.environment.merging(environment) { _, replacement in replacement }
        return (root, BridgeClient(python: executable, moduleRoot: root, workspace: root, environment: launchEnvironment))
    }
}

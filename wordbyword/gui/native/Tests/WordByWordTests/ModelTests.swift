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

    private func snapshot(grade: String = "grade3", legacy: Bool = false, root: String = "/workspace",
                          report: String = "/workspace/archive_root/grade3/lesson.docx") -> WorkspaceSnapshot {
        let canonical = Dictionary(uniqueKeysWithValues: pathKeys.map { ($0, "\(root)/\($0)/\(grade)") })
        let browsing = Dictionary(uniqueKeysWithValues: pathKeys.map { ($0, "/legacy/\($0)") })
        return WorkspaceSnapshot(workspace: root, grade: grade, gradeLabel: "3학년", legacyLayout: legacy,
                                 warnings: [], paths: browsing, runPaths: canonical, counts: [:],
                                 files: ["archive": [StudioFile(id: report, name: "lesson.docx", path: report,
                                                                kind: "DOCX", size: 12, modified: "")]],
                                 artifacts: [], summary: [:])
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
        for action in StudioAction.allCases {
            let flags = try StudioCommandBuilder.arguments(for: action, snapshot: source,
                                                           settings: StudioSettings(), selectedReport: source.files["archive"]![0].path)
            XCTAssertTrue(flags.contains(try XCTUnwrap(commands[action])), "\(action)")
            XCTAssertFalse(flags.contains(where: { $0.hasPrefix("/legacy/") }), "\(action)")
            if action != .migrateWorkspace {
                XCTAssertEqual(Array(flags.prefix(2)), ["--target-grade", "grade3"], "\(action)")
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
                                                           selectedReport: "/workspace/archive_root/grade3/lesson.docx")
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
        for report in ["", "/outside/lesson.docx", "/workspace/archive_root/grade3/lesson.txt"] {
            XCTAssertThrowsError(try StudioCommandBuilder.arguments(for: .generateReport, snapshot: snapshot(),
                                                                     settings: StudioSettings(), selectedReport: report))
        }
        XCTAssertThrowsError(try StudioCommandBuilder.arguments(for: .generateReport,
                                                                 snapshot: snapshot(report: "/workspace/archive_root/grade3/lesson.txt"),
                                                                 settings: StudioSettings(), selectedReport: "/workspace/archive_root/grade3/lesson.txt"))
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
        try settings.write(to: root)
        XCTAssertEqual(try StudioSettings.read(from: root), settings)
        settings.model = "second-model"
        try settings.write(to: root)
        XCTAssertEqual(try StudioSettings.read(from: root), settings)
        let contents = try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent(".wordbyword").path)
        XCTAssertEqual(contents, ["studio-settings.json"])
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
        model.grade = "grade3"
        model.snapshot = snapshot()
        XCTAssertThrowsError(try model.arguments(for: .migrateWorkspace))
        model.isRunning = true
        model.changeGrade("grade4")
        model.settings.model = "changed"
        model.requestRun(.buildDictionary)
        XCTAssertEqual(model.grade, "grade3")
        XCTAssertEqual(model.settings.model, "gemma4:31b")
        XCTAssertNil(model.pendingAction)
        model.isRunning = false
        model.requestRun(.buildDictionary)
        XCTAssertEqual(model.pendingAction, .buildDictionary)
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
    func testStaleSnapshotCannotReplaceNewGrade() async throws {
        let script = """
        import json, os, signal, sys, time
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        request = json.load(sys.stdin)
        grade = request['grade']
        if grade == 'grade1':
            open(os.path.join(sys.argv[-1], 'first-started'), 'w').close()
            time.sleep(0.9)
        print(json.dumps({"type":"snapshot", "data":{"workspace":sys.argv[-1], "grade":grade, "grade_label":grade, "legacy_layout":False, "warnings":[], "paths":{}, "run_paths":{}, "counts":{}, "files":{}, "artifacts":[], "summary":{}}}), flush=True)
        """
        let fixture = try bridgeFixture(script)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = StudioRuntime(workspace: fixture.root, python: fixture.root.appendingPathComponent("test-python"), moduleRoot: fixture.root)
        let model = StudioModel(runtime: runtime, autoRefresh: false)
        model.refresh()
        try await waitUntil { FileManager.default.fileExists(atPath: fixture.root.appendingPathComponent("first-started").path) }
        model.changeGrade("grade2")
        try await waitUntil { !model.isLoading && model.snapshot?.grade == "grade2" }
        try await Task.sleep(nanoseconds: 1_000_000_000)
        XCTAssertEqual(model.grade, "grade2")
        XCTAssertEqual(model.snapshot?.grade, "grade2")
        XCTAssertNil(model.error)
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
        model.snapshot = snapshot(grade: "grade1", root: fixture.root.path)
        model.requestRun(.previewDuplicates)
        model.confirmRun()
        try await waitUntil { model.logs.contains(where: { $0.phase == "ready" }) }
        model.cancelRun()
        XCTAssertTrue(model.isRunning)
        XCTAssertTrue(model.isCancelling)
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertTrue(model.isRunning)
        try await waitUntil { !model.isRunning && !model.isLoading }
        XCTAssertEqual(model.lastResult?.status, "cancelled")
        XCTAssertFalse(model.isCancelling)
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
        model.snapshot = snapshot(grade: "grade1", root: fixture.root.path)
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

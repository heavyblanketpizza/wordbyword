import Foundation
import Darwin

struct StudioRuntime: Sendable {
    let workspace: URL
    let python: URL
    let moduleRoot: URL

    static func resolve(arguments: [String] = ProcessInfo.processInfo.arguments,
                        bundle: Bundle = .main,
                        currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)) -> StudioRuntime {
        struct Configuration: Decodable {
            let python: String
            let workspace: String
            let moduleRoot: String?
            enum CodingKeys: String, CodingKey {
                case python, workspace
                case moduleRoot = "module_root"
            }
        }
        let configuration = bundle.url(forResource: "runtime", withExtension: "json")
            .flatMap { try? Data(contentsOf: $0) }
            .flatMap { try? JSONDecoder().decode(Configuration.self, from: $0) }
        func option(_ name: String) -> String? {
            guard let index = arguments.firstIndex(of: name), arguments.indices.contains(index + 1),
                  !arguments[index + 1].hasPrefix("--") else { return nil }
            return arguments[index + 1]
        }
        let workspace = URL(fileURLWithPath: option("--workspace") ?? configuration?.workspace ?? currentDirectory.path,
                            relativeTo: currentDirectory).standardizedFileURL
        // Keep the venv's bin/python path: resolving its symlink drops the virtual environment.
        let python = URL(fileURLWithPath: option("--python") ?? configuration?.python
                         ?? workspace.appendingPathComponent(".venv/bin/python").path,
                         relativeTo: currentDirectory).standardizedFileURL
        let moduleRoot = URL(fileURLWithPath: option("--module-root") ?? configuration?.moduleRoot
                             ?? configuration?.workspace ?? workspace.path,
                             relativeTo: currentDirectory).standardizedFileURL
        return StudioRuntime(workspace: workspace, python: python, moduleRoot: moduleRoot)
    }
}

struct BridgeRequest: Encodable, Sendable {
    let operation: String
    var grade: String? = nil
    var arguments: [String]? = nil
    var path: String? = nil
    var destination: String? = nil
    var files: [String]? = nil

    var terminalType: String {
        switch operation {
        case "snapshot": return "snapshot"
        case "distribution": return "distribution"
        case "preview", "migration_plan": return "preview"
        default: return "result"
        }
    }
}

enum BridgePacket: Decodable, Sendable {
    case snapshot(WorkspaceSnapshot)
    case event(CommandEvent)
    case result(RunResult)
    case preview(PreviewDocument)
    case distribution(DictionaryDistribution)
    case error(String)

    private enum CodingKeys: String, CodingKey { case type, data, message }
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        switch type {
        case "snapshot": self = .snapshot(try container.decode(WorkspaceSnapshot.self, forKey: .data))
        case "event": self = .event(try container.decode(CommandEvent.self, forKey: .data))
        case "result": self = .result(try container.decode(RunResult.self, forKey: .data))
        case "preview": self = .preview(try container.decode(PreviewDocument.self, forKey: .data))
        case "distribution": self = .distribution(try container.decode(DictionaryDistribution.self, forKey: .data))
        case "error": self = .error(try container.decode(String.self, forKey: .message))
        default: throw StudioFailure("The Python bridge returned an unknown packet type: \(type).")
        }
    }

    var type: String {
        switch self {
        case .snapshot: return "snapshot"
        case .event: return "event"
        case .result: return "result"
        case .preview: return "preview"
        case .distribution: return "distribution"
        case .error: return "error"
        }
    }
}

/// One subprocess per operation. Pipe reads, JSON decoding, and process waits never run on the UI thread.
final class BridgeClient: @unchecked Sendable {
    private let python: URL
    private let moduleRoot: URL
    private let workspace: URL
    private let environment: [String: String]
    private let lock = NSLock()
    private var process: Process?
    private var launched = false
    private var started = false
    private var cancellationRequested = false

    init(python: URL, moduleRoot: URL, workspace: URL,
         environment: [String: String] = ProcessInfo.processInfo.environment) {
        self.python = python
        self.moduleRoot = moduleRoot
        self.workspace = workspace
        self.environment = environment
    }

    func packets(for request: BridgeRequest) -> AsyncThrowingStream<BridgePacket, Error> {
        AsyncThrowingStream(bufferingPolicy: .bufferingNewest(1_024)) { continuation in
            lock.lock()
            let wasStarted = started
            started = true
            lock.unlock()
            guard !wasStarted else {
                continuation.finish(throwing: StudioFailure("This bridge operation has already started."))
                return
            }
            continuation.onTermination = { [weak self] reason in
                if case .cancelled = reason { self?.cancel() }
            }
            DispatchQueue.global(qos: .userInitiated).async { [self] in
                run(request, continuation: continuation)
            }
        }
    }

    /// Requests SIGINT only. The stream remains open until the process exits and both pipes drain.
    func cancel() {
        lock.lock()
        cancellationRequested = true
        if launched, let process, process.isRunning { process.interrupt() }
        lock.unlock()
    }

    private func run(_ request: BridgeRequest,
                     continuation: AsyncThrowingStream<BridgePacket, Error>.Continuation) {
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            continuation.finish(throwing: StudioFailure("Python is unavailable at \(python.path). Relaunch WordByWord with the project's installed environment."))
            return
        }
        let process = Process()
        let input = Pipe()
        let output = Pipe()
        let errors = Pipe()
        process.executableURL = python
        process.arguments = ["-B", "-u", "-m", "wordbyword.gui.bridge", "--workspace", workspace.path]
        process.currentDirectoryURL = moduleRoot
        var environment = self.environment
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["NO_COLOR"] = "1"
        environment["TERM"] = "dumb"
        // The application code remains fixed when the user switches data workspaces.
        environment["PYTHONPATH"] = moduleRoot.path + (environment["PYTHONPATH"].map { ":" + $0 } ?? "")
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        defer {
            try? input.fileHandleForWriting.close()
            try? input.fileHandleForReading.close()
            try? output.fileHandleForReading.close()
            try? output.fileHandleForWriting.close()
            try? errors.fileHandleForReading.close()
            try? errors.fileHandleForWriting.close()
            lock.lock()
            self.process = nil
            launched = false
            lock.unlock()
        }

        do {
            var body = try JSONEncoder().encode(request)
            body.append(0x0A)
            lock.lock()
            self.process = process
            lock.unlock()
            try process.run()
            lock.lock()
            launched = true
            if cancellationRequested, process.isRunning { process.interrupt() }
            lock.unlock()
            try? output.fileHandleForWriting.close()
            try? errors.fileHandleForWriting.close()
            try? input.fileHandleForReading.close()

            let stderr = BoundedBytes(limit: 32_768)
            let stderrGroup = DispatchGroup()
            stderrGroup.enter()
            DispatchQueue.global(qos: .utility).async {
                defer { stderrGroup.leave() }
                while let data = try? Self.readChunk(from: errors.fileHandleForReading, count: 4_096) {
                    stderr.append(data)
                }
            }

            var protocolFailure: String?
            do {
                try input.fileHandleForWriting.write(contentsOf: body)
                try input.fileHandleForWriting.close()
            } catch {
                protocolFailure = "The request could not be delivered to Python: \(error.localizedDescription)"
                cancel()
            }

            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            var buffer = Data()
            var terminalCount = 0
            var resultExitCode: Int?
            var reportedError: String?
            let maximumLineBytes = 16 * 1_024 * 1_024
            func consume(_ line: Data) {
                guard protocolFailure == nil else { return }
                guard !line.allSatisfy({ $0 == 0x20 || $0 == 0x09 || $0 == 0x0D }) else { return }
                do {
                    guard line.count <= maximumLineBytes else {
                        throw StudioFailure("The Python bridge response exceeded the safe size limit.")
                    }
                    let packet = try decoder.decode(BridgePacket.self, from: line)
                    if case .error(let message) = packet {
                        reportedError = String(message.prefix(8_192))
                        return
                    }
                    if packet.type != "event" {
                        guard packet.type == request.terminalType else {
                            throw StudioFailure("The Python bridge returned \(packet.type) for a \(request.operation) request.")
                        }
                        terminalCount += 1
                        guard terminalCount == 1 else {
                            throw StudioFailure("The Python bridge returned more than one completion response.")
                        }
                    }
                    if case .result(let result) = packet { resultExitCode = result.exitCode }
                    continuation.yield(packet)
                } catch {
                    protocolFailure = "The Python bridge sent an invalid response: \(error.localizedDescription)"
                    cancel()
                }
            }
            do {
                while let data = try Self.readChunk(from: output.fileHandleForReading, count: 16_384) {
                    if protocolFailure != nil { continue } // Drain even after malformed output.
                    buffer.append(data)
                    while let newline = buffer.firstIndex(of: 0x0A) {
                        let line = Data(buffer[..<newline])
                        buffer.removeSubrange(...newline)
                        consume(line)
                    }
                    if buffer.count > maximumLineBytes {
                        protocolFailure = "The Python bridge response exceeded the safe size limit."
                        buffer.removeAll(keepingCapacity: false)
                        cancel()
                    }
                }
                if !buffer.isEmpty { consume(buffer) }
            } catch {
                protocolFailure = "The Python output could not be read: \(error.localizedDescription)"
                cancel()
            }
            process.waitUntilExit()
            stderrGroup.wait()
            // Completion is delivered only after all stdout packets and stderr bytes are consumed.
            if let protocolFailure { throw StudioFailure(protocolFailure) }
            if let reportedError { throw StudioFailure(reportedError) }
            let status = Int(process.terminationStatus)
            let diagnostic = String(StudioDiagnostics.redacted(stderr.string, environment: environment).suffix(2_000))
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let suffix = diagnostic.isEmpty ? "" : "\n\(diagnostic)"
            guard terminalCount == 1 else {
                throw StudioFailure("Python stopped with status \(status) without a completion response.\(suffix)")
            }
            guard status == 0 || (resultExitCode != nil && resultExitCode != 0) else {
                throw StudioFailure("Python exited unexpectedly with status \(status).\(suffix)")
            }
            continuation.finish()
        } catch {
            continuation.finish(throwing: StudioFailure(StudioDiagnostics.redacted(error.localizedDescription,
                                                                                   environment: environment)))
        }
    }

    // FileHandle.read(upToCount:) can wait to fill its buffer on macOS. A single
    // POSIX read returns each available pipe chunk so progress arrives while a
    // long-lived Python operation is still running.
    private static func readChunk(from handle: FileHandle, count: Int) throws -> Data? {
        var bytes = [UInt8](repeating: 0, count: count)
        while true {
            let received = bytes.withUnsafeMutableBytes {
                Darwin.read(handle.fileDescriptor, $0.baseAddress, count)
            }
            if received > 0 { return Data(bytes.prefix(received)) }
            if received == 0 { return nil }
            if errno == EINTR { continue }
            throw StudioFailure("A process pipe could not be read: \(String(cString: strerror(errno)))")
        }
    }
}

/// Native libraries and interpreter startup can bypass the Python packet redactor.
/// Sanitize their diagnostics before displaying errors or adding them to the copyable journal.
enum StudioDiagnostics {
    static func redacted(_ text: String, environment: [String: String]) -> String {
        var value = text
        let secrets = environment.filter { name, secret in
            secret.count >= 4 && name.range(of: "(?i)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)",
                                            options: .regularExpression) != nil
        }.map(\.value).sorted { $0.count > $1.count }
        for secret in secrets { value = value.replacingOccurrences(of: secret, with: "[redacted]") }

        func replace(_ pattern: String, with replacement: String) {
            guard let expression = try? NSRegularExpression(pattern: pattern) else { return }
            value = expression.stringByReplacingMatches(in: value,
                                                         range: NSRange(value.startIndex..., in: value),
                                                         withTemplate: replacement)
        }
        replace(#"(?i)(https?://)[^/\s@]+@"#, with: "$1[redacted]@")
        replace(#"(?i)((?:proxy[-_])?authorization[\"']?\s*[:=]\s*[\"']?)[^\r\n\"',;}]+"#,
                with: "$1[redacted]")
        replace(#"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+"#, with: "$1 [redacted]")
        replace(#"(?i)([?&](?:api[_-]?key|access[_-]?token|token|key|password|secret|auth(?:orization)?)=)[^&#\s\"']+"#,
                with: "$1[redacted]")
        replace(#"(?i)((?:api[_-]?key|access[_-]?token|password|passwd|secret|credential)[\"']?\s*[:=]\s*[\"']?)[^\s,;&\"'<>]+"#,
                with: "$1[redacted]")
        return value
    }
}

private final class BoundedBytes: @unchecked Sendable {
    private let lock = NSLock()
    private var bytes = Data()
    private var truncated = false
    private let limit: Int
    init(limit: Int) { self.limit = limit }
    func append(_ data: Data) {
        lock.lock()
        bytes.append(data)
        if bytes.count > limit {
            bytes.removeFirst(bytes.count - limit)
            truncated = true
        }
        lock.unlock()
    }
    var string: String {
        lock.lock()
        defer { lock.unlock() }
        let text = String(decoding: bytes, as: UTF8.self)
        if truncated {
            // A secret can straddle the retained tail boundary. Discard that
            // partial first line instead of displaying an unmatchable fragment.
            guard let newline = text.firstIndex(of: "\n") else { return "" }
            return String(text[text.index(after: newline)...])
        }
        return text
    }
}

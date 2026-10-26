import Foundation
import Speech

let path = CommandLine.arguments.dropFirst().first ?? ""
if path.isEmpty {
    fputs("usage: transcribe file.wav\n", stderr)
    exit(1)
}
let url = URL(fileURLWithPath: path)

func fail(_ message: String, _ code: Int32) -> Never {
    fputs(message + "\n", stderr)
    exit(code)
}

func emit(_ result: SFSpeechRecognitionResult) {
    finished = true
    let rows: [[String: Any]] = result.transcriptions.prefix(5).map { transcription in
        let confidences = transcription.segments.map { $0.confidence }
        let row: [String: Any] = [
            "text": transcription.formattedString,
            "confidence": confidences.isEmpty
                ? NSNull()
                : confidences.reduce(0, +) / Float(confidences.count),
        ]
        return row
    }
    let payload: [String: Any] = ["transcripts": rows]
    guard let data = try? JSONSerialization.data(withJSONObject: payload) else {
        fail("could not encode transcript", 4)
    }
    FileHandle.standardOutput.write(data)
    if CommandLine.arguments.count > 2 {
        let out = URL(fileURLWithPath: CommandLine.arguments[2])
        try? data.write(to: out)
    }
    exit(0)
}

var task: SFSpeechRecognitionTask?
var finished = false

func armTimeout() {
    DispatchQueue.main.asyncAfter(deadline: .now() + 45) {
        if finished { return }
        task?.cancel()
        fail("timed out", 5)
    }
}

func recognize() {
    let status = SFSpeechRecognizer.authorizationStatus()
    if status == .notDetermined {
        SFSpeechRecognizer.requestAuthorization { _ in
            DispatchQueue.main.async { recognize() }
        }
        return
    }
    guard status == .authorized else {
        fail("speech recognition not authorized (\(status.rawValue))", 2)
    }
    guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
          recognizer.isAvailable
    else {
        fail("recognizer unavailable", 3)
    }
    let request = SFSpeechURLRecognitionRequest(url: url)
    request.shouldReportPartialResults = false
    if #available(macOS 13.0, *) {
        request.addsPunctuation = false
    }
    if CommandLine.arguments.count > 3 {
        let hintsURL = URL(fileURLWithPath: CommandLine.arguments[3])
        if let text = try? String(contentsOf: hintsURL, encoding: .utf8) {
            let hints = text.split(whereSeparator: \.isNewline).map(String.init).filter { !$0.isEmpty }
            if !hints.isEmpty {
                request.contextualStrings = hints
            }
        }
    }
    task = recognizer.recognitionTask(with: request) { result, error in
        if let result = result {
            guard result.isFinal else { return }
            emit(result)
        }
        if let error = error {
            fail(error.localizedDescription, 4)
        }
    }
}

DispatchQueue.main.async {
    armTimeout()
    recognize()
}
dispatchMain()

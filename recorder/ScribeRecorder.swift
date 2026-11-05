// ScribeRecorder: captures the microphone and system (or one app's) audio into
// two synced WAV files using ScreenCaptureKit. No virtual audio devices needed.
//
// Launched by the Python app as a subprocess.
//
//   scribe-recorder --out-dir <dir> --name <base> [--app <bundle id>]
//   scribe-recorder --list-apps
//
// --app records only that app's audio for the "them" track; without it, all
// system audio is recorded. --list-apps prints one "APP <bundle id>\t<name>"
// line per running regular app and exits (no permission needed).
//
// stdin  (one command per line): pause | resume | stop
//        EOF on stdin is treated as "stop", so a crashed parent never leaves
//        this process recording in the background.
// stdout (one status per line):  READY | RECORDING | PAUSED
//                                LEVEL <me 0-1> <them 0-1>
//                                SAVED <me_path> <them_path>
//                                ERROR <message>
// exit codes: 0 saved, 1 other error, 2 permission missing, 3 app not running

import AppKit
import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

setvbuf(stdout, nil, _IOLBF, 0)

func emit(_ line: String) {
    print(line)
    fflush(stdout)
}

func fail(_ message: String, code: Int32) -> Never {
    emit("ERROR \(message)")
    exit(code)
}

// MARK: - One output track (one WAV file)

/// Writes one track and keeps it on the shared session timeline: gaps (e.g. no
/// buffers while nobody is talking) are filled with silence so both files stay
/// the same length and aligned sample-for-sample.
final class Track {
    let url: URL
    private var file: AVAudioFile?
    private var format: AVAudioFormat?
    private(set) var framesWritten: AVAudioFramePosition = 0
    private(set) var peak: Float = 0

    init(url: URL) { self.url = url }

    func write(_ buffer: AVAudioPCMBuffer, atSeconds t: Double) throws {
        if file == nil {
            format = buffer.format
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: buffer.format.sampleRate,
                AVNumberOfChannelsKey: buffer.format.channelCount,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false,
            ]
            file = try AVAudioFile(forWriting: url, settings: settings,
                                   commonFormat: buffer.format.commonFormat,
                                   interleaved: buffer.format.isInterleaved)
        }
        guard let file, let format, buffer.format == format else { return }

        // Pad with silence if this buffer starts later than where the file ends.
        let expected = AVAudioFramePosition(max(0, t) * format.sampleRate)
        let gap = expected - framesWritten
        if gap > AVAudioFramePosition(format.sampleRate * 0.05) {
            try writeSilence(frames: gap, to: file, format: format)
        }
        try file.write(from: buffer)
        framesWritten += AVAudioFramePosition(buffer.frameLength)
        peak = max(peak, Track.peak(of: buffer))
    }

    /// Pads the end so both tracks finish at the same time.
    func padTo(seconds: Double) throws {
        guard let file, let format else { return }
        let gap = AVAudioFramePosition(seconds * format.sampleRate) - framesWritten
        if gap > 0 { try writeSilence(frames: gap, to: file, format: format) }
    }

    func takePeak() -> Float { defer { peak = 0 }; return peak }

    func close() { file = nil }  // AVAudioFile finalizes the WAV header on release

    private func writeSilence(frames: AVAudioFramePosition, to file: AVAudioFile,
                              format: AVAudioFormat) throws {
        var remaining = frames
        let chunk: AVAudioFrameCount = 4096
        guard let silence = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: chunk) else { return }
        while remaining > 0 {
            let n = AVAudioFrameCount(min(AVAudioFramePosition(chunk), remaining))
            silence.frameLength = n
            for b in UnsafeMutableAudioBufferListPointer(silence.mutableAudioBufferList) {
                if let data = b.mData { memset(data, 0, Int(b.mDataByteSize)) }
            }
            try file.write(from: silence)
            remaining -= AVAudioFramePosition(n)
            framesWritten += AVAudioFramePosition(n)
        }
    }

    static func peak(of buffer: AVAudioPCMBuffer) -> Float {
        guard let channels = buffer.floatChannelData else { return 0 }
        var p: Float = 0
        for c in 0..<Int(buffer.format.channelCount) {
            for i in 0..<Int(buffer.frameLength) { p = max(p, abs(channels[c][i])) }
        }
        return p
    }
}

func pcmBuffer(from sampleBuffer: CMSampleBuffer) -> AVAudioPCMBuffer? {
    guard let desc = CMSampleBufferGetFormatDescription(sampleBuffer),
          let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc),
          let format = AVAudioFormat(streamDescription: asbd) else { return nil }
    let frames = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
    guard frames > 0,
          let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { return nil }
    buffer.frameLength = frames
    let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
        sampleBuffer, at: 0, frameCount: Int32(frames), into: buffer.mutableAudioBufferList)
    return status == noErr ? buffer : nil
}

func hostSeconds() -> Double {
    CMClockGetTime(CMClockGetHostTimeClock()).seconds
}

// MARK: - Recorder

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let me: Track
    let them: Track
    let appBundleID: String?
    private var stream: SCStream?
    private let queue = DispatchQueue(label: "scribeiq.audio")

    // Timeline state, only touched on `queue`.
    private var startTime: Double?          // host-clock seconds of the first buffer
    private var pausedTotal: Double = 0
    private var pausedAt: Double?
    private var stopped = false

    init(outDir: URL, name: String, appBundleID: String?) {
        me = Track(url: outDir.appendingPathComponent("\(name)_me.wav"))
        them = Track(url: outDir.appendingPathComponent("\(name)_them.wav"))
        self.appBundleID = appBundleID
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else { fail("No display found", code: 1) }

        let filter: SCContentFilter
        if let bundleID = appBundleID {
            let apps = content.applications.filter { $0.bundleIdentifier == bundleID }
            if apps.isEmpty { fail("\(appName(bundleID)) is not running", code: 3) }
            filter = SCContentFilter(display: display, including: apps, exceptingWindows: [])
        } else {
            filter = SCContentFilter(display: display, excludingApplications: [],
                                     exceptingWindows: [])
        }

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.captureMicrophone = true
        config.sampleRate = 48_000
        config.channelCount = 1
        // Video is required by the API but unused; keep it as cheap as possible.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        try await stream.startCapture()
        self.stream = stream
    }

    func pause() {
        queue.async {
            guard self.pausedAt == nil, !self.stopped else { return }
            self.pausedAt = hostSeconds()
            emit("PAUSED")
        }
    }

    func resume() {
        queue.async {
            guard let p = self.pausedAt, !self.stopped else { return }
            self.pausedTotal += hostSeconds() - p
            self.pausedAt = nil
            emit("RECORDING")
        }
    }

    func stop() async {
        try? await stream?.stopCapture()
        queue.sync {
            stopped = true
            if let start = startTime {
                let end = (pausedAt ?? hostSeconds()) - start - pausedTotal
                try? me.padTo(seconds: end)
                try? them.padTo(seconds: end)
            }
            me.close()
            them.close()
        }
    }

    func reportLevels() {
        queue.async {
            guard !self.stopped else { return }
            emit(String(format: "LEVEL %.3f %.3f", self.me.takePeak(), self.them.takePeak()))
        }
    }

    // SCStreamOutput
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard !stopped, pausedAt == nil, sampleBuffer.isValid else { return }
        let track: Track
        switch type {
        case .audio: track = them
        case .microphone: track = me
        default: return
        }
        let pts = sampleBuffer.presentationTimeStamp.seconds
        if startTime == nil { startTime = pts }
        // Buffers that straddle a pause boundary land slightly early; harmless.
        let t = pts - (startTime ?? pts) - pausedTotal
        guard let buffer = pcmBuffer(from: sampleBuffer) else { return }
        do {
            try track.write(buffer, atSeconds: t)
        } catch {
            emit("ERROR Could not write \(track.url.lastPathComponent): \(error.localizedDescription)")
        }
    }

    // SCStreamDelegate
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        emit("ERROR Capture stopped: \(error.localizedDescription)")
        Task { await self.stop(); exit(1) }
    }
}

// MARK: - Arguments and permissions

var outDir = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
var baseName = ISO8601DateFormatter().string(from: Date())
var appBundleID: String?
var listApps = false
var args = CommandLine.arguments.dropFirst().makeIterator()
while let arg = args.next() {
    switch arg {
    case "--out-dir": if let v = args.next() { outDir = URL(fileURLWithPath: v) }
    case "--name": if let v = args.next() { baseName = v }
    case "--app":
        guard let v = args.next(), !v.isEmpty else { fail("--app needs a bundle id", code: 1) }
        appBundleID = v
    case "--list-apps": listApps = true
    default: fail("Unknown argument \(arg)", code: 1)
    }
}

/// Running apps that show in the Dock. NSWorkspace needs no permission.
func runningRegularApps() -> [NSRunningApplication] {
    NSWorkspace.shared.runningApplications.filter {
        $0.activationPolicy == .regular && $0.bundleIdentifier != nil
    }
}

func appName(_ bundleID: String) -> String {
    runningRegularApps().first { $0.bundleIdentifier == bundleID }?.localizedName ?? bundleID
}

if listApps {
    for app in runningRegularApps() {
        emit("APP \(app.bundleIdentifier!)\t\(app.localizedName ?? app.bundleIdentifier!)")
    }
    exit(0)
}

// Check the app before creating anything, so a typo leaves no empty files.
if let bundleID = appBundleID,
   !NSWorkspace.shared.runningApplications.contains(where: { $0.bundleIdentifier == bundleID }) {
    fail("\(appName(bundleID)) is not running", code: 3)
}

try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

if !CGPreflightScreenCaptureAccess() {
    CGRequestScreenCaptureAccess()
    fail("Screen Recording permission is needed to capture call audio. Enable it in "
         + "System Settings > Privacy & Security > Screen & System Audio Recording, "
         + "then restart the app that launched ScribeIQ.", code: 2)
}

func microphoneAllowed() async -> Bool {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: return true
    case .notDetermined: return await AVCaptureDevice.requestAccess(for: .audio)
    default: return false
    }
}

// MARK: - Main

let recorder = Recorder(outDir: outDir, name: baseName, appBundleID: appBundleID)
let finishing = NSLock()
var didFinish = false
var levelTimer: DispatchSourceTimer?

func finish() {
    finishing.lock()
    if didFinish { finishing.unlock(); return }
    didFinish = true
    finishing.unlock()
    Task {
        await recorder.stop()
        emit("SAVED \(recorder.me.url.path) \(recorder.them.url.path)")
        exit(0)
    }
}

signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)
let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
sigint.setEventHandler { finish() }
sigint.resume()
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigterm.setEventHandler { finish() }
sigterm.resume()

Task {
    guard await microphoneAllowed() else {
        fail("Microphone permission is needed. Enable it in System Settings > "
             + "Privacy & Security > Microphone.", code: 2)
    }
    do {
        try await recorder.start()
    } catch {
        let ns = error as NSError
        let denied = ns.domain == SCStreamErrorDomain
            && ns.code == SCStreamError.userDeclined.rawValue
        fail("Could not start capture: \(error.localizedDescription)", code: denied ? 2 : 1)
    }
    emit("READY")
    emit("RECORDING")

    let timer = DispatchSource.makeTimerSource(queue: .main)
    timer.schedule(deadline: .now() + 0.2, repeating: 0.2)
    timer.setEventHandler { recorder.reportLevels() }
    timer.resume()
    levelTimer = timer

    // Read commands until "stop" or EOF.
    Thread.detachNewThread {
        while let line = readLine() {
            switch line.trimmingCharacters(in: .whitespaces).lowercased() {
            case "pause": recorder.pause()
            case "resume": recorder.resume()
            case "stop": finish(); return
            case "": continue
            default: emit("ERROR Unknown command \(line)")
            }
        }
        finish()  // EOF: parent went away
    }
}

dispatchMain()

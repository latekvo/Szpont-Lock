import AVFoundation
import CoreMedia
import Foundation

/// Records a short clip from the webcam. The session is torn down as soon as the file is
/// finalised, so the camera indicator is lit only for the length of the recording.
///
/// Frames are written through an `AVAssetWriter` rather than `AVCaptureMovieFileOutput`,
/// because the clip has to be a specific length and the file output gives no dependable
/// handle on that: `recordedDuration` counts from the `startRecording` call rather than
/// from the first frame written, and the gap between them shifts with how warm the camera
/// already was - measured anywhere from 0.5s to 1.1s on one machine, i.e. a 5s request
/// landing anywhere between 4.2s and 5.5s. Deciding from presentation timestamps instead
/// makes the length exact and independent of pipeline warm-up.
///
/// Video only - adding audio would mean a Microphone TCC prompt that was never asked for.
enum RecordingOutcome {
    case saved(URL)
    case failed
    /// Aborted because the machine was unlocked before the clip finished. The partial
    /// file is deleted rather than left behind: whoever unlocked it was the owner, so
    /// the footage is of them answering their own trap.
    case discarded
}

final class CameraRecorder: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private var session: AVCaptureSession?
    private var writer: AVAssetWriter?
    private var writerInput: AVAssetWriterInput?
    private var firstPresentationTime: CMTime?
    private var destination: URL?
    private var targetDuration: TimeInterval = 0
    private var completion: ((RecordingOutcome) -> Void)?
    private var isStopping = false
    private var finished = false
    private let queue = DispatchQueue(label: "com.szpont.lock.camera")

    static func authorizationStatus() -> AVAuthorizationStatus {
        AVCaptureDevice.authorizationStatus(for: .video)
    }

    static func requestAccess(_ completion: @escaping (Bool) -> Void) {
        AVCaptureDevice.requestAccess(for: .video) { granted in
            DispatchQueue.main.async { completion(granted) }
        }
    }

    /// Records `duration` seconds into `directory`. `completion` runs on the main queue.
    func record(duration: TimeInterval, into directory: URL, completion: @escaping (RecordingOutcome) -> Void) {
        self.completion = completion
        targetDuration = duration
        isStopping = false
        finished = false

        queue.async { [weak self] in
            guard let self else { return }
            guard let device = AVCaptureDevice.default(for: .video),
                  let input = try? AVCaptureDeviceInput(device: device) else {
                self.finish(.failed)
                return
            }

            let session = AVCaptureSession()
            session.beginConfiguration()
            session.sessionPreset = .high

            let videoOutput = AVCaptureVideoDataOutput()
            videoOutput.alwaysDiscardsLateVideoFrames = true
            videoOutput.videoSettings = [
                kCVPixelBufferPixelFormatTypeKey as String:
                    Int(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)
            ]
            videoOutput.setSampleBufferDelegate(self, queue: self.queue)

            guard session.canAddInput(input), session.canAddOutput(videoOutput) else {
                session.commitConfiguration()
                self.finish(.failed)
                return
            }
            session.addInput(input)
            session.addOutput(videoOutput)
            session.commitConfiguration()

            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd_HH-mm-ss"
            let url = directory.appendingPathComponent("szpontlock_\(formatter.string(from: Date())).mov")
            try? FileManager.default.removeItem(at: url)

            self.session = session
            self.destination = url
            session.startRunning()

            // If the pipeline never delivers, do not hold the camera open forever.
            self.queue.asyncAfter(deadline: .now() + duration + 12.0) { self.finish(.failed) }
        }
    }

    func captureOutput(_ output: AVCaptureOutput,
                       didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard !isStopping, !finished, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)

        if writer == nil {
            guard prepareWriter(for: sampleBuffer) else {
                finish(.failed)
                return
            }
            writer?.startWriting()
            writer?.startSession(atSourceTime: presentationTime)
            firstPresentationTime = presentationTime
        }

        guard let writer, let input = writerInput, let start = firstPresentationTime,
              writer.status == .writing else {
            finish(.failed)
            return
        }

        if input.isReadyForMoreMediaData {
            input.append(sampleBuffer)
        }

        guard CMTimeGetSeconds(CMTimeSubtract(presentationTime, start)) >= targetDuration else { return }
        isStopping = true
        input.markAsFinished()
        let url = destination
        writer.finishWriting { [weak self] in
            guard let self else { return }
            self.queue.async {
                guard writer.status == .completed, let url else {
                    self.finish(.failed)
                    return
                }
                self.finish(.saved(url))
            }
        }
    }

    /// Aborts an in-flight recording and deletes the partial file.
    ///
    /// No-op once the clip has already been finalised: a complete recording is kept even
    /// if the unlock follows a moment later, because the full window was captured.
    func cancel() {
        queue.async { [weak self] in
            guard let self, !self.finished else { return }
            let partial = self.destination
            self.writer?.cancelWriting()
            if let partial { try? FileManager.default.removeItem(at: partial) }
            self.finish(.discarded)
        }
    }

    private func prepareWriter(for sampleBuffer: CMSampleBuffer) -> Bool {
        guard let url = destination,
              let format = CMSampleBufferGetFormatDescription(sampleBuffer),
              let writer = try? AVAssetWriter(outputURL: url, fileType: .mov) else { return false }

        let dimensions = CMVideoFormatDescriptionGetDimensions(format)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: Int(dimensions.width),
            AVVideoHeightKey: Int(dimensions.height)
        ])
        input.expectsMediaDataInRealTime = true

        guard writer.canAdd(input) else { return false }
        writer.add(input)
        self.writer = writer
        self.writerInput = input
        return true
    }

    private func finish(_ outcome: RecordingOutcome) {
        guard !finished else { return }
        finished = true
        session?.stopRunning()
        session = nil
        writer = nil
        writerInput = nil
        firstPresentationTime = nil
        destination = nil
        let completion = self.completion
        self.completion = nil
        DispatchQueue.main.async { completion?(outcome) }
    }
}

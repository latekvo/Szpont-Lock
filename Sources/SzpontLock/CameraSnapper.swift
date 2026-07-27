import AVFoundation
import Foundation

/// One-shot webcam capture. The session is torn down as soon as the photo lands,
/// so the camera indicator is on only for the moment of the shot.
final class CameraSnapper: NSObject, AVCapturePhotoCaptureDelegate {
    private var session: AVCaptureSession?
    private var output: AVCapturePhotoOutput?
    private var completion: ((URL?) -> Void)?
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

    /// Captures a single frame into the captures directory. `completion` runs on the main queue.
    func snap(completion: @escaping (URL?) -> Void) {
        self.completion = completion
        finished = false

        queue.async { [weak self] in
            guard let self else { return }
            guard let device = AVCaptureDevice.default(for: .video),
                  let input = try? AVCaptureDeviceInput(device: device) else {
                self.finish(nil)
                return
            }

            let session = AVCaptureSession()
            session.beginConfiguration()
            session.sessionPreset = .photo
            guard session.canAddInput(input) else {
                session.commitConfiguration()
                self.finish(nil)
                return
            }
            session.addInput(input)

            let output = AVCapturePhotoOutput()
            guard session.canAddOutput(output) else {
                session.commitConfiguration()
                self.finish(nil)
                return
            }
            session.addOutput(output)
            session.commitConfiguration()

            self.session = session
            self.output = output
            session.startRunning()

            // Give auto-exposure a moment, otherwise the frame is a black rectangle.
            self.queue.asyncAfter(deadline: .now() + 0.7) {
                guard session.isRunning else {
                    self.finish(nil)
                    return
                }
                output.capturePhoto(with: AVCapturePhotoSettings(), delegate: self)
            }

            // Backstop: never leave the camera running if the delegate never fires.
            self.queue.asyncAfter(deadline: .now() + 8.0) { self.finish(nil) }
        }
    }

    func photoOutput(_ output: AVCapturePhotoOutput,
                     didFinishProcessingPhoto photo: AVCapturePhoto,
                     error: Error?) {
        guard error == nil, let data = photo.fileDataRepresentation() else {
            finish(nil)
            return
        }
        SecretStore.prepareDirectories()
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        let url = SecretStore.capturesDirectory
            .appendingPathComponent("intruder_\(formatter.string(from: Date())).jpg")
        do {
            try data.write(to: url, options: [.atomic])
            finish(url)
        } catch {
            finish(nil)
        }
    }

    private func finish(_ url: URL?) {
        guard !finished else { return }
        finished = true
        session?.stopRunning()
        session = nil
        output = nil
        let completion = self.completion
        self.completion = nil
        DispatchQueue.main.async { completion?(url) }
    }
}

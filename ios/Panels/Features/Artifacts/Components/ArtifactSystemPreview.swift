import SwiftUI
import QuickLook

struct ArtifactSystemPreview: UIViewControllerRepresentable {
    let document: ArtifactDocument
    func makeCoordinator() -> Coordinator { Coordinator(document: document) }
    func makeUIViewController(context: Context) -> QLPreviewController {
        let controller = QLPreviewController()
        controller.dataSource = context.coordinator
        return controller
    }
    func updateUIViewController(_ controller: QLPreviewController, context: Context) {}
    final class Coordinator: NSObject, QLPreviewControllerDataSource {
        let document: ArtifactDocument
        init(document: ArtifactDocument) { self.document = document }
        func numberOfPreviewItems(in controller: QLPreviewController) -> Int { 1 }
        func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> any QLPreviewItem { document.localURL as NSURL }
    }
}

struct ArtifactShareSheet: UIViewControllerRepresentable {
    let document: ArtifactDocument
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [document.localURL], applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

import SwiftUI

struct ArtifactViewerView: View {
    let owner: ArtifactOwner
    let path: String
    let title: String
    var body: some View { ScreenPlaceholder(title: title, symbol: "doc.richtext") }
}

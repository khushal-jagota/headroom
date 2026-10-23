#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
build=$(mktemp -d "${TMPDIR:-/tmp}/panels-offline-tests.XXXXXX")
trap 'rm -rf "$build"' EXIT
mkdir -p "$build/Sources/Panels" "$build/Tests/PanelsTests"
cp ios/Panels/Core/Networking/APIClient.swift ios/Panels/Core/Persistence/*.swift ios/Panels/Models/*.swift \
  ios/Panels/Features/Conversation/Models/*.swift ios/Panels/Features/Conversation/Networking/*.swift \
  ios/Panels/Features/Conversation/Logic/*.swift ios/Panels/Features/Conversation/State/ConversationModel.swift ios/Panels/Features/Review/ReviewAPI.swift \
  ios/Panels/Features/Review/ReviewDecision.swift ios/Panels/Features/Review/ReviewMessageDecision.swift "$build/Sources/Panels/"
cp ios/Panels/Features/Ticket/Networking/TicketAPI.swift ios/Panels/Features/Ticket/Models/TicketLifecycle.swift "$build/Sources/Panels/"
cp ios/PanelsTests/ReviewMessageTests.swift ios/PanelsTests/ReviewDecisionTests.swift ios/PanelsTests/OfflineTests.swift ios/PanelsTests/ConversationTests.swift "$build/Tests/PanelsTests/"
cp -R ios/PanelsTests/Fixtures "$build/Tests/PanelsTests/"
cat > "$build/Package.swift" <<'SWIFT'
// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "PanelsOfflineTests", platforms: [.macOS(.v14)], products: [.library(name: "Panels", targets: ["Panels"])], targets: [.target(name: "Panels"), .testTarget(name: "PanelsTests", dependencies: ["Panels"], resources: [.copy("Fixtures")])])
SWIFT
PANELS_STORE_DIRECTORY="$build/store" swift test --package-path "$build" --scratch-path "$build/.build" --jobs 4

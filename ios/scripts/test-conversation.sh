#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
build=$(mktemp -d "${TMPDIR:-/tmp}/panels-conversation-tests.XXXXXX")
trap 'rm -rf "$build"' EXIT
mkdir -p "$build/Sources/Panels" "$build/Tests/PanelsTests"
cp ios/Panels/Core/Networking/APIClient.swift ios/Panels/Models/*.swift \
  ios/Panels/Features/Conversation/Models/*.swift \
  ios/Panels/Features/Conversation/Networking/*.swift \
  ios/Panels/Features/Conversation/State/ConversationModel.swift "$build/Sources/Panels/"
cp ios/PanelsTests/ConversationTests.swift "$build/Tests/PanelsTests/"
cat > "$build/Package.swift" <<'SWIFT'
// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "PanelsConversationTests", platforms: [.macOS(.v14)], products: [.library(name: "Panels", targets: ["Panels"])], targets: [.target(name: "Panels"), .testTarget(name: "PanelsTests", dependencies: ["Panels"])])
SWIFT
swift test --package-path "$build" --scratch-path "$build/.build" --jobs 4

#!/bin/bash
# Run the Foundation-only More screen tests on macOS, without any simulator.
set -euo pipefail
cd "$(dirname "$0")/../.."
build=$(mktemp -d "${TMPDIR:-/tmp}/panels-system-tests.XXXXXX")
trap 'rm -rf "$build"' EXIT
mkdir -p "$build/Sources/Panels" "$build/Tests/PanelsTests"
cp ios/Panels/Core/Persistence/LocalStore.swift ios/Panels/Core/Networking/APIClient.swift ios/Panels/Models/*.swift ios/Panels/Features/Conversation/Models/ConversationContracts.swift \
  ios/Panels/Features/More/Usage/UsageModels.swift \
  ios/Panels/Features/More/VPS/VPSModels.swift \
  ios/Panels/Features/More/Settings/SettingsModels.swift "$build/Sources/Panels/"
cp ios/Panels/Features/Orchestrator/Logic/WorkerGroups.swift ios/Panels/Features/Workspace/Logic/WorkspaceGrouping.swift "$build/Sources/Panels/"
cp ios/PanelsTests/SyncMainTests.swift ios/PanelsTests/SystemScreenTests.swift "$build/Tests/PanelsTests/"
cat > "$build/Package.swift" <<'SWIFT'
// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "PanelsSystemTests", platforms: [.macOS(.v14)],
    products: [.library(name: "Panels", targets: ["Panels"])],
    targets: [.target(name: "Panels"), .testTarget(name: "PanelsTests", dependencies: ["Panels"])]
)
SWIFT
PANELS_STORE_DIRECTORY="$build/store" swift test --package-path "$build" --scratch-path "$build/.build" --jobs 4

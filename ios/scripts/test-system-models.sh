#!/bin/bash
# Run More models and shared-demand checks on macOS, without a simulator.
set -euo pipefail
cd "$(dirname "$0")/../.."
build=$(mktemp -d "${TMPDIR:-/tmp}/panels-system-tests.XXXXXX")
trap 'rm -rf "$build"' EXIT
mkdir -p "$build/Sources/Panels" "$build/Tests/PanelsTests"
cp ios/Panels/Core/Networking/ResourceRead.swift ios/Panels/Core/Networking/ResourceDemand.swift ios/Panels/Core/Networking/ResourceOwner.swift ios/Panels/Core/Networking/ResourceVersions.swift ios/Panels/Core/Networking/ChangeStream.swift ios/Panels/Core/Persistence/LocalStore.swift ios/Panels/Core/Persistence/ResponseCacheWriter.swift ios/Panels/Core/Networking/APIClient.swift ios/Panels/Models/*.swift ios/Panels/Features/Conversation/Models/ConversationContracts.swift ios/Panels/Core/Persistence/OutgoingStorage.swift ios/Panels/Core/Persistence/PendingWork.swift ios/Panels/Features/Review/ReviewAPI.swift \
  ios/Panels/Features/Ticket/Models/TicketLifecycle.swift ios/Panels/Features/More/Usage/UsageModels.swift \
  ios/Panels/Features/More/VPS/VPSModels.swift \
  ios/Panels/Features/More/Settings/SettingsModels.swift "$build/Sources/Panels/"
cp ios/Panels/Features/More/Ideas/Networking/IdeasAPI.swift ios/Panels/Features/More/Ideas/State/IdeasModel.swift \
  ios/Panels/Features/More/Feedback/FeedbackContracts.swift ios/Panels/Features/More/Feedback/Data/FeedbackAPI.swift ios/Panels/Features/More/Feedback/Data/FeedbackModel.swift "$build/Sources/Panels/"
cp ios/Panels/Features/Orchestrator/Logic/WorkerGroups.swift ios/Panels/Features/Workspace/Logic/WorkspaceGrouping.swift "$build/Sources/Panels/"
cp ios/PanelsTests/IdeasTests.swift ios/PanelsTests/SyncMainTests.swift ios/PanelsTests/SystemScreenTests.swift "$build/Tests/PanelsTests/"
cat > "$build/Package.swift" <<'SWIFT'
// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "PanelsSystemTests", platforms: [.macOS(.v15)],
    products: [.library(name: "Panels", targets: ["Panels"])],
    targets: [.target(name: "Panels"), .testTarget(name: "PanelsTests", dependencies: ["Panels"])]
)
SWIFT
PANELS_STORE_DIRECTORY="$build/store" swift test --package-path "$build" --scratch-path "$build/.build" --jobs 4 "$@"

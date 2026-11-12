#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
build=$(mktemp -d "${TMPDIR:-/tmp}/panels-offline-tests.XXXXXX")
trap 'rm -rf "$build"' EXIT
mkdir -p "$build/Sources/Panels" "$build/Tests/PanelsTests"
cp ios/Panels/Core/Networking/APIClient.swift ios/Panels/Core/Networking/ResourceVersions.swift ios/Panels/Core/Networking/ResourceOwner.swift ios/Panels/Core/Networking/ChangeStream.swift ios/Panels/Core/State/AppStore.swift ios/Panels/Core/Navigation/*.swift ios/Panels/Core/Persistence/*.swift ios/Panels/Models/*.swift \
  ios/Panels/Features/Conversation/Models/*.swift ios/Panels/Features/Conversation/Networking/*.swift \
  ios/Panels/Features/Conversation/Logic/*.swift ios/Panels/Features/Conversation/State/ConversationModel.swift ios/Panels/Features/Review/ReviewAPI.swift \
  ios/Panels/Features/Review/ReviewDecision.swift ios/Panels/Features/Review/ReviewMessageDecision.swift "$build/Sources/Panels/"
cp ios/Panels/Features/Goals/Models/*.swift ios/Panels/Features/Goals/Logic/*.swift ios/Panels/Features/Goals/Networking/*.swift ios/Panels/Features/Goals/State/*.swift ios/Panels/Features/Artifacts/Logic/ArtifactTarget.swift ios/Panels/Features/Artifacts/Networking/ArtifactReader.swift ios/Panels/Features/Notifications/NotificationContracts.swift ios/Panels/Features/Notifications/Logic/NotificationRouting.swift "$build/Sources/Panels/"
cp ios/Panels/Features/Chief/ChiefAPI.swift ios/Panels/Features/Ticket/Networking/TicketAPI.swift ios/Panels/Features/Ticket/Models/TicketLifecycle.swift ios/Panels/Features/Ticket/Logic/TicketPresentation.swift ios/Panels/Features/Workspace/Logic/WorkspaceGrouping.swift "$build/Sources/Panels/"
cp ios/Panels/Features/Orchestrator/RecapAPI.swift ios/Panels/Features/Orchestrator/Logic/WorkerGroups.swift ios/Panels/Features/Artifacts/Logic/ArtifactMarkdown.swift "$build/Sources/Panels/"
cp ios/PanelsTests/ArtifactLogicTests.swift ios/PanelsTests/WorkerScreenTests.swift ios/PanelsTests/GoalTests.swift ios/PanelsTests/NavigationViewTests.swift ios/PanelsTests/ResourceRefreshTests.swift ios/PanelsTests/ReviewMessageTests.swift ios/PanelsTests/ReviewDecisionTests.swift ios/PanelsTests/OfflineTests.swift ios/PanelsTests/ConversationTests.swift "$build/Tests/PanelsTests/"
cp -R ios/PanelsTests/Fixtures "$build/Tests/PanelsTests/"
cat > "$build/Package.swift" <<'SWIFT'
// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "PanelsOfflineTests", platforms: [.macOS(.v14)], products: [.library(name: "Panels", targets: ["Panels"])], targets: [.target(name: "Panels"), .testTarget(name: "PanelsTests", dependencies: ["Panels"], resources: [.copy("Fixtures")])])
SWIFT
node ios/scripts/goal-parity.cjs "$build/goal-parity.json"
PANELS_GOAL_PARITY_PATH="$build/goal-parity.json" PANELS_STORE_DIRECTORY="$build/store" swift test --package-path "$build" --scratch-path "$build/.build" --jobs 4 "$@"

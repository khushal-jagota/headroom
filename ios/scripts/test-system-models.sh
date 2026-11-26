#!/bin/bash
# Run More models and shared-demand checks on macOS, without a simulator.
set -euo pipefail
cd "$(dirname "$0")/../.."
build=$(mktemp -d "${TMPDIR:-/tmp}/panels-system-tests.XXXXXX")
trap 'rm -rf "$build"' EXIT
touch "$build/.metadata_never_index"
mkdir -p "$build/Sources/Panels" "$build/Tests/PanelsTests"
cp ios/Panels/Core/Networking/ResourceRead.swift ios/Panels/Core/Networking/ResourceDemand.swift ios/Panels/Core/Networking/ResourceOwner.swift ios/Panels/Core/Networking/ResourceVersions.swift ios/Panels/Core/Networking/ChangeStream.swift ios/Panels/Core/Persistence/LocalStore.swift ios/Panels/Core/Persistence/ResponseCacheWriter.swift ios/Panels/Core/Networking/APIClient.swift ios/Panels/Core/Networking/ServerConnection.swift ios/Panels/Models/*.swift ios/Panels/Features/Conversation/Models/ConversationContracts.swift ios/Panels/Features/Conversation/Networking/ConversationMedia.swift ios/Panels/Features/Conversation/Video/VideoContracts.swift ios/Panels/Core/Persistence/OutgoingStorage.swift ios/Panels/Core/Persistence/PendingWork.swift ios/Panels/Features/Review/ReviewAPI.swift \
  ios/Panels/Features/Ticket/Models/TicketLifecycle.swift ios/Panels/Features/More/Usage/UsageModels.swift \
  ios/Panels/Features/More/VPS/VPSModels.swift \
  ios/Panels/Features/More/Settings/SettingsModels.swift "$build/Sources/Panels/"
cp ios/Panels/Features/Orchestrator/Logic/WorkerGroups.swift "$build/Sources/Panels/"
cp ios/PanelsTests/SyncMainTests.swift ios/PanelsTests/SystemScreenTests.swift "$build/Tests/PanelsTests/"
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

/** A real, unsigned Xcode command-line target with enough Swift work to expose reuse. */
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

export async function write_project(root: string): Promise<void> {
  const project = join(root, "Probe.xcodeproj");
  await mkdir(join(project, "xcshareddata", "xcschemes"), { recursive: true });
  const source_names = [
    "main.swift",
    ...Array.from({ length: 32 }, (_, index) => `Part${index}.swift`),
  ];
  const id = (index: number): string => index.toString(16).padStart(24, "0");
  const references = source_names
    .map(
      (name, index) =>
        `${id(100 + index)} = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = ${name}; sourceTree = "<group>";};`,
    )
    .join("\n");
  const build_files = source_names
    .map(
      (_, index) =>
        `${id(200 + index)} = {isa = PBXBuildFile; fileRef = ${id(100 + index)};};`,
    )
    .join("\n");
  await writeFile(
    join(project, "project.pbxproj"),
    `// !$*UTF8*$!
{
archiveVersion = 1; classes = {}; objectVersion = 56;
objects = {
${references}
${build_files}
${id(1)} = {isa = PBXProject; attributes = {LastUpgradeCheck = 2600;}; buildConfigurationList = ${id(6)}; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base); mainGroup = ${id(2)}; productRefGroup = ${id(3)}; projectDirPath = ""; projectRoot = ""; targets = (${id(4)});};
${id(2)} = {isa = PBXGroup; children = (${source_names.map((_, index) => id(100 + index)).join(",")},${id(3)}); sourceTree = "<group>";};
${id(3)} = {isa = PBXGroup; children = (${id(5)}); name = Products; sourceTree = "<group>";};
${id(4)} = {isa = PBXNativeTarget; buildConfigurationList = ${id(7)}; buildPhases = (${id(8)}); buildRules = (); dependencies = (); name = Probe; productName = Probe; productReference = ${id(5)}; productType = "com.apple.product-type.tool";};
${id(5)} = {isa = PBXFileReference; explicitFileType = "compiled.mach-o.executable"; includeInIndex = 0; path = Probe; sourceTree = BUILT_PRODUCTS_DIR;};
${id(6)} = {isa = XCConfigurationList; buildConfigurations = (${id(9)}); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug;};
${id(7)} = {isa = XCConfigurationList; buildConfigurations = (${id(10)}); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug;};
${id(8)} = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (${source_names.map((_, index) => id(200 + index)).join(",")}); runOnlyForDeploymentPostprocessing = 0;};
${id(9)} = {isa = XCBuildConfiguration; buildSettings = {SDKROOT = macosx; MACOSX_DEPLOYMENT_TARGET = 15.0;}; name = Debug;};
${id(10)} = {isa = XCBuildConfiguration; buildSettings = {PRODUCT_NAME = "$(TARGET_NAME)"; SWIFT_VERSION = 5.0; SWIFT_OPTIMIZATION_LEVEL = "-Onone"; SWIFT_COMPILATION_MODE = singlefile; ONLY_ACTIVE_ARCH = YES; CODE_SIGNING_ALLOWED = NO;}; name = Debug;};
}; rootObject = ${id(1)};
}
`,
  );
  await writeFile(
    join(project, "xcshareddata", "xcschemes", "Probe.xcscheme"),
    `<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2600" version="1.3"><BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="${id(4)}" BuildableName="Probe" BlueprintName="Probe" ReferencedContainer="container:Probe.xcodeproj"/></BuildActionEntry></BuildActionEntries></BuildAction></Scheme>
`,
  );
  await writeFile(
    join(root, "main.swift"),
    `import Foundation\nprint("baseline:" + String(${Array.from({ length: 32 }, (_, index) => `part${index}()`).join(" + ")}))\n`,
  );
  await Promise.all(
    Array.from({ length: 32 }, async (_, index) => {
      await writeFile(
        join(root, `Part${index}.swift`),
        `import Foundation
struct Value${index}: Codable { let index: Int; let label: String }
func part${index}() -> Int {
  let value = Value${index}(index: ${index}, label: "part-${index}")
  let encoded = try! JSONEncoder().encode(value)
  return try! JSONDecoder().decode(Value${index}.self, from: encoded).index
}
`,
      );
    }),
  );
}

/** Construct launch values only. No provider executable or interpreter can start here. */
import { child_environment, hermes_acp_child_launch } from '../../backends/hermes_acp/launch.ts';
import { ConversationAccess, ConversationRoleMaterials } from '../../contracts.ts';

interface Case {
  name: string;
  ambient: Record<string, string>;
  overrides: [string, string][];
  identity: [string, string][] | null;
}
interface Specification {
  launch: Parameters<typeof hermes_acp_child_launch>[0];
  cases: Case[];
}
export function run_launch_parity(spec: Specification): unknown {
  const before = { ...process.env };
  const launch = hermes_acp_child_launch(spec.launch);
  const cases = spec.cases.map((item) => ({
    name: item.name,
    environment: child_environment(
      { ...launch, environment_overrides: [...launch.environment_overrides, ...item.overrides] },
      {
        access: ConversationAccess.full,
        role_materials:
          item.identity === null
            ? null
            : new ConversationRoleMaterials({
                role_text: 'Synthetic launch identity',
                identity_environment_variables: item.identity,
              }),
      },
      item.ambient,
    ),
  }));
  return {
    launch,
    cases,
    parent_environment_unchanged: JSON.stringify(process.env) === JSON.stringify(before),
  };
}

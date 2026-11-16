import type { BackendProbeEnvironment, BackendUpdateAdvisory, ProbeRecipe } from './contracts.ts';
import { classify_install_method, npm_prefix_from_path } from './logic/install.ts';
import { version_is_newer } from './logic/versions.ts';
import { REGISTRY_LOOKUP_TIMEOUT_SECONDS } from './configuration.ts';
export async function update_advisory(
  recipe: ProbeRecipe,
  executable_path: string,
  version: string | null,
  environment: BackendProbeEnvironment,
): Promise<BackendUpdateAdvisory | null> {
  if (recipe.registry_package_name === null && recipe.native_update_command === null) return null;
  const real_path = environment.real_path(executable_path);
  let install_method = classify_install_method([executable_path, real_path], {
    native_path_marker: recipe.native_path_marker,
  });
  let prefix: string | null = null;
  if (install_method !== 'native') {
    if (install_method === 'npm_global') {
      prefix = npm_prefix_from_path(real_path);
      const normalize = (value: string) =>
        environment.real_path(value).replaceAll('\\', '/').toLowerCase();
      if (
        prefix === null ||
        normalize(prefix) !== normalize(environment.user_local_npm_prefix()) ||
        !environment.prefix_is_owned_and_writable(prefix)
      )
        install_method = 'manual_only';
    } else install_method = 'manual_only';
  }
  const update_command =
    install_method === 'native'
      ? recipe.native_update_command
      : install_method === 'npm_global' && prefix !== null && recipe.registry_package_name !== null
        ? [
            'npm',
            'install',
            '--global',
            '--prefix',
            prefix,
            recipe.registry_package_name + '@latest',
          ]
        : null;
  const latest_version =
    recipe.registry_package_name !== null &&
    ['npm_global', 'bun_global', 'pnpm_global'].includes(install_method)
      ? await environment.latest_released_version(recipe.registry_package_name, {
          timeout_seconds: REGISTRY_LOOKUP_TIMEOUT_SECONDS,
        })
      : null;
  const update_available =
    latest_version !== null && version !== null && version_is_newer(latest_version, version);
  const detail =
    update_command === null
      ? recipe.manual_only_detail
      : update_available && latest_version !== null
        ? `Version ${latest_version} is available.`
        : latest_version !== null
          ? 'This is the newest published version.'
          : "Panels can run this backend's own update command; there is no newer version to report.";
  return { install_method, update_command, latest_version, update_available, detail };
}

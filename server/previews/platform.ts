import { LinuxProcessInspector } from './process.ts';
import { DarwinProcessInspector } from './darwin.ts';
import { PreviewError, type ProcessInspector } from './contracts.ts';
export function process_inspector(platform: string = process.platform): ProcessInspector {
  if (platform === 'linux') return new LinuxProcessInspector();
  if (platform === 'darwin') return new DarwinProcessInspector();
  const unavailable = async (): Promise<never> => {
    throw new PreviewError('Preview process evidence is unavailable on this operating system.', 503);
  };
  return { discover: unavailable, check: unavailable, connected: unavailable };
}

/** Where a provider home keeps its transcripts, normalized as the Python service did. */
import { expand_path } from '../../environments/paths.ts';

/** pathlib removes empty and '.' segments, but deliberately retains '..'. */
export function transcript_home(path: string, child: string): string {
  const expanded = expand_path(path);
  const root =
    expanded.startsWith('//') && !expanded.startsWith('///')
      ? '//'
      : expanded.startsWith('/')
        ? '/'
        : '';
  const parts = expanded.split('/').filter((part) => part !== '' && part !== '.');
  return root + [...parts, child].join('/');
}

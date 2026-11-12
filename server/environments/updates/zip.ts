/** File-backed ZIP reads keep an application artifact out of memory. */
import yauzl from 'yauzl';
import type { Entry, ZipFile } from 'yauzl';
import { Readable } from 'node:stream';
import { crc32 } from 'node:zlib';
export async function with_zip_member<T>(
  path: string,
  name: string,
  consume: (stream: Readable) => Promise<T>,
): Promise<T> {
  const archive = await new Promise<ZipFile>((resolve, reject) =>
    yauzl.open(
      path,
      { lazyEntries: true, autoClose: false, validateEntrySizes: true },
      (error, result) => (error ? reject(error) : resolve(result!)),
    ),
  );
  try {
    const entry = await new Promise<Entry>((resolve, reject) => {
      let found: Entry | undefined;
      archive.on('error', reject);
      archive.on('entry', (entry: Entry) => {
        if (entry.fileName === name) found = entry;
        archive.readEntry();
      });
      archive.on('end', () =>
        found === undefined
          ? reject(new Error(`There is no item named '${name}' in the archive`))
          : resolve(found),
      );
      archive.readEntry();
    });
    const stream = await new Promise<Readable>((resolve, reject) =>
      archive.openReadStream(entry, (error, result) => (error ? reject(error) : resolve(result!))),
    );
    const validated = Readable.from(
      (async function* () {
        let checksum = 0;
        for await (const chunk of stream) {
          checksum = crc32(chunk, checksum);
          yield chunk;
        }
        if (checksum !== entry.crc32) throw new Error(`Bad CRC-32 for file '${name}'`);
      })(),
    );
    try {
      return await consume(validated);
    } finally {
      validated.destroy();
      stream.destroy();
    }
  } finally {
    archive.close();
  }
}

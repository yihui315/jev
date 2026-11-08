// Verify verbatim frozen Paper engine sources embedded in the cloud build.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
const manifest = JSON.parse(readFileSync(new URL('./vendor-manifest.json', import.meta.url), 'utf8'));
if (manifest.origin_commit !== '34b2e54c7a125c149c758c16b8da8faa5b4b2229' || manifest.files.length !== 5) throw Error('UNEXPECTED_ENGINE_PROVENANCE');
for (const file of manifest.files) {
  if (!file.path.startsWith('vendor/nr_trusted/') || file.path.includes('..')) throw Error('BAD_ENGINE_PATH');
  const bytes = readFileSync(new URL('../' + file.path, import.meta.url));
  const sha = createHash('sha1').update('blob ' + bytes.length + '\0').update(bytes).digest('hex');
  if (sha !== file.git_blob_sha) throw Error('ENGINE_VENDOR_HASH_MISMATCH: ' + file.path);
}
console.log('[engine-vendor] verified 5 immutable files from 34b2e54c7a125c149c758c16b8da8faa5b4b2229');

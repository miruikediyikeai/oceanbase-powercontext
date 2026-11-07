import { SeekdbClient } from 'seekdb';
import { writeFile, readFile } from 'node:fs/promises';
import { loadEnvFile } from 'node:process';
try { loadEnvFile(); } catch (e) { if (e.code !== 'ENOENT') throw e; }
const client = new SeekdbClient({ host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '', database: process.env.SEEKDB_DATABASE || 'stepfun' });
try {
  const sql = await client.execute('SELECT VERSION() AS version, 1+1 AS sqlCheck, NOW() AS serverTime');
  if (!sql[0].version.includes('seekdb') || sql[0].sqlCheck !== 2) throw new Error('Not a verified seekdb instance');
  const tables = await client.execute('SHOW TABLES');
  const collections = await client.listCollections({ withEmbeddingFunction: false });
  const metadata = await client.execute('SELECT collection_id, collection_name FROM sdk_collections WHERE collection_name = ?', ['kb_personal']);
  const physical = metadata.length ? 'c$v2$' + metadata[0].collection_id : null;
  const ddl = [];
  for (const row of tables) {
    const name = Object.values(row)[0];
    if (name === physical || name === 'kb_catalog') ddl.push(await client.execute('SHOW CREATE TABLE `' + name.replaceAll('`', '``') + '`'));
  }
  const parameters = {};
  for (const key of ['memory_limit', 'cpu_count', 'datafile_size', 'datafile_maxsize', 'log_disk_size']) {
    parameters[key] = (await client.execute('SHOW PARAMETERS LIKE ?', [key])).map(row => ({ name: row.name, value: row.value }));
  }
  const report = { checkedAt: new Date().toISOString(), sql, ddl, parameters, collectionCount: collections.length };
  if (process.env.SEEKDB_TELEMETRY_FILE) {
    const telemetry = JSON.parse(await readFile(process.env.SEEKDB_TELEMETRY_FILE, 'utf8'));
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(telemetry.content.id)) throw new Error('Invalid telemetry UUID');
    report.telemetryId = telemetry.content.id;
  } else report.telemetry = 'No telemetry file supplied; no ID has been fabricated.';
  await writeFile(new URL('../evidence/database.json', import.meta.url), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await client.close(); }

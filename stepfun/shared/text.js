// Limit each Chinese chunk to comfortably fit the model's 512-token window.
export function chunkText(text, size = 380, overlap = 50) {
  const normalized = text.replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];
  const chars = Array.from(normalized), chunks = [];
  for (let start = 0; start < chars.length; start += size - overlap) {
    chunks.push(chars.slice(start, start + size).join(''));
    if (start + size >= chars.length) break;
  }
  return chunks;
}
export async function digest(text) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, '0')).join('');
}
export function median(values) {
  const sorted = [...values].sort((a, b) => a - b), mid = Math.floor(sorted.length / 2);
  return sorted.length ? (sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2) : null;
}
export function quality(ids, relevant, k = 5) {
  const results = ids.slice(0, k), truth = new Set(relevant);
  const hits = results.filter(id => truth.has(id));
  const dcg = results.reduce((s, id, i) => s + (truth.has(id) ? 1 / Math.log2(i + 2) : 0), 0);
  const ideal = Array.from({ length: Math.min(k, truth.size) }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, b) => a + b, 0);
  return { recall: hits.length / truth.size, ndcg: ideal ? dcg / ideal : 0 };
}

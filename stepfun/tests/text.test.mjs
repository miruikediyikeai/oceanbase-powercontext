import test from 'node:test';
import assert from 'node:assert/strict';
import { chunkText, digest, median, quality } from '../shared/text.js';
test('chunking preserves Unicode, overlap, and tail', () => {
  const input = '知'.repeat(379) + '🙂' + '识'.repeat(440), chunks = chunkText(input);
  assert.equal(chunks.length, 3);
  assert.ok(chunks.every(x => Array.from(x).length <= 380));
  assert.equal(Array.from(chunks[0]).slice(-50).join(''), Array.from(chunks[1]).slice(0, 50).join(''));
  assert.equal(chunks[0] + chunks.slice(1).map(x => Array.from(x).slice(50).join('')).join(''), input);
  assert.deepEqual(chunkText(' \n '), []);
});
test('stable chunk identities distinguish source and content', async () => {
  assert.equal(await digest('file:0:content'), await digest('file:0:content'));
  assert.notEqual(await digest('a:0:content'), await digest('b:0:content'));
});
test('quality metrics and medians have known outcomes', () => {
  assert.deepEqual(quality(['x', 'a'], ['a'], 1), { recall: 0, ndcg: 0 });
  assert.deepEqual(quality(['a'], ['a'], 5), { recall: 1, ndcg: 1 });
  assert.equal(median([1, 100, 3]), 3); assert.equal(median([1, 2, 3, 4]), 2.5);
});

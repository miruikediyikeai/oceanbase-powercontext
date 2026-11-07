export const MODEL = Object.freeze({
  id: 'Xenova/bge-small-zh-v1.5', revision: '75c43b069aac4d136ba6bc1122f995fedcfd2781',
  dimension: 512, pooling: 'cls', normalize: true, maxLength: 512,
  queryPrefix: '为这个句子生成表示以用于检索相关文章：',
});
export const MODEL_KEY = `${MODEL.id}@${MODEL.revision}:cls:normalized:512`;
export const DTYPES = ['q8', 'fp32', 'fp16'];

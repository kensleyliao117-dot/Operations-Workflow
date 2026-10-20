import test from 'node:test'
import assert from 'node:assert/strict'
import { imageRatios, imageRatioOptions, normalizeImageSize } from '../src/utils/image_sizes.js'

test('five displayed ratios map to exact pixel dimensions', () => {
  assert.deepEqual(
    imageRatios.map((row) => row.ratio),
    ['1:1', '3:4', '4:3', '16:9', '9:16'],
  )
  for (const { ratio, size } of imageRatios) {
    const [rw, rh] = ratio.split(':').map(Number)
    const [w, h] = size.split('*').map(Number)
    assert.equal(w * rh, h * rw)
  }
  assert.equal(normalizeImageSize('1728*2368', imageRatioOptions()), '1536*2048')
})

test('custom model preserves exact supported dimensions and disables unsupported ratios', () => {
  const options = imageRatioOptions({ image_sizes: ['1024*1024', '1536*2048', '1024*1536'] })
  assert.deepEqual(
    options.filter((row) => !row.disabled).map((row) => row.ratio),
    ['1:1', '3:4'],
  )
  assert.equal(normalizeImageSize('2048*2048', options), '1024*1024')
  assert.equal(normalizeImageSize('1024*1536', options), '1536*2048')
  assert.equal(normalizeImageSize('1536*2048', imageRatioOptions({ image_sizes: [] })), '')
})

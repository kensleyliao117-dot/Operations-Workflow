import test from 'node:test'
import assert from 'node:assert/strict'
import {
  canApprove,
  canPublish,
  canEdit,
  reviewSubmissionAction,
  splitTags,
  safeLink,
} from '../src/utils/domain.js'
import { publishKey, userKey } from '../src/utils/storage.js'
import { formatObservationAge } from '../src/utils/metrics.js'
test('observation windows preserve unknown time and distinguish hours from days', () => {
  assert.equal(formatObservationAge(7200), '发布后 2 小时')
  assert.equal(formatObservationAge(86400), '发布后 1 天')
  assert.equal(formatObservationAge(93660), '发布后 1 天 2 小时 1 分钟')
  assert.equal(formatObservationAge(0), '发布后 0 秒')
  for (const value of [null, undefined, -1, NaN]) assert.match(formatObservationAge(value), /未知/)
})
const latest = { id: 12 },
  historical = { id: 11 }
test('only an unedited latest version can be approved and risk checks remain advisory', () => {
  const task = { status: 'pending_review' },
    check = { content_id: 12, status: 'no_risk_detected' }
  assert.equal(canApprove(task, latest, latest, check, false), true)
  for (const value of [
    null,
    { ...check, status: 'risk_detected' },
    { ...check, status: 'error' },
    { ...check, content_id: 11 },
  ])
    assert.equal(canApprove(task, latest, latest, value, false), true)
  for (const args of [
    [task, historical, latest, check, false],
    [task, latest, latest, check, true],
    [task, null, latest, null, false],
  ])
    assert.equal(canApprove(...args), false)
})
test('review submission distinguishes unsaved edits, saved versions and same-version submission', () => {
  const content = { id: 12, version: 2 },
    task = { status: 'running' }
  const workflow = { status: 'waiting_review', interrupt: {}, content_id: 11, content_version: 1 }
  assert.equal(reviewSubmissionAction(task, content, content, workflow), 'submit_changes')
  const same = { ...workflow, content_id: 12, content_version: 2 }
  assert.equal(reviewSubmissionAction(task, content, content, same), 'submit_review')
  assert.equal(reviewSubmissionAction({ status: 'pending_review' }, content, content, same), null)
  assert.equal(reviewSubmissionAction({ status: 'rejected' }, content, content, same), null)
  assert.equal(
    reviewSubmissionAction({ status: 'rejected' }, content, content, same, true),
    'submit_changes',
  )
  assert.equal(reviewSubmissionAction(task, content, { id: 13 }, same), null)
  assert.equal(reviewSubmissionAction(task, content, content, { ...same, interrupt: null }), null)
})
test('cancelled tasks, old versions, interrupted reviews and unknown publication block publishing', () => {
  const task = { status: 'approved' }
  assert.equal(canPublish(task, latest, latest, [], false, { status: 'approved' }), true)
  for (const args of [
    [task, historical, latest, [], false, null],
    [task, latest, latest, [], true, null],
    [task, latest, latest, [{ publish_status: 'unknown' }], false, null],
    [task, latest, latest, [], false, { status: 'waiting_review' }],
    [{ status: 'cancelled' }, latest, latest, [], false, null],
  ])
    assert.equal(canPublish(...args), false)
  assert.equal(
    canEdit({ status: 'failed', failed_from_status: 'publishing' }, latest, latest),
    false,
  )
})
test('saved latest content can resume a failed rule stage without regenerating', () => {
  const task = { status: 'running' },
    content = { id: 3, version: 3 }
  const workflow = {
    status: 'failed',
    interrupt: null,
    next_nodes: ['platform_rules'],
    content_id: 1,
    content_version: 1,
  }
  assert.equal(reviewSubmissionAction(task, content, content, workflow), 'retry_review')
  assert.equal(reviewSubmissionAction(task, content, content, workflow, true), 'retry_review')
  assert.equal(
    reviewSubmissionAction(task, content, content, {
      ...workflow,
      next_nodes: ['generate_images'],
    }),
    null,
  )
  assert.equal(reviewSubmissionAction({ status: 'cancelled' }, content, content, workflow), null)
  assert.equal(reviewSubmissionAction(task, { id: 1, version: 1 }, content, workflow), null)
})

test('request keys survive refresh and are separated by user, version, account and visibility', () => {
  const data = new Map()
  globalThis.localStorage = {
    getItem: (key) => data.get(key) || null,
    setItem: (key, value) => data.set(key, value),
  }
  const key = publishKey(1, 2, 12, 4, 'private')
  assert.equal(publishKey(1, 2, 12, 4, 'private'), key)
  for (const args of [
    [2, 2, 12, 4, 'private'],
    [1, 2, 13, 4, 'private'],
    [1, 2, 12, 5, 'private'],
    [1, 2, 12, 4, 'public'],
  ])
    assert.notEqual(publishKey(...args), key)
  assert.notEqual(userKey(1, 'content-draft', 12), userKey(2, 'content-draft', 12))
})
test('tag normalization and links preserve text without accepting script URLs', () => {
  assert.deepEqual(splitTags('#通勤，耳机 #通勤'), ['通勤', '耳机'])
  assert.equal(safeLink('javascript:alert(1)'), undefined)
  assert.equal(safeLink('https://name:password@example.com'), undefined)
  assert.equal(safeLink('https://example.com/note'), 'https://example.com/note')
})

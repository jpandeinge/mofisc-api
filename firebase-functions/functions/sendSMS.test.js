const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const secret = 'test-only-server-secret-32-characters-long';
function fixture(configuredSecret = secret) {
  const sent = [];
  const exports = {};
  const firestore = () => ({ collection: () => ({ add: async () => {} }) });
  firestore.FieldValue = { serverTimestamp: () => 0 };
  vm.runInNewContext(fs.readFileSync(require.resolve('./sendSMS'), 'utf8'), {
    exports, console: { log() {}, error() {} },
    require(name) {
      if (name === 'firebase-functions/v2/https') return { onRequest: (_, handler) => handler };
      if (name === 'firebase-functions/params') return { defineSecret: key => ({ value: () => key === 'SMS_API_SECRET' ? configuredSecret : 'test-credential' }) };
      if (name === 'firebase-admin') return { apps: [{}], firestore };
      if (name === 'twilio') return () => ({ messages: { create: async input => { sent.push(input); return { sid: 'test-sid', status: 'queued' }; } } });
      return require(name);
    },
  });
  async function call(endpoint, body, token = secret, method = 'POST') {
    const res = { statusCode: 200, set() {}, status(code) { this.statusCode = code; return this; }, json(data) { this.body = data; } };
    await exports[endpoint]({ method, body, get: () => token ? 'Bearer ' + token : '' }, res);
    return res;
  }
  return { call, sent };
}
test('both endpoints reject unauthenticated calls before contacting Twilio', async () => {
  const { call, sent } = fixture();
  for (const endpoint of ['sendSMS', 'sendBulkSMS']) {
    for (const token of ['', 'public-firebase-api-key']) {
      assert.equal((await call(endpoint, {}, token)).statusCode, 401);
    }
    assert.equal((await call(endpoint, {}, secret, 'GET')).statusCode, 405);
  }
  assert.equal(sent.length, 0);
});
test('missing or short server secrets fail closed', async () => {
  for (const value of ['', 'short']) assert.equal((await fixture(value).call('sendSMS', {})).statusCode, 503);
});
test('valid authenticated messages are sent', async () => {
  const { call, sent } = fixture();
  assert.equal((await call('sendSMS', { to: '+264811234567', body: 'Hello' })).statusCode, 200);
  assert.equal(sent.length, 1);
});
test('invalid payload types and oversized batches never trigger SMS charges', async () => {
  const { call, sent } = fixture();
  for (const body of [null, {}, { to: {}, body: 'Hi' }, { to: '+264811234567', body: 'x'.repeat(1601) }]) {
    assert.equal((await call('sendSMS', body)).statusCode, 400);
  }
  for (const recipients of [[], ['bad-number'], Array(101).fill('+264811234567')]) {
    assert.equal((await call('sendBulkSMS', { recipients, body: 'Hello' })).statusCode, 400);
  }
  assert.equal(sent.length, 0);
});
test('Firebase and the overridden CommonJS UUID dependency load successfully', () => {
  const { v4 } = require('uuid');
  assert.match(v4(), /^[0-9a-f-]{36}$/);
  assert.equal(typeof require('firebase-admin/firestore').getFirestore, 'function');
  assert.equal(typeof require('gaxios').request, 'function');
});

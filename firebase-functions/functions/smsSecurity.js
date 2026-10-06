'use strict';

const { createHash, timingSafeEqual } = require('node:crypto');
const hash = value => createHash('sha256').update(value).digest();
const validPhone = value => typeof value === 'string' && /^\+[1-9]\d{1,14}$/.test(value);
const validBody = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 1600;

// These are server-to-server endpoints. A public Firebase web API key is NOT
// authentication; use a separate random secret shared only with the API.
function authorize(req, res, secret) {
  if (req.method !== 'POST') {
    res.set('Allow', 'POST');
    res.status(405).json({ error: 'Method Not Allowed' });
    return false;
  }
  if (typeof secret !== 'string' || secret.length < 32) {
    res.status(503).json({ error: 'SMS service not configured' });
    return false;
  }
  const header = req.get('Authorization') || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : '';
  if (!token || !timingSafeEqual(hash(token), hash(secret))) {
    res.status(401).json({ error: 'Unauthorized' });
    return false;
  }
  return true;
}

module.exports = { authorize, validPhone, validBody };

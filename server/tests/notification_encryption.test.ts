import {
  createDecipheriv,
  createECDH,
  generateKeyPairSync,
  hkdfSync,
  randomBytes,
} from 'node:crypto';
import { expect, test } from 'vitest';
import { build_web_push_request } from '../notifications/web_push.ts';

// Intent comparison cannot detect a wrong subscription key, VAPID key conversion,
// or encryption format. Decrypt the production request with independent Node crypto.
// Request generation is offline: this proof never invokes a transport adapter.
test('the production Web Push request decrypts with its subscription keys', () => {
  const receiver = createECDH('prime256v1');
  const publicKey = receiver.generateKeys();
  const auth = randomBytes(16);
  const vapid = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const vapidPublic = vapid.publicKey.export({ format: 'jwk' });
  const intent = {
    title: 'Panels',
    body: 'A question needs your answer. café 🔔',
    route: '/#/workspace/example',
    tag: 'panels-ticket-example',
  };
  const request = build_web_push_request(
    {
      subscription_id: 'synthetic-subscription',
      endpoint: 'https://push.invalid/subscription',
      p256dh: publicKey.toString('base64url'),
      auth: auth.toString('base64url'),
    },
    intent,
    {
      private_key: vapid.privateKey.export({ format: 'der', type: 'pkcs8' }).toString('base64url'),
      public_key: Buffer.concat([
        Buffer.from([4]),
        Buffer.from(vapidPublic.x!, 'base64url'),
        Buffer.from(vapidPublic.y!, 'base64url'),
      ]).toString('base64url'),
    },
    { subject: 'https://comparison.invalid' },
  );
  expect(request.headers['Content-Encoding']).toBe('aes128gcm');
  const body = request.body!;
  const salt = body.subarray(0, 16);
  const keyLength = body[20]!;
  const senderKey = body.subarray(21, 21 + keyLength);
  const encrypted = body.subarray(21 + keyLength);
  const shared = receiver.computeSecret(senderKey);
  const keyInfo = Buffer.concat([Buffer.from('WebPush: info\0'), publicKey, senderKey]);
  const ikm = hkdfSync('sha256', shared, auth, keyInfo, 32);
  const key = hkdfSync(
    'sha256',
    Buffer.from(ikm),
    salt,
    Buffer.from('Content-Encoding: aes128gcm\0'),
    16,
  );
  const nonce = hkdfSync(
    'sha256',
    Buffer.from(ikm),
    salt,
    Buffer.from('Content-Encoding: nonce\0'),
    12,
  );
  const decipher = createDecipheriv('aes-128-gcm', Buffer.from(key), Buffer.from(nonce));
  decipher.setAuthTag(encrypted.subarray(-16));
  const plain = Buffer.concat([decipher.update(encrypted.subarray(0, -16)), decipher.final()]);
  expect(plain[plain.length - 1]).toBe(2);
  expect(JSON.parse(plain.subarray(0, -1).toString('utf8'))).toEqual(intent);
});

import Redis from 'ioredis';

let _client: Redis | null = null;

export function getRedis(): Redis {
  if (!_client) {
    _client = new Redis(process.env.REDIS_URL ?? 'redis://localhost:6379/0', {
      lazyConnect: false,
      maxRetriesPerRequest: 1,
      connectTimeout: 2000,
      enableOfflineQueue: false,
    });
    _client.on('error', () => { /* suppress */ });
  }
  return _client;
}

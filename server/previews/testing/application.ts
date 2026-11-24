/** Native preview fixture: one owned application in the Ticket worktree. */
import { createServer } from 'node:http';
import { WebSocketServer } from 'ws';
const server = createServer((_request, response) => response.end('machine preview'));
const sockets = new WebSocketServer({ server });
sockets.on('connection', (socket) => socket.on('message', (data) => socket.send(data)));
server.listen(0, '127.0.0.1', () => {
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Application listener missing');
  process.stdout.write(JSON.stringify({ port: address.port }) + '\n');
});

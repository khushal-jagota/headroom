import { createConversationController, type ConversationController } from './conversationController';
import { createPanelsTransport, type PanelsSocket } from './panelsTransport';

export const PRODUCTION_CONVERSATION_RECONNECT_DELAY_MS = 500;

export interface ProductionConversationEnvironment {
  readonly location: Pick<Location, 'protocol' | 'host'>;
  socketFactory(url: string): PanelsSocket;
  randomUUID(): string;
  setTimer(callback: () => void, delayMs: number): unknown;
  clearTimer(timer: unknown): void;
  now(): number;
}

export function productionConversationUrl(
  location: Pick<Location, 'protocol' | 'host'>,
): string {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${location.host}/api/conversation`;
}

function browserEnvironment(): ProductionConversationEnvironment {
  return {
    location: window.location,
    socketFactory: (url) => new WebSocket(url) as unknown as PanelsSocket,
    randomUUID: () => crypto.randomUUID(),
    setTimer: (callback, delayMs) => window.setTimeout(callback, delayMs),
    clearTimer: (timer) => window.clearTimeout(timer as number),
    now: () => Date.now(),
  };
}

export function createProductionConversationController(
  employeeId: string,
  environment: ProductionConversationEnvironment = browserEnvironment(),
  deferInitialAttach = false,
): ConversationController {
  return createConversationController({
    employeeId,
    deferInitialAttach,
    transportFactory: (callbacks) => createPanelsTransport({
      url: productionConversationUrl(environment.location),
      socketFactory: environment.socketFactory,
      ...callbacks,
    }),
    reconnectDelayMs: PRODUCTION_CONVERSATION_RECONNECT_DELAY_MS,
    setTimer: environment.setTimer,
    clearTimer: environment.clearTimer,
    now: environment.now,
    fallbackId: (turnOrdinal, role, segmentOrdinal) => (
      `${employeeId}:fallback:${turnOrdinal}:${role}:${segmentOrdinal}`
    ),
    clientMessageId: environment.randomUUID,
  });
}

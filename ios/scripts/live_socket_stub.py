"""A stand-in for the server's live socket, `/api/live`, for the simulator stubs.

It speaks the contract in `server/live_updates/contracts.ts` with the standard library only:
the first frame is `hello`, the answer is `ready`, and every later server frame is an envelope
with a sequence number that starts at 1. A stub supplies what each subscription sends.
"""

import base64
import hashlib
import json
import struct
import threading
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PATH = "/api/live"


def _read_exactly(stream, count):
    data = b""
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def _read_frame(stream):
    """The next client frame as (opcode, payload). Client frames are masked."""
    first, second = _read_exactly(stream, 2)
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack(">H", _read_exactly(stream, 2))
    elif length == 127:
        (length,) = struct.unpack(">Q", _read_exactly(stream, 8))
    mask = _read_exactly(stream, 4) if second & 0x80 else b"\0\0\0\0"
    payload = bytearray(_read_exactly(stream, length))
    for index in range(length):
        payload[index] ^= mask[index % 4]
    return first & 0x0F, bytes(payload)


def _frame(opcode, payload):
    header = bytes([0x80 | opcode])
    if len(payload) < 126:
        header += bytes([len(payload)])
    elif len(payload) < 65536:
        header += bytes([126]) + struct.pack(">H", len(payload))
    else:
        header += bytes([127]) + struct.pack(">Q", len(payload))
    return header + payload


def serve(handler, frames, *, periods=None, changes=True, alive=lambda: True, on_subscribe=None):
    """Runs one socket on a stub's request handler until it ends.

    `frames(subscription)` returns the (event, json_text) pairs that a subscription sends on
    each of its turns. A subscription is a dict with `id`, `kind`, `conversation_id` and
    `params`. `periods` maps a kind to the seconds between its turns. With `changes`, the
    socket sends a change ping on each turn of the first period. When `alive()` turns false,
    the socket closes as a drop, as an ended event stream did.
    """
    periods = periods or {}
    key = handler.headers.get("Sec-WebSocket-Key")
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    handler.wfile.write(
        ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
         f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()
    )
    handler.wfile.flush()
    handler.close_connection = True
    lock = threading.Lock()
    subscriptions = {}
    sequence = [0]
    finished = threading.Event()

    def write(raw):
        with lock:
            handler.wfile.write(raw)
            handler.wfile.flush()

    def send(sub, kind, data=None):
        with lock:
            sequence[0] += 1
            text = json.dumps({"seq": sequence[0], "sub": sub, "kind": kind})[:-1]
            if data is not None:
                text += ',"data":' + data
            text += "}"
            handler.wfile.write(_frame(1, text.encode()))
            handler.wfile.flush()

    def close(code):
        try:
            write(_frame(8, struct.pack(">H", code)))
        except OSError:
            pass
        finished.set()

    def read():
        try:
            while not finished.is_set():
                opcode, payload = _read_frame(handler.rfile)
                if opcode == 8:
                    break
                if opcode == 9:
                    write(_frame(10, payload))
                    continue
                message = json.loads(payload)
                if message["op"] == "hello":
                    send(None, "ready", '{"heartbeat_ms":15000}')
                elif message["op"] == "subscribe":
                    subscription = {
                        "id": message["id"], "kind": message["kind"],
                        "conversation_id": message.get("conversation_id"),
                        "params": message.get("params", {}), "due": 0,
                    }
                    if on_subscribe:
                        on_subscribe(subscription)
                    subscriptions[message["id"]] = subscription
                elif message["op"] == "unsubscribe":
                    subscriptions.pop(message["id"], None)
        except (OSError, ConnectionError, ValueError):
            pass
        finished.set()

    threading.Thread(target=read, daemon=True).start()
    ping_every = min(periods.values(), default=1)
    next_ping = 0
    try:
        while not finished.is_set():
            if not alive():
                return close(1001)
            now = time.time()
            for subscription in list(subscriptions.values()):
                if now < subscription["due"]:
                    continue
                subscription["due"] = now + periods.get(subscription["kind"], 1)
                for event, data in frames(subscription):
                    send(subscription["id"], event, data)
            if changes and now >= next_ping and sequence[0]:
                next_ping = now + ping_every
                send(None, "change")
            time.sleep(0.1)
    except OSError:
        pass

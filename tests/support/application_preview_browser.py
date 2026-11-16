"""Local TLS edge for the real Vite browser proof; never used by the product.

Tailscale terminates TLS and supplies identity in production. This loopback-only
shim supplies precisely those two facilities so browser-generated WSS URLs exercise
the unchanged canonical-origin gate. It forwards real bytes and WebSocket frames.
"""

from __future__ import annotations

import asyncio
import ssl
import sys

from aiohttp import ClientSession, ClientWebSocketResponse, WSMsgType, web


async def serve(upstream: str, port: int, certificate: str, key: str) -> None:
    async with ClientSession(trust_env=False, auto_decompress=False) as session:

        async def forward(request: web.Request) -> web.StreamResponse:
            headers = dict(request.headers)
            headers["Tailscale-User-Login"] = "owner@example.test"
            target = upstream + request.raw_path
            if request.headers.get("Upgrade", "").lower() == "websocket":
                protocols = tuple(
                    item.strip()
                    for item in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
                    if item.strip()
                )
                for name in list(headers):
                    if name.lower().startswith("sec-websocket-"):
                        del headers[name]
                async with session.ws_connect(target, headers=headers, protocols=protocols) as peer:
                    downstream = web.WebSocketResponse(protocols=protocols)
                    await downstream.prepare(request)

                    async def pump(
                        source: ClientWebSocketResponse | web.WebSocketResponse,
                        destination: ClientWebSocketResponse | web.WebSocketResponse,
                    ) -> None:
                        async for message in source:
                            if message.type == WSMsgType.TEXT:
                                await destination.send_str(message.data)
                            elif message.type == WSMsgType.BINARY:
                                await destination.send_bytes(message.data)

                    tasks = [
                        asyncio.create_task(pump(peer, downstream)),
                        asyncio.create_task(pump(downstream, peer)),
                    ]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    return downstream
            async with session.request(
                request.method,
                target,
                headers=headers,
                data=await request.read(),
                allow_redirects=False,
            ) as response:
                response_headers = {
                    name: value
                    for name, value in response.headers.items()
                    if name.lower() not in {"transfer-encoding", "connection"}
                }
                result = web.StreamResponse(status=response.status, headers=response_headers)
                await result.prepare(request)
                async for chunk in response.content.iter_chunked(65536):
                    await result.write(chunk)
                await result.write_eof()
                return result

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", forward)
        runner = web.AppRunner(app)
        await runner.setup()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        await web.TCPSite(runner, "127.0.0.1", port, ssl_context=context).start()
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(serve(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]))

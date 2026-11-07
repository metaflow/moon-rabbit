# DRAFT — upstream bug report for PythonistaGuild/TwitchIO

> Not filed yet. Intended for https://github.com/PythonistaGuild/TwitchIO/issues/new?template=bug.yml
> (sections below mirror the fields of their bug template). Review, then paste in.
> Checked on 2026-11-07: no existing issue covers this (open: #547, #495, #494; related closed: #522, #492).

**Title:** `[Bug]: Websocket.close() cancels its own task when called from the keepalive watchdog → socket never closed, leaks one connection per trip (Conduit shards hit 429 after ~1000)`

---

## What actually happened?

When the keepalive watchdog (`Websocket._process_keepalive`) decides a socket is stale, it calls
`_create_connection_task()` → `close()` **from inside `_keep_alive_task` itself**. `close()` then does:

```python
if self._keep_alive_task:
    try:
        self._keep_alive_task.cancel()      # <- cancels the task that is running this very code
    ...
if self._socket:
    try:
        await self._socket.close()          # <- CancelledError is delivered here
    except Exception:                       # <- CancelledError is a BaseException, not caught
        pass
```

The pending self-cancellation is raised at the first `await` after `.cancel()`, i.e. `await self._socket.close()`.
`CancelledError` is a `BaseException`, so `except Exception` does not swallow it and `close()` aborts half-way:

- `self._socket` is never closed and never set to `None` (the TCP/WebSocket connection stays open),
- `self._closing` stays `True` forever, so every later `close()` call returns immediately,
- `_cleanup()` already set `_closed = True`, so the object looks closed while the connection is not,
- the `"Successfully closed ..."` log line is never emitted (only `"needs to close unexpectedly"`),
- the `websocket_closed` event **was** already dispatched, so `Client._websocket_closed` opens a *replacement* shard
  socket. Net effect: every watchdog trip leaves one extra live connection.

### Impact in production

Bot using `AutoClient` with a Conduit (2 shards), running for ~3 weeks. The number of stale-socket trips grew over
time (a feedback loop: more leaked connections → Twitch flakiness → more missed keepalives → more leaks):

| Date       | `"needs to close unexpectedly"` per day |
|------------|-----------------------------------------|
| 2026-10-27 | 15                                      |
| 2026-10-30 | 53                                      |
| 2026-11-01 | 254                                     |
| 2026-11-04 | 1,465                                   |
| 2026-11-05 | 2,552                                   |
| 2026-11-06 | 3,488                                   |

At the end of that period:

- exactly **1,000 distinct** Conduit websocket session ids were alive at once, each still receiving
  `session_keepalive` (~384/hour each, ~380k log lines per hour at DEBUG),
- new connections failed with `429` (`Invalid response status ... url='wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=10'`)
  and `Conduit Websocket "None" retrying to reconnect websocket connection in "N" seconds.` looped forever, so the bot
  stopped receiving chat events,
- the Python process held ~1 GB RSS (+~0.5 GB swapped out); a freshly started process with the same code is ~90 MB.
  Each leaked connection keeps its own `aiohttp.ClientSession`/connector, `Websocket` object and tasks alive.

Log signature (a `WARNING` with no `INFO "Successfully closed"` after it; compare with a healthy cycle):

```
# leaked: close() aborted
WARNING Conduit Websocket "AgoQUxaX..." needs to close unexpectedly. TwitchClient will attempt to reassociate this shard if possible.
INFO    Received "session_welcome" message from Conduit Websocket: "AgoQOAwJ..."
INFO    Associated shards with ConduitInfo(...) successfully. Shards: 2 / 2 (connected / Conduit total).

# healthy: close() completed
WARNING Conduit Websocket "AgoQOjsm..." needs to close unexpectedly. TwitchClient will attempt to reassociate this shard if possible.
INFO    Successfully closed Conduit Websocket: "AgoQOjsm..."
INFO    Received "session_welcome" message from Conduit Websocket: "AgoQq26G..."
```

> Honesty note for the maintainers: the minimal reproduction below proves the half-aborted `close()`
> (socket left open, `_closing` stuck). In the production process the leaked sockets were additionally *still being read*
> (keepalives logged by `_listen`), so there may be a second route (e.g. the `_listen` → `_create_connection_task` path) that
> also leaves connections behind. The fix below addresses the self-cancel; I have not been able to prove it is the only route.

## What did you expect to happen instead?

`close()` always runs to completion: the socket is closed, `_closing` is reset, `"Successfully closed"` is logged, and
the number of open connections for a shard stays at one. A stale shard should be replaced, not accumulated.

## Minimum reproducible example

Self-contained, uses a local fake EventSub server that sends `session_welcome` and then no keepalives
(so the watchdog fires after ~20 s). Needs only `aiohttp` and `twitchio`.

```python
import asyncio
import json
import sys

from aiohttp import web

import twitchio.eventsub.websockets as ws_mod

PORT = 18765
server_saw_close = asyncio.Event()


async def fake_eventsub(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_str(
        json.dumps(
            {
                "metadata": {"message_type": "session_welcome"},
                "payload": {
                    "session": {
                        "id": "SESSION1",
                        "keepalive_timeout_seconds": 10,
                        "connected_at": "2026-01-01T00:00:00.000000000Z",
                    }
                },
            }
        )
    )
    async for _ in ws:  # never send keepalives; just wait for the client
        pass
    server_saw_close.set()  # only reached if the client really closes the connection
    return ws


class StubClient:  # a Conduit shard needs a truthy client; only dispatch() is used
    def dispatch(self, *args, **kwargs) -> None:
        pass


if len(sys.argv) > 1 and sys.argv[1] == "patched":
    # Proposed fix: never cancel the task that is currently executing close().
    _orig_close = ws_mod.Websocket.close

    async def close(self, cleanup: bool = True, *, reassociate: bool = True) -> None:
        current = asyncio.current_task()
        for attr in ("_keep_alive_task", "_listen_task"):
            if getattr(self, attr) is current:
                setattr(self, attr, None)
        await _orig_close(self, cleanup, reassociate=reassociate)

    ws_mod.Websocket.close = close


async def main() -> None:
    app = web.Application()
    app.add_routes([web.get("/ws", fake_eventsub)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    ws_mod.WSS = f"ws://127.0.0.1:{PORT}/ws"

    ws = ws_mod.Websocket(client=StubClient(), http=None, shard_id="0")
    await ws.connect(fail_once=True)

    await asyncio.sleep(25)  # watchdog fires at the ~20 s check
    try:
        await asyncio.wait_for(server_saw_close.wait(), 3)
    except TimeoutError:
        pass

    print(f"close() ran to completion : {not ws._closing}   (expected True)")
    print(f"_socket released          : {ws._socket is None}   (expected True)")
    print(f"connection really closed  : {server_saw_close.is_set()}   (expected True)")
    sys.stdout.flush()
    import os

    os._exit(0)  # a leaked connection would otherwise keep the server runner alive


asyncio.run(main())
```

Output below is identical on twitchio 3.2.2 / aiohttp 3.13.5 (the version in production) and on the latest release
3.3.2 / aiohttp 3.14.5. `eventsub/websockets.py` on `main` @ 7340b19 is byte-identical to 3.2.2 apart from the copyright header:

```
$ python mre.py
Conduit Websocket "SESSION1" needs to close unexpectedly. <...StubClient...> will attempt to reassociate this shard if possible.
close() ran to completion : False   (expected True)
_socket released          : False   (expected True)
connection really closed  : False   (expected True)

$ python mre.py patched
Conduit Websocket "SESSION1" needs to close unexpectedly. <...StubClient...> will attempt to reassociate this shard if possible.
close() ran to completion : True   (expected True)
_socket released          : True   (expected True)
connection really closed  : True   (expected True)
```

## Steps to reproduce the problem

1. Run a Conduit-based bot (`AutoClient`/`Client` with a conduit, websocket transport) for a long time, or run the MRE above.
2. Let any shard socket miss a keepalive for longer than `keep_alive_timeout + 5` seconds (network stall, event-loop stall,
   Twitch hiccup). `_process_keepalive` then calls `_create_connection_task()` → `close()` from within `_keep_alive_task`.
3. Observe `needs to close unexpectedly` without a following `Successfully closed`, and the connection staying open.
4. Over days, open sessions accumulate until new `wss://eventsub.wss.twitch.tv/ws` connections are rejected with HTTP 429.

## Suggested fix

Smallest change: don't cancel the task that is running `close()`, in `Websocket.close()`:

```python
current = asyncio.current_task()

if self._keep_alive_task and self._keep_alive_task is not current:
    self._keep_alive_task.cancel()
...
if self._listen_task and self._listen_task is not current:
    self._listen_task.cancel()
```

(`_listen` → `_create_connection_task()` → `close()` has the same shape for `_listen_task`; there the cancel happens after the
last `await`, so it happens to be benign today, but guarding it is cheaper than reasoning about it.)

Defence in depth, either or both:

- catch `BaseException` around `await self._socket.close()` / use `try/finally` so `_closing` is always reset and the socket is
  always released even if `close()` is cancelled from outside;
- run the stale-socket handling in a separate task (`asyncio.create_task(self._create_connection_task())`) from
  `_process_keepalive`, as `_listen` effectively does for its own cleanup.

I verified both the "skip current task" variant (shown in the MRE as `patched`) and an application-side wrapper that runs
`_create_connection_task` in a shielded task; both make the MRE pass. Happy to open a PR with a regression test based on the MRE
if you prefer that.

## Additional logs

See the table and log signature above. DEBUG log of the leaked state (1,000 distinct sessions, all alive for a full hour):

```
$ grep session_keepalive bot.debug.log | grep -oE 'Websocket: "[^"]+"' | sort -u | wc -l
1000
...
DEBUG Failed to connect to Conduit Websocket "None>"": 429, message='Invalid response status', url='wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=10'.
INFO  Conduit Websocket "None" retrying to reconnect websocket connection in "90" seconds.
```

## System and Environment information

(`python -m twitchio --version` failed in my environment with `FileNotFoundError: 'twitchio/__init__.py'`, so these values are from the lockfile/runtime. Fill in the real output before filing if it works for you.)

```
twitchio 3.2.2 (production) and 3.3.2 (latest release) — bug reproduces on both; main @ 7340b19 has the same websockets.py
aiohttp 3.13.5 and 3.14.5
Python 3.13.14 (production), 3.13.5 (MRE runs)
Ubuntu 26.04 (Linux 7.0), x86_64
```

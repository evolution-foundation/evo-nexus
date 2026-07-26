"""Serialize all frame writes on a simple_websocket connection and disable
permessage-deflate to prevent stateful compressor corruption.

flask-sock hands each WebSocket route a ``simple_websocket.ws.Server``
instance (subclass of ``simple_websocket.ws.Base``).  That class has TWO
threads that write the same socket with NO synchronization:

  1. the application thread(s) calling ``.send()`` / ``.close()``; and
  2. simple-websocket's own ``Base._thread``, which replies to client Pings
     with a Pong, handles the handshake Accept, and sends Close frames via
     ``_handle_events()``.

The additional hazard is permessage-deflate.  ``_handle_events`` always
accepts the ``PerMessageDeflate`` extension offered by modern browsers,
which activates a stateful zlib compressor shared by both threads.  Any
concurrent call to ``ws.send()`` from the application thread corrupts the
compressor context, producing frames where RSV1=1 (compressed) but the
payload is invalid.  Chrome surfaces this as:

    WebSocket connection failed: Invalid frame header

The lock alone is not enough because ``_thread`` calls
``ws.receive_data()`` — which also touches the wsproto state machine —
*before* calling ``_handle_events()``.  If ``send()`` races against that
window the connection state can become inconsistent.

Fix (two parts, both applied here):

1. **Replace ``_handle_events``** with a copy that passes ``extensions=[]``
   to ``AcceptConnection``, preventing deflate negotiation entirely.  The
   replacement also runs under the per-instance write lock, so the wsproto
   state machine (``self.ws``) is only mutated by one thread at a time.

2. **Wrap ``send``/``close``** with the same per-instance lock so the pump
   thread and the internal ``_thread`` cannot write to the socket in parallel.

Idempotent.  Import-guarded: no-op if the lib is absent / renamed.
"""

from __future__ import annotations

import threading

_INSTALLED = False


def install() -> None:
    """Monkeypatch simple_websocket.ws.Base to serialize writes and kill deflate.

    Call once at startup, BEFORE any WebSocket route runs (i.e. before the
    ``flask_sock.Sock`` instance is created).  No-op if already patched or if
    simple_websocket is not importable.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    try:
        from simple_websocket.ws import Base
    except Exception:  # lib missing/renamed: leave as-is
        return

    if getattr(Base, "_evonexus_send_lock_installed", False):
        _INSTALLED = True
        return

    try:
        from wsproto.events import (
            AcceptConnection,
            CloseConnection,
            Ping,
            Pong,
            Request,
            TextMessage,
            BytesMessage,
        )
        from wsproto.frame_protocol import CloseReason
        from wsproto.utilities import LocalProtocolError
    except Exception:
        return

    _orig_send = Base.send
    _orig_close = Base.close

    def _lock_for(self) -> threading.RLock:
        lock = getattr(self, "_evonexus_send_lock", None)
        if lock is None:
            with Base._evonexus_lock_create_lock:
                lock = getattr(self, "_evonexus_send_lock", None)
                if lock is None:
                    lock = threading.RLock()
                    self._evonexus_send_lock = lock
        return lock

    def send(self, data):
        with _lock_for(self):
            return _orig_send(self, data)

    def close(self, reason=None, message=None):
        with _lock_for(self):
            return _orig_close(self, reason=reason, message=message)

    def _handle_events(self):
        # Exact reimplementation of Base._handle_events with two changes:
        #   - extensions=[] in AcceptConnection (no permessage-deflate)
        #   - entire method runs under the per-instance write lock
        with _lock_for(self):
            keep_going = True
            out_data = b''
            for event in self.ws.events():
                try:
                    if isinstance(event, Request):
                        self.subprotocol = self.choose_subprotocol(event)
                        out_data += self.ws.send(AcceptConnection(
                            subprotocol=self.subprotocol,
                            extensions=[]))
                    elif isinstance(event, CloseConnection):
                        if self.is_server:
                            out_data += self.ws.send(event.response())
                        self.close_reason = event.code
                        self.close_message = event.reason
                        self.connected = False
                        self.event.set()
                        keep_going = False
                    elif isinstance(event, Ping):
                        out_data += self.ws.send(event.response())
                    elif isinstance(event, Pong):
                        self.pong_received = True
                    elif isinstance(event, (TextMessage, BytesMessage)):
                        self.incoming_message_len += len(event.data)
                        if self.max_message_size and \
                                self.incoming_message_len > self.max_message_size:
                            out_data += self.ws.send(CloseConnection(
                                CloseReason.MESSAGE_TOO_BIG, 'Message is too big'))
                            self.event.set()
                            keep_going = False
                            break
                        if self.incoming_message is None:
                            self.incoming_message = event.data
                        elif isinstance(event, TextMessage):
                            if not isinstance(self.incoming_message, bytearray):
                                self.incoming_message = bytearray(
                                    (self.incoming_message + event.data).encode())
                            else:
                                self.incoming_message += event.data.encode()
                        else:
                            if not isinstance(self.incoming_message, bytearray):
                                self.incoming_message = bytearray(
                                    self.incoming_message + event.data)
                            else:
                                self.incoming_message += event.data
                        if not event.message_finished:
                            continue
                        if isinstance(self.incoming_message, (str, bytes)):
                            self.input_buffer.append(self.incoming_message)
                        elif isinstance(event, TextMessage):
                            self.input_buffer.append(
                                self.incoming_message.decode())
                        else:
                            self.input_buffer.append(bytes(self.incoming_message))
                        self.incoming_message = None
                        self.incoming_message_len = 0
                        self.event.set()
                    else:
                        pass
                except LocalProtocolError:
                    out_data = b''
                    self.event.set()
                    keep_going = False
            if out_data:
                self.sock.send(out_data)
            return keep_going

    Base._evonexus_lock_create_lock = threading.Lock()
    Base.send = send
    Base.close = close
    Base._handle_events = _handle_events
    Base._evonexus_send_lock_installed = True
    _INSTALLED = True

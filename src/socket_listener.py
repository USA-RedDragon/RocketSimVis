import socket

import state_manager
import json

import time
import traceback
from collections import deque
from threading import Lock

class SocketListener:
    def __init__(self):
        self.has_received: bool = False
        self.buffer_size: int = 1024 * 1024
        self.should_run = True
        self.actual_port: int = 0
        self.connection_closed: bool = False  # Set when stream (socketpair/TCP) reaches EOF
        # Headless buffering: when enabled, parsed JSON dicts are queued
        # so the render loop can consume them one at a time
        self._headless_buffer = False
        self._state_queue: deque = deque()
        self._queue_lock = Lock()

    def enable_headless_buffer(self):
        """Enable buffered mode for headless recording.
        States are queued instead of overwritten, so none are lost."""
        self._headless_buffer = True

    def pop_state(self):
        """Pop the next buffered state dict, or None if empty."""
        with self._queue_lock:
            if self._state_queue:
                return self._state_queue.popleft()
            return None

    def queue_size(self):
        with self._queue_lock:
            return len(self._state_queue)

    def run(self, bind_addr: str, port_num: int):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind((bind_addr, port_num))
        sock.settimeout(0.5)
        # Get the actual port (important when port_num=0 for random assignment)
        self.actual_port = sock.getsockname()[1]
        print("Created socket on {}:{}, listening...".format(bind_addr, self.actual_port))
        prev_recv_time = time.time()
        while self.should_run:
            try:
                data, addr = sock.recvfrom(self.buffer_size)
            except:
                continue

            self.has_received = True

            try:
                j = json.loads(data.decode("utf-8"))
            except json.decoder.JSONDecodeError as err:
                print("ERROR parsing received text to JSON:", err)

                view_range = 10
                start, stop = max(0, err.pos - view_range), min(err.pos + view_range, len(err.doc) - 1)
                snippet = err.doc[start:stop].replace('\r', '').replace('\n', ' ')
                snippet_prefix = "Received JSON: "
                underline = (' ' * (len(snippet)//2 + len(snippet_prefix))) + '^ HERE'
                print("\t" + snippet_prefix + snippet)
                print("\t" + underline)
                j = None

            if not (j is None):
                recv_time = time.time()

                if self._headless_buffer:
                    # In headless mode, queue the raw JSON for the render loop
                    # to consume one at a time
                    with self._queue_lock:
                        self._state_queue.append((j, recv_time, recv_time - prev_recv_time))
                else:
                    with state_manager.global_state_mutex:
                        try:
                            state_manager.global_state_manager.state.read_from_json(j)
                        except:
                            print("ERROR reading received JSON:")
                            traceback.print_exc()

                        state_manager.global_state_manager.state.recv_time = recv_time
                        state_manager.global_state_manager.state.recv_interval = recv_time - prev_recv_time

                prev_recv_time = recv_time

    def stop_async(self):
        self.should_run = False

    def _recv_exactly(self, sock, n):
        """Read exactly n bytes from a stream socket, or return None on EOF/error."""
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except socket.timeout:
                if not self.should_run:
                    return None
                continue
            except OSError:
                return None
            if not chunk:
                return None  # EOF — sender closed the connection
            buf.extend(chunk)
        return bytes(buf)

    def run_from_fd(self, fd: int):
        """Read length-prefixed JSON messages from an inherited socketpair fd.
        Each message is a 4-byte big-endian length followed by that many bytes of JSON."""
        import struct
        import os
        sock = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
        os.close(fd)  # fromfd() duplicated the fd; close the original
        sock.settimeout(0.5)
        print(f"Listening on inherited socketpair fd {fd}...")
        self._run_stream_loop(sock, f"socketpair fd {fd}")

    def run_from_tcp(self):
        """Listen on a TCP port on localhost, accept one connection, and read
        length-prefixed JSON frames.  Prints the chosen port to stdout so the
        parent C++ process can connect."""
        import struct
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.actual_port = srv.getsockname()[1]
        # The parent reads this line on stdout to know which port to connect to
        print(f"TCP_PORT:{self.actual_port}", flush=True)
        print(f"TCP listener waiting for connection on 127.0.0.1:{self.actual_port}...",
              file=__import__('sys').stderr, flush=True)
        srv.settimeout(30.0)  # Wait up to 30s for the C++ side to connect
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            print("ERROR: No TCP connection received within 30 seconds, giving up.",
                  file=__import__('sys').stderr, flush=True)
            srv.close()
            return
        srv.close()
        conn.settimeout(0.5)
        # Increase receive buffer
        try:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except OSError:
            pass
        print(f"TCP connection accepted from {addr}",
              file=__import__('sys').stderr, flush=True)
        self._run_stream_loop(conn, f"TCP {addr}")

    def _run_stream_loop(self, sock, label: str):
        """Shared loop for reading length-prefixed JSON from a SOCK_STREAM socket."""
        import struct
        prev_recv_time = time.time()
        while self.should_run:
            # Read 4-byte length header
            hdr = self._recv_exactly(sock, 4)
            if hdr is None:
                if not self.should_run:
                    break
                # EOF from parent — treat as shutdown
                print(f"{label} EOF, shutting down listener.", flush=True)
                break

            (msg_len,) = struct.unpack("!I", hdr)
            if msg_len == 0:
                continue
            if msg_len > self.buffer_size:
                print(f"WARNING: message too large ({msg_len} bytes), skipping")
                # drain it
                self._recv_exactly(sock, msg_len)
                continue

            data = self._recv_exactly(sock, msg_len)
            if data is None:
                break

            self.has_received = True
            try:
                j = json.loads(data.decode("utf-8"))
            except json.decoder.JSONDecodeError as err:
                print("ERROR parsing received JSON:", err)
                j = None

            if j is not None:
                recv_time = time.time()

                if self._headless_buffer:
                    with self._queue_lock:
                        self._state_queue.append((j, recv_time, recv_time - prev_recv_time))
                else:
                    with state_manager.global_state_mutex:
                        try:
                            state_manager.global_state_manager.state.read_from_json(j)
                        except:
                            print("ERROR reading received JSON:")
                            traceback.print_exc()

                        state_manager.global_state_manager.state.recv_time = recv_time
                        state_manager.global_state_manager.state.recv_interval = recv_time - prev_recv_time

                prev_recv_time = recv_time

        sock.close()
        self.connection_closed = True
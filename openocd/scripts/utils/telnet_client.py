#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file telnet_client.py
#
# @section Description
# Script for telnet related operations
#

import time
import socket
from utils.debug_context import logger, g_dbgContext

class TelnetClient:
    """
    @brief Telnet client for communicating with OpenOCD debug server.

    @details Manages telnet connection lifecycle to an OpenOCD debug server,
    handles command transmission, and receives responses with a
    configurable connection timeout.

    NOTE:
    This client uses a raw TCP socket (not telnetlib). Since OpenOCD's port 4444
    is a Telnet server, it may send Telnet "option negotiation" bytes on connect.
    We filter those bytes so they don't appear as junk characters in logs.
    """

    DEFAULT_TIMEOUT = 5
    DEFAULT_BANNER_DELAY = 0.5
    DEFAULT_BUFFER_SIZE = 4096

    LOCAL_HOST = "127.0.0.1"
    PORT = 4444

    # Telnet protocol bytes (RFC 854 style)
    IAC  = 255  # "Interpret As Command"
    DONT = 254
    DO   = 253
    WONT = 252
    WILL = 251
    SB   = 250
    SE   = 240

    def __init__(self, host=None, port=PORT, timeout=DEFAULT_TIMEOUT,
                 banner_delay=DEFAULT_BANNER_DELAY, auto_connect=True):
        """
        @brief Construct a TelnetClient.

        @param host          Defaults to the configured GDB_REMOTE_HOST
                              (see debug_context.getGdbRemoteHost()), so this
                              client targets whichever machine OpenOCD is
                              running on -- "localhost" unless configured
                              otherwise.
        @param auto_connect  If True (default), connects immediately so
                              "telnet = TelnetClient()" is usable right
                              away without a separate connect() call.
                              "with TelnetClient() as telnet:" still works
                              the same as before -- __enter__ skips
                              reconnecting if already connected, and
                              __exit__ closes the connection either way.
        """
        self.host = host if host is not None else g_dbgContext.getGdbRemoteHost()
        self.port = port
        self.timeout = timeout
        self.banner_delay = banner_delay
        self.sock = None
        if auto_connect:
            self.connect()

    def connect(self):
        """
        @brief Establish connection to telnet server.
        """
        try:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout)
            logger.info(f"Connected to {self.host}:{self.port}")

            # Drain initial negotiation/banner so it doesn't mix into first command output
            _ = self.read_banner()

        except socket.timeout:
            logger.error(f"Error: Connection timeout to {self.host}:{self.port}")
            raise
        except ConnectionRefusedError:
            logger.error(f"Error: Connection refused by {self.host}:{self.port}")
            raise

    def filter_telnet_bytes_and_reply(self, data: bytes) -> bytes:
        """
        @brief Remove telnet negotiation bytes from incoming stream.
        Also replies to DO/WILL with WONT/DONT (polite refusal), so server
        stops negotiating.

        @return "cooked" bytes (only printable output)
        """
        if not data:
            return b""

        out = bytearray()
        i = 0
        n = len(data)

        while i < n:
            b = data[i]

            if b == self.IAC:
                # Need at least 2 more bytes for IAC <cmd> <opt>
                if i + 1 >= n:
                    break

                cmd = data[i + 1]

                # Handle subnegotiation: IAC SB ... IAC SE
                if cmd == self.SB:
                    # Skip until IAC SE
                    i += 2
                    while i < n:
                        if data[i] == self.IAC and i + 1 < n and data[i + 1] == self.SE:
                            i += 2
                            break
                        i += 1
                    continue

                # Normal negotiation: IAC DO/DONT/WILL/WONT <option>
                if i + 2 < n:
                    opt = data[i + 2]

                    # Politely refuse all negotiations (safe default for OpenOCD telnet)
                    try:
                        if cmd == self.DO:
                            self.sock.sendall(bytes([self.IAC, self.WONT, opt]))
                        elif cmd == self.WILL:
                            self.sock.sendall(bytes([self.IAC, self.DONT, opt]))
                    except Exception:
                        # If we can't reply, just keep filtering
                        pass

                    i += 3
                    continue

                # Incomplete sequence
                break

            # Normal data byte
            out.append(b)
            i += 1

        return bytes(out)

    def read_with_timeout(self, timeout=0.2):
        """Helper method to read data with temporary timeout."""
        if self.sock is None:
            raise RuntimeError("TelnetClient not connected -- call connect() first")

        original_timeout = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        
        chunks = []
        try:
            while True:
                try:
                    raw = self.sock.recv(self.DEFAULT_BUFFER_SIZE)
                    if not raw:
                        break
                    cooked = self.filter_telnet_bytes_and_reply(raw)
                    if cooked:
                        chunks.append(cooked)
                except socket.timeout:
                    break
        finally:
            self.sock.settimeout(original_timeout)
    
        return b"".join(chunks)

    def read_banner(self):
        """
        @brief Receive complete response from server after command.

        Reads all available data, filters telnet negotiation bytes,
        returns printable response.
        """
        time.sleep(self.banner_delay)
        try:
            data = self.read_with_timeout(0.2)
            return data.decode("utf-8", errors="ignore").strip()
        except Exception as e:
            logger.warning(f"Warning: Failed to read banner: {e}")
            return ""   
    
    def send_command(self, command: str):
        """
        @brief Send a single command to the server.
        """
        if self.sock is None:
            raise RuntimeError("TelnetClient not connected -- call connect() first")

        try:
            self.sock.sendall((command + "\n").encode("utf-8"))
            response = self.read_with_timeout(0.2)
        except Exception as e:
            logger.error(f"Error sending command: {e}")
            raise

        return response

    def close(self):
        """
        @brief Close the telnet connection.
        """
        if self.sock:
            try:
                self.sock.close()
                logger.info("Telnet connection closed")
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
            self.sock = None

    def __enter__(self):
        if self.sock is None:
            self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


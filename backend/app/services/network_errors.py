"""Type-only, credential-free network failure evidence shared by probes and Bot calls."""
import errno
import socket
import ssl

from aiohttp import (
    ClientConnectorError,
    ClientConnectorSSLError,
    ClientHttpProxyError,
    ClientProxyConnectionError,
    ConnectionTimeoutError,
    SocketTimeoutError,
)
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from python_socks import ProxyConnectionError as SocksConnectionError
from python_socks import ProxyError as SocksError
from python_socks import ProxyTimeoutError as SocksTimeoutError


def classify_network_failure(exc: BaseException) -> tuple[str, str]:
    """Keep unknown phases unknown; never inspect arbitrary exception messages.

    SDKs wrap transport errors. Walk a bounded, cycle-safe cause/context graph,
    retaining explicit proxy/TLS evidence ahead of generic connection wrappers.
    """
    pending = [exc]
    chain: list[BaseException] = []
    seen: set[int] = set()
    while pending and len(chain) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None and not current.__suppress_context__:
            pending.append(current.__context__)
        if isinstance(current, ClientConnectorError):
            pending.append(current.os_error)
    for error in chain:
        if isinstance(error, (ssl.SSLError, ClientConnectorSSLError)):
            return "tls", "tls"
        if isinstance(error, ClientHttpProxyError) and error.status == 407:
            return "proxy_auth", "proxy"
        if isinstance(error, (ProxyError, SocksError)) and error.error_code == 407:
            return "proxy_auth", "proxy"
    for error in chain:
        if isinstance(error, (ProxyTimeoutError, SocksTimeoutError)):
            return "timeout", "proxy"
        if isinstance(error, (ProxyConnectionError, SocksConnectionError, ClientProxyConnectionError)):
            return "proxy_connection", "proxy"
        if isinstance(error, (ProxyError, SocksError, ClientHttpProxyError)):
            # SOCKS auth/remote DNS often have no structured code. Do not guess.
            return "network_unknown", "proxy"
    for error in chain:
        if isinstance(error, socket.gaierror):
            return "dns", "dns"
        if isinstance(error, ConnectionTimeoutError):
            return "timeout", "connect"
        if isinstance(error, SocketTimeoutError):
            return "timeout", "response"
    for error in chain:
        if isinstance(error, TimeoutError):
            return "timeout", "unknown"
        if isinstance(error, ClientConnectorError) or (
                isinstance(error, OSError) and error.errno in {
                    errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH,
                }):
            return "tcp", "connect"
    return "network_unknown", "unknown"

# HTTP server basics shared by the UI handler.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate",
              "proxy-authorization", "te", "trailer", "trailers",
              "transfer-encoding", "upgrade"}


AGENTFORGE_PREFIX = "/__agentforge"


class _UIServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that does not shout when a browser hangs up."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            BrokenPipeError, TimeoutError)):
            log.debug(f"client went away: {type(exc).__name__}")
            return
        super().handle_error(request, client_address)

"""Facefold launcher: starts the local server and opens the browser.

    python calistir.py              # start and open the browser
    python calistir.py --port 9000  # a different port
    python calistir.py --no-browser # do not open a browser

Binds to 127.0.0.1 only, so nothing is reachable from the network.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser

HOST = "127.0.0.1"
DEFAULT_PORT = 8760

# Plain ASCII on purpose. This prints into a Windows console whose code page
# is whatever the user's Windows was installed with, and box-drawing or
# accented characters come out as mojibake there.
BANNER = r"""
  ========================================
    F A C E F O L D
  ========================================

  Sorts your photos into folders by who is in them.
  Nothing is ever sent to the internet.
"""


def free_port(start: int, tries: int = 20) -> int:
    """Find a usable port so a second instance does not just crash."""
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
                return port
            except OSError:
                continue
    raise SystemExit("No free port found (%d-%d)." % (start, start + tries))


def main() -> int:
    parser = argparse.ArgumentParser(description="Facefold")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    try:
        from facefold.web import create_app
    except ImportError as exc:
        print("Missing packages: %s" % exc)
        print("Run 'python -m pip install -r requirements.txt' first.")
        return 1

    port = args.port if args.debug else free_port(args.port)
    url = "http://%s:%d/" % (HOST, port)

    print(BANNER)
    print("  Address: %s" % url)
    print("  Press Ctrl+C in this window to stop\n")

    app = create_app()

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=HOST, port=port, debug=args.debug, threaded=True,
                use_reloader=args.debug)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Start the local pilot and open its interface on this computer."""
import sys
import threading
import webbrowser

from server import create_server


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("Χρειάζεται Python 3.12 ή νεότερη.")
    try:
        server = create_server("127.0.0.1", 8000)
    except OSError as exc:
        raise SystemExit("Δεν ήταν δυνατή η εκκίνηση. Έλεγξε αν η θύρα 8000 χρησιμοποιείται ήδη.") from exc
    opener = threading.Timer(0.7, webbrowser.open, args=("http://127.0.0.1:8000",))
    opener.daemon = True
    opener.start()
    print("Η δοκιμαστική εφαρμογή ανοίγει στον browser αυτού του υπολογιστή.", flush=True)
    print("Κράτησε αυτό το παράθυρο ανοιχτό. Για διακοπή πάτησε Ctrl+C.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

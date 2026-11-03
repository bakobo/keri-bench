"""A stand-in role process for tests/test_procs.py.

Usage: ``fake_role.py MODE [--delay S] [--port P] [--bind-delay S] [--ignore-term]
[--exit-after S --exit-code N]``

MODE says what the one announcement line is: ``ok`` prints ``{"url": ..., "pid": ...}``,
``garbage`` prints a line that is not JSON, ``list`` prints a JSON array, ``silent`` prints
nothing and sleeps, and ``exit`` prints nothing and exits. With ``--port`` the role serves a
trivial HTTP endpoint on that port, binding ``--bind-delay`` seconds after it announces;
without it the role just sleeps. With ``--exit-after`` it
exits with ``--exit-code`` that many seconds after announcing, as a role that dies would. Its pid is written to stderr first, so a test can find a role
that never announced.
"""

import argparse
import http.server
import json
import os
import signal
import sys
import time


class Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["ok", "garbage", "list", "silent", "exit"])
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--port", type=int)
    parser.add_argument("--bind-delay", type=float, default=0.0)
    parser.add_argument("--ignore-term", action="store_true")
    parser.add_argument("--chatter", type=int, default=0,
                        help="bytes to write to stdout after announcing, then a marker file")
    parser.add_argument("--marker")
    parser.add_argument("--exit-after", type=float,
                        help="seconds after announcing to exit on its own, with --exit-code")
    parser.add_argument("--exit-code", type=int, default=0)
    opts = parser.parse_args()
    if opts.ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print(f"pid {os.getpid()}", file=sys.stderr, flush=True)
    time.sleep(opts.delay)
    if opts.mode == "exit":
        sys.exit(3)
    url = f"http://127.0.0.1:{opts.port}/" if opts.port else None
    lines = {"ok": json.dumps({"url": url, "pid": os.getpid()}), "garbage": "ready, probably",
             "list": "[1, 2]"}
    if opts.mode in lines:
        print(lines[opts.mode], flush=True)
    if opts.chatter:
        sys.stdout.write("x" * opts.chatter + "\n")
        sys.stdout.flush()
        with open(opts.marker, "w") as done:
            done.write("done")
    if opts.exit_after is not None:
        time.sleep(opts.exit_after)
        os._exit(opts.exit_code)
    if opts.port and opts.mode == "ok":
        time.sleep(opts.bind_delay)
        http.server.HTTPServer(("127.0.0.1", opts.port), Ok).serve_forever()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()

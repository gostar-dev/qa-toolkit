"""연습용 서버 실행.

  python -m apiqa serve --port 8080              정상 서버
  python -m apiqa serve --port 8080 --bug idem_dup   결함 하나를 심은 서버
  python -m apiqa bugs                           심을 수 있는 결함 목록
"""
import argparse
import sys

from .server import BUGS, make_server


def main(argv=None):
    p = argparse.ArgumentParser(prog="apiqa")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--bug", choices=sorted(BUGS))
    sub.add_parser("bugs")
    a = p.parse_args(argv)
    if a.cmd == "bugs":
        for k, v in BUGS.items():
            print(f"{k:15s} {v}")
        return 0
    srv = make_server(port=a.port, bug=a.bug)
    print(f"http://127.0.0.1:{a.port} 에서 실행 중" + (f" (심은 결함: {a.bug})" if a.bug else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

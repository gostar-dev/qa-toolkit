"""테스트 대상으로 쓰는 가짜 메시지 플랫폼 서버 (표준 라이브러리만 사용).

실제 서비스가 아니라, API 테스트 하네스를 검증하기 위해 만든 연습용 서버다.
대화방에 메시지를 보내고, 목록을 페이지로 읽고, 보낸 메시지를 취소하는 API를 제공한다.

  POST   /v1/rooms/{room}/messages      메시지 전송 (Idempotency-Key 헤더 지원)
  GET    /v1/rooms/{room}/messages      목록 조회 (limit, cursor)
  GET    /v1/messages/{id}              단건 조회
  DELETE /v1/messages/{id}              전송 취소 (보낸 사람만)
  GET    /health                        상태 확인

bug 인자로 결함을 하나 심을 수 있다. 하네스가 그 결함을 잡는지 확인하는 용도다.
"""
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_TEXT = 1000
DEFAULT_LIMIT, MAX_LIMIT = 20, 100

TOKENS = {"tok-alice": "alice", "tok-bob": "bob", "tok-carol": "carol"}
ROOMS = {"r1": {"alice", "bob"}, "r2": {"bob", "carol"}}

BUGS = {
    "idem_dup": "같은 Idempotency-Key 재전송을 새 메시지로 저장",
    "page_skip": "다음 페이지 커서를 하나 건너뛰어 계산",
    "auth_bypass": "방 멤버가 아니어도 목록 조회 허용",
    "len_boundary": "정확히 1000자인 메시지를 거부 (> 대신 >=)",
    "unsent_visible": "취소한 메시지가 목록에 계속 보임",
    "schema_drop": "단건 조회 응답에서 created_at 누락",
    "race_seq": "순번 발급에 잠금이 없어 동시 전송 시 순번 중복",
}


class Store:
    def __init__(self, bug=None):
        self.bug = bug
        self.lock = threading.Lock()
        self.messages = {}      # id -> message dict (+ _unsent)
        self.seq = 0
        self.idem = {}          # (user, key) -> message id

    def next_seq(self):
        if self.bug == "race_seq":
            cur = self.seq
            time.sleep(0.003)
            self.seq = cur + 1
            return self.seq
        self.seq += 1
        return self.seq

    def create(self, room, user, text, key):
        if self.bug == "race_seq":
            return self._create(room, user, text, key)
        with self.lock:
            return self._create(room, user, text, key)

    def _create(self, room, user, text, key):
        if key and self.bug != "idem_dup" and (user, key) in self.idem:
            return self.messages[self.idem[(user, key)]], True
        msg = {
            "id": uuid.uuid4().hex[:12],
            "room_id": room,
            "sender": user,
            "text": text,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "seq": self.next_seq(),
        }
        self.messages[msg["id"]] = dict(msg, _unsent=False)
        if key:
            self.idem[(user, key)] = msg["id"]
        return self.messages[msg["id"]], False

    def listing(self, room, limit, cursor):
        with self.lock:
            rows = sorted((m for m in self.messages.values() if m["room_id"] == room
                           and (self.bug == "unsent_visible" or not m["_unsent"])), key=lambda m: m["seq"])
        start = 0
        if cursor is not None:
            start = next((i for i, m in enumerate(rows) if m["seq"] > cursor), len(rows))
        page = rows[start:start + limit]
        nxt = None
        if start + limit < len(rows) and page:
            nxt = page[-1]["seq"] + (1 if self.bug == "page_skip" else 0)
        return page, nxt


def public(msg, drop=None):
    out = {k: v for k, v in msg.items() if not k.startswith("_")}
    if drop:
        out.pop(drop, None)
    return out


class Handler(BaseHTTPRequestHandler):
    store: Store = None

    def log_message(self, *args):
        pass

    # ---------- 공통 ----------
    def send_json(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, status, code, message):
        self.send_json(status, {"error": {"code": code, "message": message}})

    def user(self):
        auth = self.headers.get("Authorization", "")
        return TOKENS.get(auth[7:]) if auth.startswith("Bearer ") else None

    # ---------- 라우팅 ----------
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self.send_json(200, {"status": "ok"})
        user = self.user()
        if not user:
            return self.error(401, "UNAUTHORIZED", "토큰이 없거나 잘못됨")
        m = re.fullmatch(r"/v1/rooms/([\w-]+)/messages", url.path)
        if m:
            return self.list_messages(m.group(1), user, parse_qs(url.query))
        m = re.fullmatch(r"/v1/messages/(\w+)", url.path)
        if m:
            return self.get_message(m.group(1), user)
        self.error(404, "NOT_FOUND", "없는 경로")

    def do_POST(self):
        url = urlparse(self.path)
        user = self.user()
        if not user:
            return self.error(401, "UNAUTHORIZED", "토큰이 없거나 잘못됨")
        m = re.fullmatch(r"/v1/rooms/([\w-]+)/messages", url.path)
        if not m:
            return self.error(404, "NOT_FOUND", "없는 경로")
        self.send_message(m.group(1), user)

    def do_DELETE(self):
        url = urlparse(self.path)
        user = self.user()
        if not user:
            return self.error(401, "UNAUTHORIZED", "토큰이 없거나 잘못됨")
        m = re.fullmatch(r"/v1/messages/(\w+)", url.path)
        if not m:
            return self.error(404, "NOT_FOUND", "없는 경로")
        self.unsend(m.group(1), user)

    # ---------- 기능 ----------
    def send_message(self, room, user):
        if room not in ROOMS:
            return self.error(404, "ROOM_NOT_FOUND", "없는 대화방")
        if user not in ROOMS[room]:
            return self.error(403, "FORBIDDEN", "대화방 멤버가 아님")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except json.JSONDecodeError:
            return self.error(400, "INVALID_JSON", "JSON 형식 오류")
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return self.error(400, "INVALID_TEXT", "text는 비어 있지 않은 문자열이어야 함")
        too_long = len(text) >= MAX_TEXT if self.store.bug == "len_boundary" else len(text) > MAX_TEXT
        if too_long:
            return self.error(400, "TEXT_TOO_LONG", f"text는 {MAX_TEXT}자 이하")
        key = self.headers.get("Idempotency-Key")
        msg, replayed = self.store.create(room, user, text, key)
        if replayed:
            return self.send_json(200, public(msg), {"Idempotent-Replayed": "true"})
        self.send_json(201, public(msg))

    def list_messages(self, room, user, q):
        if room not in ROOMS:
            return self.error(404, "ROOM_NOT_FOUND", "없는 대화방")
        if user not in ROOMS[room] and self.store.bug != "auth_bypass":
            return self.error(403, "FORBIDDEN", "대화방 멤버가 아님")
        try:
            limit = int(q.get("limit", [DEFAULT_LIMIT])[0])
            cursor = int(q["cursor"][0]) if "cursor" in q else None
        except ValueError:
            return self.error(400, "INVALID_PARAM", "limit, cursor는 정수")
        if not 1 <= limit <= MAX_LIMIT:
            return self.error(400, "INVALID_LIMIT", f"limit은 1~{MAX_LIMIT}")
        page, nxt = self.store.listing(room, limit, cursor)
        self.send_json(200, {"items": [public(m) for m in page], "next_cursor": nxt})

    def get_message(self, mid, user):
        msg = self.store.messages.get(mid)
        if not msg:
            return self.error(404, "MESSAGE_NOT_FOUND", "없는 메시지")
        if user not in ROOMS[msg["room_id"]]:
            return self.error(403, "FORBIDDEN", "대화방 멤버가 아님")
        if msg["_unsent"]:
            return self.error(410, "MESSAGE_UNSENT", "취소된 메시지")
        drop = "created_at" if self.store.bug == "schema_drop" else None
        self.send_json(200, public(msg, drop))

    def unsend(self, mid, user):
        with self.store.lock:
            msg = self.store.messages.get(mid)
            if not msg:
                return self.error(404, "MESSAGE_NOT_FOUND", "없는 메시지")
            if msg["sender"] != user:
                return self.error(403, "FORBIDDEN", "보낸 사람만 취소 가능")
            if msg["_unsent"]:
                return self.error(410, "MESSAGE_UNSENT", "이미 취소된 메시지")
            msg["_unsent"] = True
        self.send_json(204)


class Server(ThreadingHTTPServer):
    request_queue_size = 128   # 동시 요청 테스트에서 연결이 끊기지 않도록 대기열을 늘림
    daemon_threads = True


def make_server(host="127.0.0.1", port=0, bug=None):
    """서버를 만들어 돌려준다. port=0이면 비어 있는 포트를 자동으로 쓴다."""
    if bug and bug not in BUGS:
        raise ValueError(f"알 수 없는 bug: {bug}")
    handler = type("BoundHandler", (Handler,), {"store": Store(bug)})
    return Server((host, port), handler)


def start_in_thread(bug=None):
    """테스트용: 백그라운드 스레드로 서버를 띄우고 (server, base_url)을 돌려준다."""
    srv = make_server(bug=bug)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    return srv, f"http://{host}:{port}"

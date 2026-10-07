"""API 테스트 하네스의 호출부와 응답 검증부 (표준 라이브러리만 사용).

ApiClient   요청을 보내고 상태 코드, 헤더, 본문을 그대로 돌려준다. 오류 응답도 예외 없이 돌려준다.
check_schema  응답 본문이 계약(필드 이름과 타입)에 맞는지 확인하고, 어긋난 점을 목록으로 돌려준다.
"""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

MESSAGE_SCHEMA = {"id": str, "room_id": str, "sender": str, "text": str, "created_at": str, "seq": int}
PAGE_SCHEMA = {"items": list, "next_cursor": (int, type(None))}
ERROR_SCHEMA = {"error": dict}


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    body: object = None


class ApiClient:
    def __init__(self, base_url, token=None, timeout=5):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def as_user(self, token):
        return ApiClient(self.base, token, self.timeout)

    def request(self, method, path, json_body=None, headers=None, raw_body=None):
        hdrs = dict(headers or {})
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        data = raw_body
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return self._wrap(r.status, r.headers, r.read())
        except urllib.error.HTTPError as e:
            return self._wrap(e.code, e.headers, e.read())

    @staticmethod
    def _wrap(status, headers, raw):
        body = json.loads(raw) if raw else None
        return Response(status, {k.lower(): v for k, v in headers.items()}, body)

    # 자주 쓰는 호출
    def send(self, room, text, key=None):
        return self.request("POST", f"/v1/rooms/{room}/messages", {"text": text},
                            {"Idempotency-Key": key} if key else None)

    def list(self, room, limit=None, cursor=None):
        q = [f"limit={limit}"] if limit is not None else []
        if cursor is not None:
            q.append(f"cursor={cursor}")
        return self.request("GET", f"/v1/rooms/{room}/messages" + ("?" + "&".join(q) if q else ""))

    def get(self, mid):
        return self.request("GET", f"/v1/messages/{mid}")

    def unsend(self, mid):
        return self.request("DELETE", f"/v1/messages/{mid}")

    def list_all(self, room, limit):
        """커서를 따라가며 전체 목록을 모은다. 페이지마다 응답을 같이 돌려준다."""
        items, pages, cursor = [], [], None
        for _ in range(1000):
            r = self.list(room, limit, cursor)
            pages.append(r)
            if r.status != 200:
                break
            items.extend(r.body["items"])
            cursor = r.body["next_cursor"]
            if cursor is None:
                break
        return items, pages


def check_schema(obj, schema, exact=True):
    """계약과 다른 점을 문자열 목록으로 돌려준다. 빈 목록이면 통과."""
    if not isinstance(obj, dict):
        return [f"객체가 아님: {type(obj).__name__}"]
    problems = []
    for name, typ in schema.items():
        if name not in obj:
            problems.append(f"필드 누락: {name}")
        elif isinstance(obj[name], bool) or not isinstance(obj[name], typ):
            problems.append(f"타입 불일치: {name}={obj[name]!r}")
    if exact:
        problems += [f"계약에 없는 필드: {k}" for k in obj if k not in schema]
    return problems

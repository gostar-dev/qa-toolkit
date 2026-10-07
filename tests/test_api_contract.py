"""메시지 플랫폼 API 계약 테스트.

서버를 테스트마다 새로 띄워 상태가 섞이지 않게 한다.
APIQA_BUG 환경 변수가 있으면 그 결함을 심은 서버를 띄운다 (tools/api_fault_check.py가 사용).
"""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from apiqa.client import ERROR_SCHEMA, MESSAGE_SCHEMA, PAGE_SCHEMA, ApiClient, check_schema
from apiqa.server import MAX_TEXT, start_in_thread


@pytest.fixture
def base():
    srv, url = start_in_thread(bug=os.environ.get("APIQA_BUG") or None)
    yield url
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def alice(base):
    return ApiClient(base, "tok-alice")


@pytest.fixture
def bob(base):
    return ApiClient(base, "tok-bob")


@pytest.fixture
def carol(base):
    return ApiClient(base, "tok-carol")


def assert_error(r, status, code):
    assert r.status == status, r.body
    assert check_schema(r.body, ERROR_SCHEMA) == []
    assert r.body["error"]["code"] == code


# ---------------- 기본, 인증, 권한 ----------------

def test_health(base):
    assert ApiClient(base).request("GET", "/health").status == 200


@pytest.mark.parametrize("token", [None, "tok-wrong"])
def test_send_without_valid_token_is_401(base, token):
    assert_error(ApiClient(base, token).send("r1", "hi"), 401, "UNAUTHORIZED")


@pytest.mark.parametrize("token", [None, "tok-wrong"])
def test_list_without_valid_token_is_401(base, token):
    assert_error(ApiClient(base, token).list("r1"), 401, "UNAUTHORIZED")


def test_non_member_cannot_send(carol):
    assert_error(carol.send("r1", "hi"), 403, "FORBIDDEN")


def test_non_member_cannot_list(alice, carol):
    alice.send("r1", "secret")
    assert_error(carol.list("r1"), 403, "FORBIDDEN")


def test_non_member_cannot_read_single(alice, carol):
    mid = alice.send("r1", "secret").body["id"]
    assert_error(carol.get(mid), 403, "FORBIDDEN")


def test_unknown_room_is_404(alice):
    assert_error(alice.send("nope", "hi"), 404, "ROOM_NOT_FOUND")


def test_unknown_message_is_404(alice):
    assert_error(alice.get("000000000000"), 404, "MESSAGE_NOT_FOUND")


# ---------------- 전송과 응답 계약 ----------------

def test_send_returns_201_and_matches_contract(alice):
    r = alice.send("r1", "안녕하세요")
    assert r.status == 201
    assert check_schema(r.body, MESSAGE_SCHEMA) == []
    assert (r.body["room_id"], r.body["sender"], r.body["text"]) == ("r1", "alice", "안녕하세요")


def test_single_read_matches_contract_and_sent_message(alice, bob):
    sent = alice.send("r1", "hello").body
    r = bob.get(sent["id"])
    assert r.status == 200
    assert check_schema(r.body, MESSAGE_SCHEMA) == []
    assert r.body == sent


def test_list_page_matches_contract(alice):
    alice.send("r1", "a")
    r = alice.list("r1")
    assert r.status == 200
    assert check_schema(r.body, PAGE_SCHEMA) == []
    assert all(check_schema(m, MESSAGE_SCHEMA) == [] for m in r.body["items"])


# ---------------- 입력 검증과 경계값 ----------------

@pytest.mark.parametrize("payload", [{"text": ""}, {"text": "   "}, {"text": 123}, {}, {"text": None}])
def test_invalid_text_is_400(alice, payload):
    assert_error(alice.request("POST", "/v1/rooms/r1/messages", payload), 400, "INVALID_TEXT")


def test_broken_json_is_400(alice):
    r = alice.request("POST", "/v1/rooms/r1/messages", raw_body=b"{not json",
                      headers={"Content-Type": "application/json"})
    assert_error(r, 400, "INVALID_JSON")


def test_text_length_boundary(alice):
    assert alice.send("r1", "가" * MAX_TEXT).status == 201
    assert_error(alice.send("r1", "가" * (MAX_TEXT + 1)), 400, "TEXT_TOO_LONG")


@pytest.mark.parametrize("limit", [0, 101, "x"])
def test_invalid_limit_is_400(alice, limit):
    assert alice.list("r1", limit).status == 400


# ---------------- 멱등성 (재전송) ----------------

def test_same_idempotency_key_creates_one_message(alice):
    first = alice.send("r1", "pay", key="k-1")
    again = alice.send("r1", "pay", key="k-1")
    assert first.status == 201 and again.status == 200
    assert again.headers.get("idempotent-replayed") == "true"
    assert again.body["id"] == first.body["id"]
    items, _ = alice.list_all("r1", 50)
    assert len(items) == 1


def test_idempotency_key_is_scoped_per_user(alice, bob):
    a = alice.send("r1", "x", key="same").body
    b = bob.send("r1", "y", key="same").body
    assert a["id"] != b["id"]


def test_parallel_retries_with_same_key_create_one_message(alice):
    with ThreadPoolExecutor(10) as ex:
        ids = {r.body["id"] for r in ex.map(lambda _: alice.send("r1", "retry", key="burst"), range(10))}
    assert len(ids) == 1
    items, _ = alice.list_all("r1", 50)
    assert len(items) == 1


# ---------------- 페이지 조회 ----------------

@pytest.mark.parametrize("limit", [1, 7, 25, 100])
def test_pagination_returns_every_message_once_in_order(alice, limit):
    sent = [alice.send("r1", f"m{i}").body["id"] for i in range(25)]
    items, pages = alice.list_all("r1", limit)
    assert all(p.status == 200 for p in pages)
    assert [m["id"] for m in items] == sent
    assert pages[-1].body["next_cursor"] is None


def test_concurrent_sends_get_unique_sequence(alice, bob):
    def send(i):
        return (alice if i % 2 else bob).send("r1", f"c{i}").body["seq"]
    with ThreadPoolExecutor(20) as ex:
        seqs = list(ex.map(send, range(40)))
    assert len(set(seqs)) == 40
    items, _ = alice.list_all("r1", 100)
    assert len(items) == 40


# ---------------- 전송 취소 ----------------

def test_unsend_by_sender(alice, bob):
    keep = alice.send("r1", "keep").body["id"]
    gone = alice.send("r1", "gone").body["id"]
    assert alice.unsend(gone).status == 204
    assert_error(bob.get(gone), 410, "MESSAGE_UNSENT")
    items, _ = bob.list_all("r1", 50)
    assert [m["id"] for m in items] == [keep]


def test_only_sender_can_unsend(alice, bob):
    mid = alice.send("r1", "mine").body["id"]
    assert_error(bob.unsend(mid), 403, "FORBIDDEN")
    assert bob.get(mid).status == 200


def test_unsend_twice_is_410(alice):
    mid = alice.send("r1", "once").body["id"]
    assert alice.unsend(mid).status == 204
    assert_error(alice.unsend(mid), 410, "MESSAGE_UNSENT")

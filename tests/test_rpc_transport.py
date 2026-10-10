"""Exercise the reusable transport against real subprocess protocol peers."""

import sys
import time

import pytest

from testwalker.rpc_transport import RpcTimeoutError, RpcTransport, RpcTransportError

PEER = r'''
import json
import sys
import time


def send(message):
    print(json.dumps(message), flush=True)


def result(identifier, value):
    send({"jsonrpc": "2.0", "id": identifier, "result": value})


if len(sys.argv) > 1 and sys.argv[1] == "not-reading":
    time.sleep(30)

for raw in sys.stdin:
    try:
        request = json.loads(raw)
    except ValueError:
        send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        continue
    identifier = request.get("id") if isinstance(request, dict) else None
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        send({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32600, "message": "Invalid Request"}})
        continue
    method = request.get("method")
    params = request.get("params", {})
    if not isinstance(params, (dict, list)):
        send({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Invalid params"}})
    elif method == "echo":
        result(identifier, params)
    elif method == "error":
        send({"jsonrpc": "2.0", "id": identifier,
              "error": {"code": -32042, "message": "Rejected", "data": {"reason": "synthetic"}}})
    elif method == "bad":
        sys.stdout.write(params["line"] + "\n")
        sys.stdout.flush()
    elif method == "large":
        result(identifier, "x" * 1000)
    elif method == "hang":
        while True:
            send({"jsonrpc": "2.0", "method": "progress", "params": {"busy": True}})
            time.sleep(0.01)
    elif method == "stderr":
        sys.stderr.write("z" * 200000 + "END")
        sys.stderr.flush()
        result(identifier, True)
    elif method == "callback":
        send({"jsonrpc": "2.0", "method": "progress", "params": {"step": 1}})
        send({"jsonrpc": "2.0", "id": "callback/1", "method": "target.ready", "params": {"ready": True}})
        nested = json.loads(next(sys.stdin))
        if nested.get("method") == "cancel":
            if params.get("nested_hang"):
                time.sleep(10)
            if params.get("early"):
                result(identifier, {"cancelled": True})
            result(nested["id"], {"acknowledged": True})
            reply = json.loads(next(sys.stdin))
        else:
            reply = nested
        if not params.get("early"):
            result(identifier, {"callback": reply})
    elif method == "shutdown":
        result(identifier, True)
        break
    elif method == "exit":
        break
'''


@pytest.fixture
def peer():
    opened = []

    def launch(**kwargs):
        client = RpcTransport([sys.executable, "-u", "-c", PEER], **kwargs)
        opened.append(client)
        return client

    yield launch
    for client in opened:
        client.close()
        assert client.process.poll() is not None


def test_only_explicit_calls_are_sent_and_errors_remain_raw(peer):
    with peer() as client:
        params = {"text": "こんにちは", "items": [None, False, 1]}
        response = client.request("echo", params)
        assert response == {"jsonrpc": "2.0", "id": "testwalker/1", "result": params}
        params["items"].append(2)
        assert client.transcript[0]["message"]["params"]["items"] == [None, False, 1]
        response["result"]["text"] = "changed by caller"
        assert client.transcript[1]["message"]["result"]["text"] == "こんにちは"
        error = client.request("error")
        assert error["error"] == {
            "code": -32042, "message": "Rejected", "data": {"reason": "synthetic"},
        }
        assert [entry["direction"] for entry in client.transcript] == [
            "sent", "received", "sent", "received",
        ]
        assert client.request("echo", [1, 2])["result"] == [1, 2]


def test_invalid_outgoing_envelopes_and_malformed_json_are_testable(peer):
    with peer() as client:
        response = client.exchange({"jsonrpc": "1.0", "id": 9, "method": "echo"})
        assert response["id"] == 9 and response["error"]["code"] == -32600
        response = client.exchange({"jsonrpc": "2.0", "id": "bad-params", "method": "echo", "params": 42})
        assert response["error"]["code"] == -32602
        response = client.exchange_raw('{"unfinished":', expected_id=None)
        assert response["id"] is None and response["error"]["code"] == -32700
        assert client.transcript[-2] == {"direction": "sent", "raw": '{"unfinished":'}
        assert client.request("echo")["result"] == {}


@pytest.mark.parametrize(
    "line, reason",
    [
        ('{"jsonrpc":"2.0","id":"wrong","result":true}', "Mismatched"),
        ('{"jsonrpc":"2.0","id":true,"result":true}', "response ID"),
        ('{"jsonrpc":"2.0","id":"testwalker/1"}', "exactly one"),
        ('{"jsonrpc":"2.0","id":"testwalker/1","result":true,"error":{}}', "exactly one"),
        ('{"jsonrpc":"1.0","id":"testwalker/1","result":true}', "2.0"),
        ('{"jsonrpc":"2.0","id":"testwalker/1","error":{"code":true,"message":"x"}}', "error object"),
        ('{"jsonrpc":"2.0","id":"testwalker/1","error":{"code":-1}}', "error object"),
        ('{"jsonrpc":"2.0","id":"testwalker/1","result":NaN}', "invalid JSON"),
        ('not JSON', "invalid JSON"),
        ('[]', "envelope"),
    ],
)
def test_broken_responses_fail_and_reap_worker(peer, line, reason):
    client = peer()
    with pytest.raises(RpcTransportError, match=reason):
        client.request("bad", {"line": line})
    assert client.closed and client.process.poll() is not None
    assert client.transcript[-1]["raw"] == line


def test_notifications_do_not_extend_request_deadline(peer):
    client = peer(timeout=0.15)
    started = time.monotonic()
    with pytest.raises(RpcTimeoutError, match="respond"):
        client.request("hang")
    assert time.monotonic() - started < 3
    assert client.notifications
    assert client.process.poll() is not None


def test_blocked_outgoing_write_is_also_bounded_and_reaped():
    client = RpcTransport([sys.executable, "-u", "-c", PEER, "not-reading"], timeout=0.15)
    started = time.monotonic()
    with pytest.raises(RpcTimeoutError, match="accept"):
        client.request("echo", {"large": "x" * 1_000_000})
    assert time.monotonic() - started < 3
    assert client.process.poll() is not None
    client.close()


@pytest.mark.parametrize("early", [False, True])
def test_callback_can_make_nested_request_and_buffer_outer_response(peer, early):
    client = peer()

    def callback(method, params):
        assert method == "target.ready" and params == {"ready": True}
        assert client.request("cancel")["result"] == {"acknowledged": True}
        return {"handled": True}

    client.handler = callback
    response = client.request("callback", {"early": early})
    if early:
        assert response["result"] == {"cancelled": True}
    else:
        assert response["result"]["callback"] == {
            "jsonrpc": "2.0", "id": "callback/1", "result": {"handled": True},
        }
    assert client.notifications == [{"jsonrpc": "2.0", "method": "progress", "params": {"step": 1}}]
    assert client.request("echo", {"after": True})["result"] == {"after": True}


def test_missing_or_failing_callback_returns_protocol_error_without_stopping_peer(peer):
    with peer() as client:
        reply = client.request("callback")["result"]["callback"]
        assert reply["error"]["code"] == -32601

        def rejected(method, params):
            raise ValueError("Synthetic callback failure")

        client.handler = rejected
        reply = client.request("callback")["result"]["callback"]
        assert reply["error"] == {"code": -32000, "message": "Synthetic callback failure"}
        assert client.request("echo")["result"] == {}


def test_stderr_is_drained_and_bounded_without_requiring_newlines(peer):
    with peer(stderr_limit=97) as client:
        assert client.request("stderr")["result"] is True
        assert client.request("shutdown")["result"] is True
        client.process.wait(timeout=2)
    assert client.stderr == "z" * 94 + "END"
    assert client.process.poll() == 0
    client.close()


def test_worker_exit_is_distinct_from_api_rejection(peer):
    client = peer()
    with pytest.raises(RpcTransportError, match="exited"):
        client.request("exit")
    assert client.process.poll() is not None


def test_oversized_response_and_context_exit_close_worker(peer):
    client = peer(max_message=256)
    with pytest.raises(RpcTransportError, match="size limit"):
        client.request("large")
    assert client.closed


def test_invalid_local_arguments_do_not_launch_or_send(peer):
    with pytest.raises(ValueError, match="sequence"):
        RpcTransport("python --dangerously-parsed")
    with peer() as client:
        with pytest.raises(ValueError, match="expected_id"):
            client.exchange({"method": "echo"})
        with pytest.raises(ValueError, match="one line"):
            client.exchange_raw("{}\n{}", expected_id=None)
        with pytest.raises(ValueError, match="finite"):
            client.exchange({"id": 1, "value": float("inf")})
        assert client.transcript == []
        assert client.request("echo")["result"] == {}


def test_callback_work_is_included_in_outer_deadline(peer):
    client = peer(timeout=1)

    def callback(method, params):
        client.request("cancel")
        time.sleep(0.15)
        return {}

    client.handler = callback
    with pytest.raises(RpcTimeoutError):
        client.request("callback", {"early": True}, timeout=0.1)
    assert client.closed and client.process.poll() is not None


def test_context_manager_reaps_worker_on_application_error(peer):
    client = peer()
    with pytest.raises(RuntimeError, match="application failure"):
        with client:
            client.request("echo")
            raise RuntimeError("application failure")
    assert client.closed and client.process.poll() is not None


def test_nested_io_cannot_extend_the_outer_request_deadline(peer):
    client = peer(timeout=3)
    stages = []

    def callback(method, params):
        stages.append("callback started")
        client.request("cancel", timeout=3)
        stages.append("nested request returned")
        return {}

    client.handler = callback
    started = time.monotonic()
    with pytest.raises(RpcTimeoutError, match="outer request deadline"):
        client.request("callback", {"nested_hang": True}, timeout=0.2)
    assert time.monotonic() - started < 1
    assert stages == ["callback started"]
    assert client.closed and client.process.poll() is not None

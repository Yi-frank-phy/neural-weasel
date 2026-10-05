from neural_weasel.pipe_server import NamedPipeServer


class Engine:
    context_epoch = 0
    calls = 0

    def request_context_update(self, before, after):
        self.calls += 1
        self.context_epoch += 1
        return self.context_epoch

    def reset_private_context(self):
        self.context_epoch = 0


def update(revision=1, *, bound=True):
    message = dict(
        type="context_update",
        request_id=f"ctx-{revision}",
        session_id="receipt-test",
        context_epoch=revision,
        before="public synthetic",
        after="fixture",
    )
    if bound:
        message.update(context_session="a" * 32, source_revision=revision, security_label="private")
    return message


def receipt(message):
    return {key: value for key, value in message.items() if key not in {"before", "after"}} | {
        "type": "context_update_receipt"
    }


def test_lost_ack_receipt_does_not_recompute_or_store_text():
    engine = Engine()
    server = NamedPipeServer(engine)
    message = update()
    assert server.handle_message(receipt(message))["accepted"] is False
    ack = server.handle_message(message)
    recovered = server.handle_message(receipt(message))
    assert recovered["accepted"] is True
    assert recovered["context_epoch"] == ack["context_epoch"]
    assert recovered["client_context_epoch"] == 1
    assert engine.calls == 1
    assert "public synthetic" not in repr(server._context_update_receipt)
    assert "fixture" not in repr(server._context_update_receipt)


def test_receipt_matches_all_identity_fields_and_rejects_text():
    server = NamedPipeServer(Engine())
    message = update()
    server.handle_message(message)
    for key, value in [
        ("request_id", "other"),
        ("session_id", "other"),
        ("context_epoch", 2),
        ("context_session", "b" * 32),
        ("source_revision", 2),
        ("security_label", "normal"),
    ]:
        request = receipt(message) | {key: value}
        assert server.handle_message(request)["accepted"] is False
    assert server.handle_message(receipt(message) | {"before": "secret"})["ok"] is False


def test_receipt_cannot_recover_superseded_or_cleared_context():
    server = NamedPipeServer(Engine())
    first = update()
    server.handle_message(first)
    server.handle_message(update(2))
    assert server.handle_message(receipt(first))["accepted"] is False
    server.handle_message(dict(type="focus", session_id="receipt-test", focused=True, secure=True))
    assert server.handle_message(receipt(update(2)))["accepted"] is False
    assert server._context_update_receipt is None


def test_legacy_receipt_and_reset():
    server = NamedPipeServer(Engine())
    message = update(bound=False)
    server.handle_message(message)
    assert server.handle_message(receipt(message))["accepted"] is True
    server.handle_message(dict(type="reset", session_id="receipt-test"))
    assert server.handle_message(receipt(message))["accepted"] is False


def test_receipt_requires_request_id_and_duplicate_update_remains_stale():
    server = NamedPipeServer(Engine())
    message = update()
    server.handle_message(message)
    assert server.handle_message(message)["accepted"] is False
    request = receipt(message)
    del request["request_id"]
    assert server.handle_message(request)["ok"] is False
    assert server.handle_message(receipt(message))["accepted"] is True

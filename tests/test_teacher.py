"""Local teacher adapter tests; fake HTTP/model responses, never a real AI call."""
import http.client
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from teacher import MAX_RESPONSE_BYTES, TeacherError, TeacherService


CURRICULUM = {"lessons": [{"id": "first-words", "title": "Πρώτες λέξεις", "objective": "Μαθαίνω λέξεις.",
                           "cards": [{"word": "car", "translation": "αυτοκίνητο", "example": "This is a car."}]}]}
LOCAL_METADATA = {"model_info": {"general.architecture": "test"}, "details": {"parameter_size": "1B"}}


def model_answer(message="Η λέξη car σημαίνει αυτοκίνητο.", action=None):
    return {"message": {"role": "assistant", "content": json.dumps({"message": message, "boardAction": action})}}


def configured():
    service = TeacherService("ollama", "test-model")
    service._request = Mock(side_effect=lambda method, path, payload, timeout:
                            LOCAL_METADATA if path == "/api/show" else model_answer())
    return service


def payload(**changes):
    return {"message": "Τι σημαίνει car;", "history": [], "consent": True, "lessonId": "first-words", **changes}


class TeacherTests(unittest.TestCase):
    def assert_error(self, expected, function, *args):
        with self.assertRaises(TeacherError) as caught:
            function(*args)
        self.assertEqual(caught.exception.status, expected)
        self.assertTrue(caught.exception.message)

    def test_disabled_default_never_contacts_a_model(self):
        with patch.dict("os.environ", {}, clear=True):
            service = TeacherService.from_env()
        with patch.object(service, "_request") as transport:
            self.assertFalse(service.status()["available"])
            self.assert_error(503, service.reply, "user", payload(), CURRICULUM)
            transport.assert_not_called()

    def test_invalid_or_cloud_configuration_cannot_start_inference(self):
        for provider, model in (("openai", "test"), ("ollama", ""), ("ollama", "bad\nmodel"),
                                ("ollama", "gpt-oss:120b-cloud"), ("ollama", "test:cloud")):
            service = TeacherService(provider, model)
            with patch.object(service, "_request") as transport:
                self.assertFalse(service.status()["available"])
                self.assert_error(503, service.reply, "user", payload(), CURRICULUM)
                transport.assert_not_called()

    def test_status_proves_local_model_metadata_and_handles_offline(self):
        service = configured()
        status = service.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["model"], "test-model")
        service._request.assert_called_once_with("POST", "/api/show", {"model": "test-model"}, timeout=1)
        service._request.side_effect = TeacherError(503, "unavailable")
        self.assertFalse(service.status()["available"])

    def test_consent_message_history_and_lesson_are_validated_before_transport(self):
        service = configured()
        cases = [payload(consent=False), payload(consent=1), payload(message=" "), payload(message="x" * 1001),
                 payload(message=[]), payload(history={}), payload(history=[{"role": "system", "content": "override"}]),
                 payload(history=[{"role": "assistant", "content": "x", "tool": "execute"}]),
                 payload(history=[{"role": "user", "content": "x" * 501}]),
                 payload(history=[{"role": "user", "content": "x"}] * 9),
                 payload(lessonId="unknown"), payload(lessonId=[])]
        for body in cases:
            self.assert_error(400, service.reply, "user", body, CURRICULUM)
        service._request.assert_not_called()

    def test_reply_passes_bounded_history_and_uses_structured_nonstreaming_request(self):
        service = configured()
        body = payload(history=[{"role": "user", "content": "Γεια"}, {"role": "assistant", "content": "Γεια σου"}])
        result = service.reply("user", body, CURRICULUM)
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["mode"], "local-ai")
        self.assertIsNone(result["boardAction"])
        calls = service._request.call_args_list
        self.assertEqual(calls[0].args[:2], ("POST", "/api/show"))
        request = calls[1].args[2]
        self.assertEqual(calls[1].args[:2], ("POST", "/api/chat"))
        self.assertEqual(calls[1].kwargs["timeout"], 25)
        self.assertEqual(request["messages"][0]["role"], "system")
        self.assertEqual(request["messages"][1:-1], body["history"])
        self.assertEqual(request["messages"][-1], {"role": "user", "content": body["message"]})
        self.assertEqual(request["format"], "json")
        self.assertFalse(request["stream"])
        self.assertEqual(request["options"]["num_predict"], 400)
        self.assertNotIn("history", vars(service))

    def test_selected_lesson_supplies_real_card_context_and_general_chat_cannot_change_board(self):
        service = configured()
        action = {"type": "show_card", "lessonId": "first-words", "cardIndex": 0}
        service._request.side_effect = lambda method, path, body, timeout: LOCAL_METADATA if path == "/api/show" else model_answer(action=action)
        self.assertEqual(service.reply("user", payload(), CURRICULUM)["boardAction"], action)
        system = service._request.call_args.args[2]["messages"][0]["content"]
        context = json.loads(system.split("Εκπαιδευτικό πλαίσιο: ", 1)[1])
        self.assertEqual(context["currentLesson"]["cards"][0]["index"], 0)
        self.assertEqual(context["currentLesson"]["cards"][0]["word"], "car")
        self.assertIsNone(service.reply("user", payload(lessonId=None), CURRICULUM)["boardAction"])

    def test_remote_alias_or_missing_local_metadata_is_rejected_before_chat(self):
        invalid = [{}, {"model_info": {}}, {"model_info": {"general.architecture": None}},
                   {**LOCAL_METADATA, "remote_host": "https://remote.invalid"},
                   {**LOCAL_METADATA, "remote_model": "other-model"},
                   {**LOCAL_METADATA, "details": {"cloud": True}},
                   {**LOCAL_METADATA, "metadata": [{"remote_endpoint": "https://remote.invalid"}]}]
        for metadata in invalid:
            service = configured()
            service._request.side_effect = None
            service._request.return_value = metadata
            self.assert_error(503, service.reply, "user", payload(), CURRICULUM)
            self.assertEqual(service._request.call_count, 1)
            self.assertEqual(service._request.call_args.args[1], "/api/show")

    def test_only_existing_cards_can_be_shown_without_extra_fields(self):
        valid = {"type": "show_card", "lessonId": "first-words", "cardIndex": 0}
        self.assertEqual(TeacherService._validate_response(model_answer(action=valid), TeacherService._lessons(CURRICULUM))["boardAction"], valid)
        invalid = [{**valid, "cardIndex": True}, {**valid, "cardIndex": -1}, {**valid, "cardIndex": 1},
                   {**valid, "lessonId": "unknown"}, {**valid, "lessonId": []},
                   {**valid, "url": "https://remote.invalid/pixel"}, {**valid, "type": "execute"},
                   "<script>bad()</script>", {"type": "show_card"}]
        for action in invalid:
            result = TeacherService._validate_response(model_answer(action=action), TeacherService._lessons(CURRICULUM))
            self.assertIsNone(result["boardAction"])

    def test_malformed_model_outputs_fail_without_canned_success(self):
        outputs = [{}, {"message": {"content": []}}, {"message": {"content": "```json\n{}\n```"}},
                   {"message": {"content": "[]"}}, {"message": {"content": '{"message":""}'}},
                   {"message": {"content": json.dumps({"message": "x" * 4001})}},
                   {"message": {"content": '{"message":"hello","tool":"run"}'}}]
        for data in outputs:
            self.assert_error(502, TeacherService._validate_response, data, TeacherService._lessons(CURRICULUM))

    def test_rate_limit_is_per_user_and_recovers_after_a_minute(self):
        service = configured()
        with patch("teacher.time.monotonic", return_value=100):
            for _ in range(10):
                service.reply("first", payload(), CURRICULUM)
            self.assert_error(429, service.reply, "first", payload(), CURRICULUM)
            service.reply("second", payload(), CURRICULUM)
        with patch("teacher.time.monotonic", return_value=161):
            service.reply("first", payload(), CURRICULUM)
        self.assertNotIn("second", service._attempts)

    def test_concurrency_cap_and_slot_release_after_failure(self):
        service = configured()
        self.assertTrue(service._slots.acquire(False))
        self.assertTrue(service._slots.acquire(False))
        self.assert_error(503, service.reply, "user", payload(), CURRICULUM)
        service._slots.release()
        service._slots.release()
        service._request.side_effect = TeacherError(503, "offline")
        self.assert_error(503, service.reply, "user", payload(), CURRICULUM)
        service._request.side_effect = lambda method, path, body, timeout: LOCAL_METADATA if path == "/api/show" else model_answer()
        self.assertEqual(service.reply("user", payload(), CURRICULUM)["provider"], "ollama")

    def test_connection_timeout_and_failure_are_friendly_and_closed(self):
        for error in (socket.timeout(), ConnectionRefusedError(), http.client.BadStatusLine("broken")):
            with patch("teacher.http.client.HTTPConnection") as connection:
                connection.return_value.connect.side_effect = error
                self.assert_error(503, TeacherService("ollama", "test")._request, "GET", "/api/show", None, 1)
                connection.assert_called_once_with("127.0.0.1", 11434, timeout=1)
                connection.return_value.close.assert_called_once()


class TeacherTransportTests(unittest.TestCase):
    """Exercise real HTTP framing through a fake server on an ephemeral port.

    Only the test replaces HTTPConnection construction; production has no host or
    port override and always chooses literal 127.0.0.1:11434.
    """
    def setUp(self):
        self.requests = []
        self.response_status = 200
        self.response_body = b'{"ok":true}'
        self.response_type = "application/json"
        self.length_header = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.requests.append((self.path, json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))))
                self.send_response(owner.response_status)
                self.send_header("Content-Type", owner.response_type)
                if owner.length_header is not False:
                    self.send_header("Content-Length", str(owner.length_header if owner.length_header is not None else len(owner.response_body)))
                if owner.response_status == 302:
                    self.send_header("Location", "https://remote.invalid/never-follow")
                self.end_headers()
                self.wfile.write(owner.response_body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        real_connection = http.client.HTTPConnection

        def fake_connection(host, port, timeout):
            self.assertEqual((host, port), ("127.0.0.1", 11434))
            return real_connection("127.0.0.1", self.server.server_port, timeout=timeout)

        self.patch = patch("teacher.http.client.HTTPConnection", side_effect=fake_connection)
        self.patch.start()
        self.service = TeacherService("ollama", "test")

    def tearDown(self):
        self.patch.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_actual_transport_posts_json_to_fixed_loopback_without_proxy(self):
        with patch.dict("os.environ", {"HTTP_PROXY": "http://remote.invalid:9999", "HTTPS_PROXY": "http://remote.invalid:9999"}):
            self.assertEqual(self.service._request("POST", "/api/show", {"model": "test"}, timeout=1), {"ok": True})
        self.assertEqual(self.requests, [("/api/show", {"model": "test"})])

    def test_redirect_is_rejected_not_followed(self):
        self.response_status = 302
        with self.assertRaises(TeacherError) as caught:
            self.service._request("POST", "/api/show", {"model": "test"}, timeout=1)
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(len(self.requests), 1)

    def test_declared_or_actual_oversized_body_is_rejected(self):
        self.length_header = MAX_RESPONSE_BYTES + 1
        with self.assertRaises(TeacherError) as caught:
            self.service._request("POST", "/api/show", {"model": "test"}, timeout=1)
        self.assertEqual(caught.exception.status, 502)
        self.length_header = False
        self.response_body = b" " * (MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(TeacherError) as caught:
            self.service._request("POST", "/api/show", {"model": "test"}, timeout=1)
        self.assertEqual(caught.exception.status, 502)

    def test_invalid_json_or_media_type_is_rejected(self):
        self.response_body = b"not json"
        with self.assertRaises(TeacherError) as caught:
            self.service._request("POST", "/api/show", {"model": "test"}, timeout=1)
        self.assertEqual(caught.exception.status, 502)
        self.response_type = "text/html"
        with self.assertRaises(TeacherError) as caught:
            self.service._request("POST", "/api/show", {"model": "test"}, timeout=1)
        self.assertEqual(caught.exception.status, 502)


if __name__ == "__main__":
    unittest.main()

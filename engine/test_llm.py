"""OAuth transport and feature-contract regressions; no network or GPU required."""
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import llm
import discover
import music
import trailer

MOMENT = {"start_tc": "0:00", "end_tc": "0:30", "start_quote": "Un exemplu concret",
          "end_quote": "merita spus mai departe", "summary": "O idee", "why": "Clear",
          "visual": "Interview", "hook": 8, "self_contained": 9, "emotion": 6, "clarity": 8}
TRAILER = {"candidates": [{"reel": 1, "start_quote": "Un exemplu concret",
                         "end_quote": "merita spus", "summary": "O idee", "why": "Clear", "visual": "Interview"}],
           "orders": [{"name": "First", "why": "Clear", "clips": [
               {"candidate": 1, "role": "hook", "summary": "O idee", "why": "Clear"}]}]}
PICKS = {"picks": [{"reel": 1, "song": "calm.wav", "reason": "Reflective"}]}


def response(text, status="completed"):
    return {"status": status, "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}]}]}


def events(*items):
    return io.BytesIO("".join("data: " + json.dumps(x) + "\r\n\r\n" for x in items).encode())


class TransportTests(unittest.TestCase):
    @patch.object(llm.http.client, "HTTPConnection")
    def test_sse_mislabeled_as_json(self, factory):
        result = events({"type": "response.completed", "response": response('{"moments":[]}')})
        result.status = 200
        factory.return_value.getresponse.return_value = result
        self.assertEqual(llm.request(llm.BASE_URL, "/responses", {}, stream=True), '{"moments":[]}')

    def test_complete_stream_ignores_partial_and_reasoning(self):
        stream = events({"type": "response.output_text.delta", "delta": "wrong"},
                        {"type": "response.completed", "response": response('{"moments":[]}')})
        self.assertEqual(llm.read_stream(stream, float("inf")), '{"moments":[]}')

    def test_responses_lite_completed_items(self):
        item = response('{"moments":[]}')["output"][0]
        stream = events({"type": "response.output_item.done", "output_index": 0, "item": item},
                        {"type": "response.completed", "response": {"status": "completed", "output": []}})
        self.assertEqual(llm.read_stream(stream, float("inf")), '{"moments":[]}')
        with self.assertRaises(llm.ModelError):
            llm.read_stream(events({"type": "response.output_item.done", "item": item}), float("inf"))

    def test_stream_failures(self):
        for stream in (io.BytesIO(b""), io.BytesIO(b"data: [DONE]\n\n"),
                       io.BytesIO(b"data: invalid\n\n"), events({"type": "response.incomplete"}),
                       events({"type": "response.failed", "error": {"code": "server_error"}})):
            with self.subTest(stream=stream), self.assertRaises(llm.ModelError):
                llm.read_stream(stream, float("inf"))

    def test_empty_incomplete_and_refusal(self):
        for result in (response(""), response("partial", "incomplete"),
                       {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}):
            with self.subTest(result=result), self.assertRaises(llm.ModelError):
                llm.completed_text(result)

    def test_auth_errors_do_not_leak_server_body(self):
        error = str(llm.classify_error(502, "invalid_grant SECRET_TOKEN"))
        self.assertIn("login", error)
        self.assertNotIn("SECRET_TOKEN", error)
        self.assertIn("OLLAMA_API_KEY", str(llm.classify_error(401, "", "ollama")))

    def test_malformed_stream_shapes(self):
        for stream in (events([]), events({"type": "response.output_item.done", "item": None}),
                       events({"type": "response.completed", "response": {"status": "completed", "output": [None]}})):
            with self.assertRaises(llm.ModelError):
                llm.read_stream(stream, float("inf"))

    @patch.object(llm.time, "sleep")
    @patch.object(llm.http.client, "HTTPConnection")
    def test_retry_limit(self, factory, sleep):
        conn = factory.return_value
        conn.getresponse.return_value.status = 503
        conn.getresponse.return_value.read.return_value = b"upstream unavailable"
        with self.assertRaises(llm.ModelError):
            llm.request(llm.BASE_URL, "/models")
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(llm.http.client, "HTTPConnection")
    def test_auth_never_retried(self, factory):
        conn = factory.return_value
        conn.getresponse.return_value.status = 502
        conn.getresponse.return_value.read.return_value = b"invalid_grant"
        with self.assertRaisesRegex(llm.ModelError, "login"):
            llm.request(llm.BASE_URL, "/models")
        self.assertEqual(factory.call_count, 1)

    @patch.object(llm.http.client, "HTTPConnection")
    def test_timeout(self, factory):
        factory.return_value.connect.side_effect = TimeoutError()
        with self.assertRaisesRegex(llm.ModelError, "timed out"):
            llm.request(llm.BASE_URL, "/responses", {})


class StartupTests(unittest.TestCase):
    @patch.object(llm, "node_command")
    @patch.object(llm, "proxy_running", return_value=True)
    def test_running_proxy_reused(self, running, command):
        llm.ensure_proxy()
        command.assert_not_called()

    @patch.object(llm.subprocess, "run")
    @patch.object(llm, "node_command", return_value=["node", "cli"])
    @patch.object(llm, "proxy_running", side_effect=[False, True])
    def test_cold_start(self, running, command, run):
        llm.ensure_proxy()
        args, kwargs = run.call_args
        self.assertIn("--detach", args[0])
        self.assertIn("127.0.0.1", args[0])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIn("OPENAI_OAUTH_INTERNAL_RUNTIME_DIR", kwargs["env"])

    @patch.object(llm.subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 30))
    @patch.object(llm, "node_command", return_value=["node", "cli"])
    @patch.object(llm, "proxy_running", return_value=False)
    def test_start_timeout(self, *_):
        with self.assertRaisesRegex(llm.ModelError, "30 seconds"):
            llm.ensure_proxy()

    @patch.object(llm.subprocess, "run", return_value=Mock(returncode=1, stderr="already starting", stdout=""))
    @patch.object(llm, "node_command", return_value=["node", "cli"])
    @patch.object(llm, "proxy_running", side_effect=[False, False, True])
    def test_concurrent_start_waits_for_owner(self, *_):
        llm.ensure_proxy()

    @patch.object(llm.shutil, "which", return_value=None)
    @patch.object(Path, "is_file", return_value=False)
    def test_missing_runtime(self, *_):
        with self.assertRaisesRegex(llm.ModelError, "setup required"):
            llm.node_command()

    @patch.object(llm.shutil, "which", return_value="node")
    @patch.object(Path, "is_file", side_effect=[True, False])
    def test_missing_dependency(self, *_):
        with self.assertRaisesRegex(llm.ModelError, "setup required"):
            llm.node_command()

    @patch.object(llm.http.client, "HTTPConnection")
    def test_port_conflict(self, factory):
        factory.return_value.getresponse.return_value.read.return_value = b'{}'
        with self.assertRaisesRegex(llm.ModelError, "occupied"):
            llm.proxy_running()


class ClientTests(unittest.TestCase):
    @patch.object(llm, "ensure_proxy")
    @patch.object(llm, "request")
    def test_account_access_and_request(self, request, ensure):
        request.side_effect = [{"data": [{"id": llm.MODEL}]}, '{"moments":[]}']
        client = llm.ModelClient({})
        self.assertEqual(client.generate("prompt", "moments"), {"moments": []})
        body = request.call_args.args[2]
        self.assertEqual(body["model"], "gpt-6-astra")
        self.assertEqual(body["reasoning"], {"effort": "medium"})
        self.assertTrue(body["stream"])
        for forbidden in ("temperature", "max_tokens", "max_output_tokens"):
            self.assertNotIn(forbidden, body)
        client.preflight()
        ensure.assert_called_once()

    @patch.object(llm, "ensure_proxy")
    @patch.object(llm, "request", return_value={"data": [{"id": "another-model"}]})
    def test_model_unavailable_no_fallback(self, request, ensure):
        with self.assertRaisesRegex(llm.ModelError, "No fallback"):
            llm.ModelClient({}).generate("prompt", "moments")
        request.assert_called_once()

    @patch.object(llm, "request")
    def test_explicit_ollama(self, request):
        request.return_value = {"choices": [{"message": {"content": '{"moments":[]}'}}]}
        client = llm.ModelClient({"AI_PROVIDER": "ollama", "OLLAMA_API_KEY": "test"})
        client.generate("prompt", "moments")
        self.assertEqual(request.call_args.args[2]["model"], "gemma4:31b-cloud")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer test")

    def test_validation(self):
        for feature, data in (("moments", {"moments": [MOMENT]}), ("picks", PICKS), ("trailer", TRAILER)):
            self.assertEqual(llm.validate(data, feature), data)
            with self.subTest(feature=feature), self.assertRaises(llm.ModelError):
                llm.validate({}, feature)
        for data in ({"moments": [dict(MOMENT, hook=float("nan"))]},
                     {"moments": [dict(MOMENT, start_quote="")]}, {"moments": [dict(MOMENT, hook=True)]}):
            with self.assertRaises(llm.ModelError):
                llm.validate(data, "moments")

    @patch.object(llm.ModelClient, "preflight")
    @patch.object(llm, "request", return_value="not JSON")
    def test_invalid_json(self, *_):
        with self.assertRaisesRegex(llm.ModelError, "malformed JSON"):
            llm.ModelClient({}).generate("prompt", "moments")


class FeatureTests(unittest.TestCase):
    @patch.object(llm.ModelClient, "preflight")
    @patch.object(llm, "request")
    def test_all_features_use_astra_and_keep_results(self, request, preflight):
        request.return_value = json.dumps({"moments": [MOMENT]})
        self.assertEqual(discover.ask_model("text", 1, {}, 20, 90, 45, True), [MOMENT])
        request.return_value = json.dumps(PICKS)
        picks = music.choose_songs([{"reel": 1, "text": "Reflective story"}], [Path("calm.wav")], {})
        self.assertEqual(picks[1]["song"], "calm.wav")
        request.return_value = json.dumps(TRAILER)
        transcript = {"duration": 30, "words": [{"word": "Un exemplu concret", "start": 0, "end": 2}]}
        cuts = [{"in_s": 0, "out_s": 30, "segments": [{"in_s": 0, "out_s": 30}]}]
        with patch.object(trailer.match, "locate", side_effect=[
            {"start_s": 0, "end_s": 2, "score": 100}, {"start_s": 18, "end_s": 20, "score": 100}]):
            result = trailer.suggest(transcript, cuts, {})
        self.assertEqual(result["candidates"][0]["length"], 20)
        self.assertEqual(result["orders"][0]["clips"][0]["in_s"], 0)
        self.assertEqual(request.call_count, 3)
        for call in request.call_args_list:
            self.assertEqual(call.args[2]["model"], llm.MODEL)
            self.assertEqual(call.args[2]["reasoning"]["effort"], "medium")

    @patch.object(llm.ModelClient, "preflight", side_effect=llm.ModelError("login required"))
    def test_provider_failure_is_explicit_and_music_optional(self, _):
        with self.assertRaisesRegex(llm.ModelError, "login required"):
            discover.ask_model("text", 1, {}, 20, 90, 45, True)
        self.assertEqual(music.choose_songs([{"reel": 1, "text": "test"}], [Path("calm.wav")], {})[1]["reason"], "login required")
        result = trailer.suggest({"words": []}, [{"in_s": 0, "out_s": 30, "segments": [1]}], {})
        self.assertEqual(result, {"error": "login required"})


if __name__ == "__main__":
    unittest.main()

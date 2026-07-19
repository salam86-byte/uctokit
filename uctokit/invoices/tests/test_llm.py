"""Testy LLM providera – zejména směrování textu a vision na dvě různé URL."""

import unittest
from unittest import mock

from uctokit.invoices.llm.base import LLMEndpoint
from uctokit.invoices.llm.providers import OpenAICompatProvider


class _Resp:
    def __init__(self, content):
        self._c = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._c}}]}


class TwoEndpointRoutingTests(unittest.TestCase):
    def test_text_and_vision_go_to_separate_urls(self):
        ep = LLMEndpoint(
            base_url="http://sglang:30000/v1", model="qwen-35b",
            vision_base_url="http://ollama:11434/v1", vision_model="qwen-vl-doc",
        )
        prov = OpenAICompatProvider(ep)
        with mock.patch("requests.post", return_value=_Resp('{"supplier_name":"X"}')) as post:
            prov.complete_json(system="s", user="u")               # text
            self.assertEqual(post.call_args.args[0], "http://sglang:30000/v1/chat/completions")
            self.assertEqual(post.call_args.kwargs["json"]["model"], "qwen-35b")

            prov.complete_json(system="s", user="u", images=[b"img"])  # vision
            self.assertEqual(post.call_args.args[0], "http://ollama:11434/v1/chat/completions")
            self.assertEqual(post.call_args.kwargs["json"]["model"], "qwen-vl-doc")

    def test_vision_falls_back_to_text_url_when_unset(self):
        ep = LLMEndpoint(base_url="http://only:1/v1", model="txt", vision_model="vis")
        prov = OpenAICompatProvider(ep)
        with mock.patch("requests.post", return_value=_Resp("{}")) as post:
            prov.complete_json(system="s", user="u", images=[b"i"])
            self.assertEqual(post.call_args.args[0], "http://only:1/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()

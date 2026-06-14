from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import scripts.telegram_provider_bot as bot


class TelegramProviderBotMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        bot.CHAT_MEMORY_DIR = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_prompt_includes_recent_memory_and_current_message(self) -> None:
        bot.append_chat_memory("123", "user", "Quero usar NVIDIA", speaker="Felipe")
        bot.append_chat_memory("123", "assistant", "provider: nvidia", speaker="Magneto")

        prompt = bot.build_prompt("123", "e a memoria?", speaker="Felipe")

        self.assertIn("Memoria recente da conversa:", prompt)
        self.assertIn("Usuário (Felipe): Quero usar NVIDIA", prompt)
        self.assertIn("Assistente: provider: nvidia", prompt)
        self.assertIn("Mensagem atual:", prompt)
        self.assertIn("e a memoria?", prompt)

    def test_clear_chat_memory_removes_history(self) -> None:
        bot.append_chat_memory("456", "user", "oi", speaker="Felipe")
        path = bot.chat_memory_path("456")
        self.assertTrue(path.exists())

        bot.clear_chat_memory("456")

        self.assertFalse(path.exists())
        self.assertEqual(bot.load_chat_memory("456"), [])

    def test_append_chat_memory_redacts_secrets(self) -> None:
        bot.append_chat_memory("789", "user", "API key: sk-abcdefghijklmnopqrstu", speaker="Felipe")
        entries = bot.load_chat_memory("789")

        self.assertEqual(len(entries), 1)
        self.assertIn("[REDACTED]", entries[0]["text"])
        self.assertNotIn("sk-abcdefghijklmnopqrstu", entries[0]["text"])


if __name__ == "__main__":
    unittest.main()

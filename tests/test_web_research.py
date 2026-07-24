from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docx import Document

from jarvis.web_research import WebResearcher


class WebResearchTests(unittest.TestCase):
    def test_rejects_local_and_private_urls(self):
        self.assertFalse(WebResearcher._safe_public_url("http://localhost:8000"))
        self.assertFalse(WebResearcher._safe_public_url("http://127.0.0.1/private"))
        self.assertFalse(WebResearcher._safe_public_url("http://192.168.1.1"))
        self.assertFalse(WebResearcher._safe_public_url("file:///C:/secret.txt"))
        self.assertTrue(WebResearcher._safe_public_url("https://example.com"))

    def test_writes_readable_sourced_word_document(self):
        with tempfile.TemporaryDirectory() as value:
            output_dir = Path(value) / "documents"
            researcher = WebResearcher(Path(value) / "data", output_dir=output_dir)
            path = researcher._write_document(
                "test topic",
                "Key findings\n\nA supported statement [1].",
                [
                    {
                        "title": "Example source",
                        "url": "https://example.com",
                        "description": "Source description.",
                        "text": "Readable source text.",
                    }
                ],
            )
            document = Document(path)
            full_text = "\n".join(
                paragraph.text for paragraph in document.paragraphs
            )
            self.assertIn("JARVIS Research: test topic", full_text)
            self.assertIn("https://example.com", full_text)
            self.assertIn("A supported statement [1].", full_text)


if __name__ == "__main__":
    unittest.main()

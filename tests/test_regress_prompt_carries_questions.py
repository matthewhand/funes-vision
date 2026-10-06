"""Regression: the question text must reach the model, not just the schema.

Ollama enforces `format` by translating the JSON schema into a sampling
grammar that keeps types, enums and required keys but DROPS each property's
`description`. A prompt that only says "answer the questions" therefore leaves
the model guessing from bare key names, so every flag comes back as a coin
flip. prompt_for_schema() restates each question into the prompt.

Run from the repo root:  python3 -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images
import scans


class TestPromptForSchema(unittest.TestCase):
    def test_keeps_detect_prompt_header(self):
        out = analyze_images.prompt_for_schema(
            {"type": "object", "properties": {"a": {"type": "boolean"}}})
        self.assertTrue(out.startswith(analyze_images.DETECT_PROMPT))

    def test_question_description_is_in_the_prompt(self):
        schema = {
            "type": "object",
            "required": ["postal_delivery"],
            "properties": {
                "postal_delivery": {
                    "type": "boolean",
                    "description": "True only if a uniformed postie is delivering a parcel.",
                }
            },
        }
        out = analyze_images.prompt_for_schema(schema)
        self.assertIn("uniformed postie is delivering a parcel", out)
        self.assertIn("postal_delivery", out)
        self.assertIn("true or false", out)

    def test_enum_options_are_named(self):
        schema = {
            "type": "object",
            "required": ["postal_how"],
            "properties": {
                "postal_how": {"type": "string",
                               "enum": ["van", "bike", "on foot", "none"]},
            },
        }
        out = analyze_images.prompt_for_schema(schema)
        self.assertIn("one of: van, bike, on foot, none", out)

    def test_required_order_drives_question_order(self):
        schema = {
            "type": "object",
            "required": ["second", "first"],
            "properties": {
                "first": {"type": "boolean", "description": "First question."},
                "second": {"type": "boolean", "description": "Second question."},
            },
        }
        out = analyze_images.prompt_for_schema(schema)
        self.assertLess(out.index("second"), out.index("first"))

    def test_property_without_description_still_asked(self):
        schema = {"type": "object",
                  "properties": {"animal_detected": {"type": "boolean"}}}
        out = analyze_images.prompt_for_schema(schema)
        self.assertIn("animal_detected", out)
        self.assertIn("answer from the image", out)

    def test_empty_schema_is_just_the_header(self):
        out = analyze_images.prompt_for_schema({})
        self.assertEqual(out, analyze_images.DETECT_PROMPT)
        self.assertEqual(analyze_images.prompt_for_schema(None),
                         analyze_images.DETECT_PROMPT)

    def test_tail_only_appended_when_given(self):
        schema = {"type": "object", "properties": {"a": {"type": "boolean"}}}
        plain = analyze_images.prompt_for_schema(schema)
        self.assertNotIn("final frame", plain)
        tailed = analyze_images.prompt_for_schema(
            schema, analyze_images.TIMELINE_PROMPT.format(n=3),
            tail="Answer every question about the final frame only.")
        self.assertIn("final frame only", tailed)
        self.assertIn("These 3 security camera frames", tailed)

    def test_every_live_scan_question_reaches_the_prompt(self):
        """The real guarantee: no live scan asks a question the model can't read."""
        for spec in scans.SCANS:
            out = analyze_images.prompt_for_schema(spec["schema"])
            for key in (spec["schema"].get("required") or []):
                self.assertIn(key, out, "%s missing key %s" % (spec["id"], key))
            for key, prop in (spec["schema"].get("properties") or {}).items():
                desc = (prop.get("description") or "").strip()
                if desc:
                    self.assertIn(desc.split()[0], out,
                                  "%s: description of %s absent" % (spec["id"], key))

    def test_union_schema_prompt_carries_every_question(self):
        union = scans.union_schema(list(scans.SCANS))
        out = analyze_images.prompt_for_schema(union)
        for spec in scans.SCANS:
            for key in (spec["schema"].get("required") or []):
                self.assertIn(key, out)


if __name__ == "__main__":
    unittest.main()

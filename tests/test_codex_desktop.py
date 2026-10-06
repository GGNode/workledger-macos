import json
import tempfile
import unittest
from pathlib import Path
from workledger.adapters.native import parse_codex
from workledger.config import Config
from workledger.store import Store
from workledger.report import build_report

AT = "2026-10-06T09:00:00Z"

class DesktopEnvelopeTests(unittest.TestCase):
    def test_desktop_context_and_answer_envelopes(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Config(Path(root)); cfg.save({"timezone": "UTC"})
            with Store(cfg.db_path) as store:
                texts = [
                    '<in-app-browser-context source="ambient-ui-state">Automatic state</in-app-browser-context>\n## My request:\nCheck the download',
                    '<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>',
                    '<send_user_message_question_reply>'+json.dumps([{"question":"Long UI question", "answer":"Install and pair"}])+'</send_user_message_question_reply>',
                ]
                rows = [{"type":"session_meta", "payload":{"id":"desktop-test"}}]
                rows += [{"type":"response_item", "timestamp":AT, "payload":{"type":"message", "id":str(i), "role":"user", "content":[{"type":"input_text", "text":text}]}} for i,text in enumerate(texts)]
                parse_codex(rows, Path(root)/'log.jsonl', store)
                # Reimport must preserve identities and not resurrect excluded context.
                parse_codex(rows, Path(root)/'log.jsonl', store)
                report=build_report(cfg,store,"2026-10-06")
                self.assertEqual(report['stats']['user_channel_messages'],2)
                prompts=[e for e in report['events'] if e['kind']=='user_message']
                self.assertEqual({e['text'] for e in prompts},{'Check the download','Install and pair'})
                self.assertTrue(all(e['actor']=='unknown' for e in prompts))

if __name__=='__main__':unittest.main()

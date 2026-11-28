import json
import unittest


class FakeSF:
    def __init__(self):
        self.updates = []

    def job_update(self, job, **fields):
        self.updates.append(fields)


class LiveLogTests(unittest.TestCase):
    def test_lines_become_events_and_flush_is_throttled(self):
        from pipeline.live_log import LiveLog
        t = [0.0]
        sf = FakeSF()
        live = LiveLog(sf, "J1", clock=lambda: t[0])
        live.line("[render] visual QA round 1: 1 wrong shot(s), edit score 7.0/10 - ok")
        live.line("[inspect] ignored noise")
        t[0] = 2.0
        live.line("[cover] cover: a.jpg - \"Big Title\" (clear)")  # throttled: no second flush yet
        self.assertEqual(len(sf.updates), 1)
        t[0] = 10.0
        live.line("[reel] published: https://instagram.com/reel/x/")  # publish forces a flush
        events = [json.loads(l) for l in sf.updates[-1]["Live_Log__c"].split("\n")]
        self.assertEqual([e["kind"] for e in events], ["qa", "cover", "publish"])
        self.assertEqual(events[0]["score"], 7.0)

    def test_tee_passes_output_through(self):
        import io
        from pipeline.live_log import LiveLog, _Tee
        out = io.StringIO()
        sf = FakeSF()
        tee = _Tee(out, LiveLog(sf, "J"))
        tee.write("[reel] FAILED RuntimeError: boom\nrest")
        self.assertIn("FAILED", out.getvalue())
        self.assertIn("error", sf.updates[-1]["Live_Log__c"])
        from pipeline.live_log import install
        self.assertIsNone(install(None, "J"))


if __name__ == "__main__":
    unittest.main()

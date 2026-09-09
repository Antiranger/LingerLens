import unittest

from companion.source_timeline import SourceTimeline, TimelinePiece


class SourceTimelineTests(unittest.TestCase):
    def test_maps_continuous_source_time(self):
        timeline = SourceTimeline()
        timeline.append(TimelinePiece(1000.0, 1003.0, 0.0, 3.0))
        timeline.append(TimelinePiece(1003.0, None, 3.0, None))
        self.assertAlmostEqual(timeline.map(1001.25), 1.25)
        self.assertAlmostEqual(timeline.map(1008.0), 8.0)

    def test_collapses_source_gap_without_waiting(self):
        timeline = SourceTimeline()
        timeline.append(TimelinePiece(1000.0, 1003.0, 0.0, 3.0))
        timeline.append(TimelinePiece(1009.0, None, 3.0, None))
        self.assertAlmostEqual(timeline.map(1002.5), 2.5)
        self.assertAlmostEqual(timeline.map(1009.25), 3.25)

    def test_does_not_guess_outside_known_piece(self):
        timeline = SourceTimeline()
        timeline.append(TimelinePiece(1000.0, 1003.0, 0.0, 3.0))
        self.assertIsNone(timeline.map(999.0))
        self.assertIsNone(timeline.map(1003.1))


if __name__ == "__main__":
    unittest.main()

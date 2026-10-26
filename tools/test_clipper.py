import unittest

from clipper import Word, score_window, select_clips, validate_youtube_url


class ClipperTests(unittest.TestCase):
    def test_accepts_supported_youtube_links(self):
        validate_youtube_url("https://www.youtube.com/watch?v=example")
        validate_youtube_url("https://youtu.be/example")

    def test_rejects_non_youtube_urls(self):
        with self.assertRaises(ValueError):
            validate_youtube_url("https://example.com/watch?v=example")
        with self.assertRaises(ValueError):
            validate_youtube_url("http://youtube.com/watch?v=example")

    def test_transcript_score_is_bounded(self):
        words = [
            Word(index * 0.3, index * 0.3 + 0.2, text)
            for index, text in enumerate(
                "Here's why this matters. The biggest mistake is rushing. "
                "But there is a better way!".split()
            )
        ]
        score = score_window(words, 6)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_selects_ten_non_overlapping_clips(self):
        words = []
        text = (
            "Here's why this matters. The biggest lesson is to think carefully. "
            "But there is another way to solve the problem. "
        ).split()
        for index in range(1200):
            token = text[index % len(text)]
            start = index * 0.45
            words.append(Word(start, start + 0.28, token))

        clips = select_clips(words, video_duration=600, clip_count=10)
        self.assertEqual(len(clips), 10)
        chronological = sorted(clips, key=lambda clip: clip.start)
        for previous, current in zip(chronological, chronological[1:]):
            self.assertLessEqual(previous.end, current.start + 1.0)
        self.assertTrue(all(18 <= clip.end - clip.start <= 45 for clip in clips))


if __name__ == "__main__":
    unittest.main()

"""The QR encoder: every version and level decodes to its payload with OpenCV's reader (skipped where cv2 is missing)."""
import os
import random
import unittest

from breadcrumbs_spacegraph import qr

try:
    import cv2
    import numpy as np
except ImportError:  # the reader is a test tool, not a dependency of the plugin
    cv2 = None


def decode(modules, quiet=4, scale=6):
    n = len(modules)
    img = np.full(((n + 2 * quiet) * scale,) * 2, 255, dtype=np.uint8)
    for y, row in enumerate(modules):
        for x, dark in enumerate(row):
            if dark:
                img[(y + quiet) * scale:(y + quiet + 1) * scale, (x + quiet) * scale:(x + quiet + 1) * scale] = 0
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    if not text:  # the classic detector can fail on a valid code that holds a finder-like run; the Aruco one reads it
        text, _, _ = cv2.QRCodeDetectorAruco().detectAndDecode(img)
    return text


class Shape(unittest.TestCase):
    def test_the_sizes_and_capacities_are_the_standard_ones(self):
        self.assertEqual([len(qr.encode(b"a", "M")) for _ in (0,)], [21])
        self.assertEqual(qr.data_codewords(1, "L"), 19)
        self.assertEqual(qr.data_codewords(1, "M"), 16)
        self.assertEqual(qr.data_codewords(10, "M"), 216)
        self.assertEqual(qr.data_codewords(10, "L"), 274)
        self.assertEqual(qr.capacity("M"), 213)
        self.assertEqual(qr.capacity("L"), 271)

    def test_too_long_for_one_code_raises(self):
        with self.assertRaises(ValueError):
            qr.encode(b"x" * (qr.capacity("M") + 1))

    def test_chain_makes_one_part_for_a_short_text_and_numbered_parts_for_a_long_one(self):
        self.assertEqual(qr.chain("short"), ["short"])
        parts = qr.chain("é" * 300, limit=100)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p.encode()) <= 100 for p in parts))
        self.assertEqual([p.split("|", 1)[0] for p in parts], ["%d/%d" % (i + 1, len(parts)) for i in range(len(parts))])
        self.assertEqual("".join(p.split("|", 1)[1] for p in parts), "é" * 300)


@unittest.skipIf(cv2 is None, "no OpenCV reader in this Python")
class Decodes(unittest.TestCase):
    def test_every_version_and_level_decodes_to_its_payload(self):
        rnd = random.Random(7)
        for ecl in ("L", "M"):
            for ver in range(1, qr.MAX_VERSION + 1):
                n = (qr.data_codewords(ver, ecl) * 8 - 4 - (8 if ver < 10 else 16)) // 8  # the most that version holds
                text = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789 /_.-~:{}\",") for _ in range(n))
                modules = qr.encode(text, ecl)
                self.assertEqual(len(modules), ver * 4 + 17, (ecl, ver))
                self.assertEqual(decode(modules), text, (ecl, ver))

    def test_utf8_decodes(self):
        self.assertEqual(decode(qr.encode("café ☤ breadcrumbs")), "café ☤ breadcrumbs")

    def test_a_chain_decodes_part_by_part_and_joins_back(self):
        text = "".join(chr(97 + i % 26) for i in range(700))
        parts = qr.chain(text, limit=150)
        got = [decode(qr.encode(p)) for p in parts]
        self.assertEqual(got, parts)
        self.assertEqual("".join(g.split("|", 1)[1] for g in got), text)


if __name__ == "__main__":
    unittest.main()

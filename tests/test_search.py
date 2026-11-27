from collections import defaultdict
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import os
import random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from solders.pubkey import Pubkey

from vanity.__main__ import derive, main, self_test
from vanity.cpu import CPU
from vanity.opencl import OpenCL
from vanity.patterns import CAP, Pattern, prefix_index
from vanity.progress import open_progress


BASE = Pubkey(bytes(range(32)))
TAG = "0123456789abcdef"


class PatternTests(unittest.TestCase):
    def test_pattern_grammar(self):
        self.assertTrue(Pattern.parse("Nova[CAP]...[CAP](?i:Star)").matches("NovaZxKsTAr"))
        self.assertTrue(Pattern.parse("Nova[CAP^SAM]...").matches("NovaZ"))
        self.assertFalse(Pattern.parse("Nova[CAP^SAM]...").matches("NovaS"))
        self.assertTrue(Pattern.parse("...x?").matches("42xa"))
        self.assertFalse(Pattern.parse("abc...cde").matches("abcde"))
        self.assertTrue(Pattern.parse("abc*def").matches("abcdef"))
        self.assertTrue(Pattern.parse("(?i)Nova...Star").matches("novAZstAR"))
        for spec in ("OwO", "foo...bar...baz", "foo[!]", "ab$", "z" * 44, "..."):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                Pattern.parse(spec)

    def test_prefix_index_keeps_real_prefixes(self):
        randomizer = random.Random(12)
        for zeros in (0, 1, 2, 8, 16, 31, 32):
            digest = bytes(zeros) + randomizer.randbytes(32 - zeros)
            address = str(Pubkey(digest))
            values = [Pattern.parse(address[:length]) for length in (1, 2, 5, len(address))]
            table, groups = prefix_index(values)
            self.assertEqual(table[int.from_bytes(digest[:2], "big") * groups], 15)


def check_backend(test, backend):
    self_test(backend)
    # Nonzero high counter words, a low-word wrap, partial masks and a second pattern group.
    start, size = 2 ** 47 - 7, 400
    addresses = [derive(BASE, TAG, start + offset)[1] for offset in range(size)]
    labels = [address[:6] + "..." + address[-6:] for address in addresses[9:46]]
    labels.extend(("...1", "a", "[CAP]", "2...(?i:a)", "?[DIGIT]", "1", addresses[2] + "$", "..." + addresses[3][-9:]))
    patterns = tuple(map(Pattern.parse, labels))
    quotas = [0 if index % 8 == 0 else CAP for index in range(len(patterns))]
    backend.configure(BASE, TAG, patterns)
    got = backend.batch(start, size, quotas)
    expected = defaultdict(list)
    for index, (pattern, quota) in enumerate(zip(patterns, quotas)):
        if quota:
            for offset, address in enumerate(addresses):
                if pattern.matches(address):
                    expected[index].append(offset)
    test.assertEqual(got.keys(), expected.keys())
    for index, offsets in expected.items():
        test.assertEqual(len(got[index]), min(len(offsets), quotas[index]))
        test.assertTrue(set(got[index]).issubset(offsets))


class BackendTests(unittest.TestCase):
    def test_cpu(self):
        engine = CPU(2)
        try:
            check_backend(self, engine)
        finally:
            engine.close()

    def test_opencl(self):
        try:
            import pyopencl as cl
            platforms = cl.get_platforms()
        except ImportError:
            self.skipTest("PyOpenCL missing")
        except cl.LogicError as error:
            self.skipTest(str(error))
        if not any(platform.get_devices() for platform in platforms):
            self.skipTest("OpenCL device missing")
        engine = OpenCL(0, allow_cpu=True)
        try:
            check_backend(self, engine)
        finally:
            engine.close()


class PersistenceTests(unittest.TestCase):
    def test_resume_and_restore_json(self):
        with TemporaryDirectory() as scratch:
            directory = Path(scratch)
            patterns = (Pattern.parse("?"),)
            with open_progress(directory, BASE, patterns) as state:
                tag = state.tag
                seed, address = derive(BASE, tag, 7)
                state.save(12, {0: [(seed, address)]})
                result = next(state.folder.glob("*.json"))
                result.unlink()
            with open_progress(directory, BASE, patterns) as state:
                self.assertEqual((state.offset, state.tag), (12, tag))
                self.assertEqual(state.hits[0], [(seed, address)])
                self.assertEqual(json.loads(result.read_text())["matches"][0]["receiving_address"], address)
                engine = CPU(1)
                engine.configure(BASE, state.tag, patterns)
                self.assertEqual(engine.batch(state.offset, 1, (1,)), {0: [0]})
            with open_progress(directory, BASE, (Pattern.parse("a"),)) as state:
                self.assertEqual(state.offset, 0)

    def test_cli_stops_and_benchmarks_without_writes(self):
        with TemporaryDirectory() as scratch:
            directory = Path(scratch)
            (directory / "address.txt").write_text(str(BASE))
            (directory / "strings.txt").write_text("?\n??\n")
            before = Path.cwd()
            os.chdir(directory)
            stdout = StringIO()
            args = ["search.py", "--backend", "cpu", "--workers", "1", "--plain"]
            try:
                with redirect_stdout(stdout), patch("sys.argv", args + ["--benchmark", "--seconds", "0.01"]):
                    self.assertEqual(main(), 0)
                self.assertFalse((directory / "vanity-progress.sqlite3").exists())
                with redirect_stdout(stdout), patch("sys.argv", args):
                    self.assertEqual(main(), 0)
                self.assertIn("All patterns matched", stdout.getvalue())
                results = sorted(directory.glob("results/*/*.json"))
                self.assertEqual(len(results), 2)
                self.assertTrue(all(len(json.loads(path.read_text())["matches"]) == CAP for path in results))
                with redirect_stdout(stdout), patch("sys.argv", args):
                    self.assertEqual(main(), 0)
                self.assertIn("All patterns already matched", stdout.getvalue())
            finally:
                os.chdir(before)

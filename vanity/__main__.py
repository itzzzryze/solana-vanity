"""Offline seeded Solana vanity search and benchmark."""
import argparse
from contextlib import nullcontext
from math import isfinite
import os
from pathlib import Path
from secrets import token_hex
import signal
import sqlite3
import sys
from time import monotonic

from solders.pubkey import Pubkey
from solders.system_program import ID

from .cpu import CPU
from .cuda import CUDA
from .dashboard import Dashboard
from .opencl import OpenCL
from .patterns import CAP, Pattern, read_patterns
from .progress import CHECKPOINT_SECONDS, open_progress

MAX_COUNTER = 2 ** 64


class Pause:
    requested = False
    exit = False

    def request(self, _signum, _frame):
        self.requested = self.exit = True


def backends(selection, device, workers):
    choices = ("cuda", "opencl", "cpu") if selection == "auto" else (selection,)
    for choice in choices:
        if choice == "cuda":
            yield choice, lambda: CUDA(device)
        elif choice in ("opencl", "amd", "intel"):
            vendor = choice if choice in ("amd", "intel") else None
            yield choice, lambda: OpenCL(device, vendor)
        else:
            yield choice, lambda: CPU(workers)


def derive(base, tag, counter):
    seed = f"{tag}{counter:016x}"
    return seed, str(Pubkey.create_with_seed(base, seed, ID))


def self_test(engine):
    """Exercise the actual backend against SDK-derived known prefix and suffix matches."""
    base = Pubkey(bytes(range(32)))
    tag, start, length = "0011223344556677", 2 ** 40 - 4, 64
    addresses = [derive(base, tag, start + i)[1] for i in range(length)]
    specs = (addresses[2][:6], "..." + addresses[7][-6:],
             addresses[12][:3] + "..." + addresses[12][-3:], addresses[21] + "$")
    patterns = tuple(map(Pattern.parse, specs))
    expected = {i: [n for n, address in enumerate(addresses) if pattern.matches(address)]
                for i, pattern in enumerate(patterns)}
    engine.configure(base, tag, patterns)
    quotas = (CAP,) * len(patterns)
    if engine.batch(start, length, quotas) != expected:
        raise RuntimeError("backend self-test disagreed with Solana SDK")


def choose_backend(args, base, tag, patterns, emit):
    errors = []
    for name, open_engine in backends(args.backend, args.device, args.workers):
        engine = None
        try:
            engine = open_engine()
            self_test(engine)
            engine.configure(base, tag, patterns)
            return engine
        except (ImportError, OSError, RuntimeError, AttributeError, ValueError) as error:
            if engine is not None:
                engine.close()
            detail = str(error).strip()
            summary = next(iter(detail.splitlines()), type(error).__name__)
            message = f"{name}: {summary}"
            errors.append(f"{name}: {detail}")
            if args.backend == "auto":
                emit(f"{message} · trying another backend")
    raise RuntimeError("No working backend. " + " | ".join(errors))


def next_size(elapsed, size, maximum):
    ratio = max(.25, min(4, .10 / max(.001, elapsed)))
    return min(maximum, max(1024, int(size * ratio)))


def verified_batch(engine, base, tag, patterns, offset, size, quotas):
    batch = engine.batch(offset, size, quotas)
    verified = {}
    for index, candidates in batch.items():
        for candidate in candidates:
            seed, address = derive(base, tag, offset + candidate)
            if not patterns[index].matches(address):
                raise RuntimeError(f"Result for {patterns[index].label!r} failed Solana SDK verification")
            verified.setdefault(index, []).append((seed, address))
    return verified


def search(args, base, patterns, state, screen, pause):
    def emit(message):
        if screen:
            screen.event(message)
        else:
            print(message, flush=True)

    def complete():
        return all(state.hits.values())

    if screen:
        screen.folder, screen.hits = state.folder, state.hits
        screen.checked = screen.checkpoint = state.offset
    else:
        emit(f"Results: {state.folder}")
    if complete():
        return f"All patterns already matched · {state.offset:,} checked · {state.folder}"
    start = monotonic()
    active_time = 0.0
    while not pause.exit and not complete():
        if screen:
            screen.state = "Preparing search"
            screen.draw(force=True)
        engine = choose_backend(args, base, state.tag, patterns, emit)
        offset = state.offset
        try:
            emit(f"{engine.name} · verified · resuming at {offset:,}")
            if screen:
                screen.device, screen.state = engine.name, "Searching"
                screen.init_segment(offset)
            segment = last_save = monotonic()
            size = 32768
            limits = [CAP - len(state.hits[index]) for index in range(len(patterns))]
            while not pause.requested and offset < MAX_COUNTER and not complete():
                if args.hours and active_time + monotonic() - segment >= args.hours * 3600:
                    pause.requested = pause.exit = True
                    break
                count = min(size, MAX_COUNTER - offset)
                before = monotonic()
                hits = verified_batch(engine, base, state.tag, patterns, offset, count, limits)
                offset += count
                now = monotonic()
                size = next_size(now - before, count, engine.maximum_batch)
                if hits or now - last_save >= CHECKPOINT_SECONDS:
                    state.save(offset, hits)
                    last_save = now
                    if hits:
                        for index in hits:
                            limits[index] = CAP - len(state.hits[index])
                            emit(f"{patterns[index].label} · {len(state.hits[index])}/{CAP} saved")
                    if not screen:
                        rate = count / max(.001, now - before)
                        emit(f"{offset:,} checked · {rate:,.0f}/sec · "
                             f"{sum(map(bool, state.hits.values()))}/{len(patterns)} patterns matched")
                if screen:
                    screen.update(now - segment, offset, state.offset)
                    screen.poll(pause, running=True)
            if offset != state.offset:
                state.save(offset, {})
            elapsed = monotonic() - segment
            active_time += elapsed
            if screen:
                screen.checkpoint = screen.checked = offset
                screen.finish_segment(elapsed)
        finally:
            engine.close()
        if offset == MAX_COUNTER and not complete():
            raise RuntimeError("Search exhausted its seed counter")
        if screen and not pause.exit:
            screen.state = "Complete" if complete() else "Paused"
            screen.event(f"Saved at {state.offset:,}")
            if screen.wait(pause) == "resume":
                pause.requested = False
        else:
            break
    outcome = "All patterns matched" if complete() else "Search saved"
    return f"{outcome} · {state.offset:,} checked · {monotonic() - start:,.1f}s elapsed · {state.folder}"


def benchmark(args, base, patterns, pause):
    engine = choose_backend(args, base, token_hex(8), patterns, lambda line: print(line, flush=True))
    try:
        print(f"{engine.name} · {len(patterns)} patterns · {args.seconds:g}s benchmark", flush=True)
        offset, size = 0, 32768
        start = report = monotonic()
        quotas = (CAP,) * len(patterns)
        while not pause.requested and monotonic() - start < args.seconds:
            before = monotonic()
            engine.batch(offset, size, quotas)
            offset += size
            now = monotonic()
            size = next_size(now - before, size, engine.maximum_batch)
            if now - report >= 2:
                print(f"{offset:,} checked · {offset / (now - start):,.0f}/sec", flush=True)
                report = now
        elapsed = monotonic() - start
        print(f"{offset:,} checked · {offset / max(.001, elapsed):,.0f}/sec average", flush=True)
    finally:
        engine.close()


def positive_float(text):
    value = float(text)
    if not isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["auto", "cuda", "opencl", "amd", "intel", "cpu"], default="auto")
    parser.add_argument("--device", type=int, default=0, help="zero-based GPU index for the selected backend")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1), help="CPU worker processes")
    parser.add_argument("--hours", type=positive_float, help="save and quit after this much active search time")
    parser.add_argument("--plain", action="store_true", help="use text logs")
    parser.add_argument("--benchmark", action="store_true", help="measure throughput without saving progress")
    parser.add_argument("--seconds", type=positive_float, default=15, help="benchmark duration")
    args = parser.parse_args()
    if not 1 <= args.workers <= 61 or args.device < 0:
        parser.error("--workers must be 1–61 and --device must be nonnegative")

    directory = Path.cwd()
    pause = Pause()
    signal.signal(signal.SIGINT, pause.request)
    signal.signal(signal.SIGTERM, pause.request)
    try:
        address = (directory / "address.txt").read_text(encoding="utf-8-sig").strip()
        try:
            base = Pubkey.from_string(address)
        except ValueError as error:
            raise ValueError("address.txt must contain one Solana public address") from error
        patterns = read_patterns(directory / "strings.txt")
        if args.benchmark:
            benchmark(args, base, patterns, pause)
        else:
            with open_progress(directory, base, patterns) as state:
                with nullcontext() if args.plain else Dashboard(patterns) as screen:
                    outcome = search(args, base, patterns, state, screen, pause)
                print(outcome, flush=True)
    except (ValueError, OSError, RuntimeError, sqlite3.Error, ImportError) as error:
        print(f"Search stopped. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

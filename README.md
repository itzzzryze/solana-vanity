# Solana Vanity

Search offline for Solana receiving addresses derived from a public address you control. Every candidate uses Solana's System Program seeded-address derivation. The search never asks for wallet recovery words, connects to a ledger, or signs a transaction. A saved result contains the receiving address, base address, owner program, and public derivation seed.

## Start on Windows

1. Extract the portable Windows x64 ZIP from a successful GitHub Actions run or release. If you cloned or downloaded the source instead, install 64-bit Python 3.12 or 3.13 and run `Setup.cmd` once.
2. Put **one Solana public address** in `address.txt`. Use a base address whose signer you control.
3. Put **one pattern per line** in `strings.txt`. Lines starting with `#` and blank lines are ignored.
4. Open Windows Terminal in that folder and run `Search.cmd`. Run `Benchmark.cmd` to measure throughput with your patterns without writing progress or results.

The process reads both text files from the directory where you run the CMD, including when you call the launcher by its full path. It saves results in `results\` and the checkpoint in `vanity-progress.sqlite3` in that same directory. Run searches using a separate working directory to keep different configurations organized.

## Patterns

| Line in `strings.txt` | What it matches |
| --- | --- |
| `Nova` | Starts with the exact text `Nova` |
| `...Star` | Ends with the exact text `Star` |
| `Nova...Star` | Starts with `Nova` and ends with `Star`, with any number of characters between them, including zero |
| `Nova[CAP]...[CAP](?i:Star)` | Starts with `Nova` and a capital, then ends with a capital and any capitalization of `Star` |
| `Nova[CAP^SAM]...` | Starts with `Nova` followed by a capital other than S, A or M |
| `N[ov]?a...Star` | Starts with N, either o or v, one arbitrary Base58 character, then a, and ends with `Star` |
| `(?i)nova...star` | Matches both sides case-insensitively |

A gap can be written as `...` or `*`. A pattern supports one gap. Left and right positions never overlap. `?` consumes one Base58 character. Use `[CAP]`, `[LOWER]`, `[DIGIT]`, or a custom class such as `[abc]`. Use `(?i:...)` around a sequence of literal characters or character classes for case-insensitive matching, or start the whole pattern with `(?i)`. Append `$` to a pattern without a gap to require a full-address match. Up to 256 unique patterns are supported.

Base58 is case-sensitive and uses:

```text
123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz
```

It omits `0`, `O`, `I` and lowercase `l`. Case-insensitive matching only adds an alternate case if it exists in Base58. Long patterns, especially exact full-address matches, can take impractically long.

## Backends

| Option | Required hardware and software |
| --- | --- |
| `--backend auto` | Tries CUDA, then OpenCL, then CPU |
| `--backend cuda` | NVIDIA GPU of compute capability 7.5 or newer and a driver that supports the bundled PTX |
| `--backend amd` | AMD GPU and vendor driver exposing a compiling OpenCL 1.2 or newer GPU device |
| `--backend intel` | Intel GPU and vendor driver exposing a compiling OpenCL 1.2 or newer GPU device |
| `--backend opencl` | Any supported OpenCL GPU, including compatible older NVIDIA GPUs |
| `--backend cpu` | 64-bit CPU and Python. Set worker count with `--workers 8`. |

Use `--device 1` to select the second device in the chosen backend's device list. CUDA uses the installed display driver and the bundled PTX. OpenCL uses the vendor's runtime and compiles the shared kernel on first use. Linux users can install `requirements.txt` and optionally `requirements-opencl.txt` in a virtual environment and run `python search.py`. Each backend checks known patterns against SDK-derived addresses before processing your checkpoint. AMD and Intel support depends on the installed vendor driver and needs physical device validation.

## Dashboard and saved progress

- The Aurora dashboard adapts to window width and height. Use arrows or J/K to select a pattern and Enter to view its saved addresses. Use Escape to return, O to open results on Windows, and ? for help. Add `--plain` for text logs.
- The scanner writes one JSON file per matched pattern, with up to ten results per file. It stops checking a pattern once that file reaches ten. The run ends when **every pattern has at least one result**. An address can appear in two files if both patterns match it.
- P pauses search work and saves progress. P again resumes. Q or Ctrl+C saves and exits. `Search.cmd --hours 8` saves and exits after about eight hours of active search. A hard shutdown can replay work since the previous checkpoint, normally about five seconds plus a running batch. Stored matches and the checkpoint are committed together and JSON exports are recreated on restart.
- Reuse the same working directory, base address and ordered pattern list to resume. Switching backends is supported. Editing or reordering patterns opens a separate checkpoint and result collection.

## Receiving and recovering SOL

These addresses are System Program accounts derived from your base address. Retain the JSON for any address you fund. Moving native SOL out requires a wallet or transaction tool that can build `TransferWithSeed` and get a signature from the base account. Verify on devnet with your wallet and signing device before funding a derived address. The search does not manage wallet balances or transfers.

- [Solana System Program instructions](https://solana.com/docs/core/programs/builtin-programs)
- [Solana TransferWithSeed API](https://docs.rs/solana-system-interface/latest/solana_system_interface/instruction/fn.transfer_with_seed.html)
- [NVIDIA PTX compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html)

## Build and test

Run `python -m unittest discover -s tests -v`. If PyOpenCL and a working runtime are installed, the suite also tests the OpenCL kernel. To regenerate CUDA PTX, install the CUDA Toolkit and run `python compile_cuda.py`.

To make a portable ZIP, download the [official Python 3.13 Windows x64 embeddable package](https://www.python.org/downloads/windows/) and run `python package_windows.py path/to/python-embed-amd64.zip`. The builder stages the Windows x64 dependency wheels and writes the archive under `dist/`.

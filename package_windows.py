"""Create a portable Windows x64 ZIP from a Python 3.13 embeddable ZIP."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("python_embed", type=Path, help="official Windows x64 Python 3.13 embeddable ZIP")
    parser.add_argument("--wheelhouse", type=Path, action="append", default=[], help="offline wheel directory")
    options = parser.parse_args()
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    with TemporaryDirectory() as scratch:
        folder = Path(scratch) / "Solana-Vanity"
        runtime = folder / "runtime"
        runtime.mkdir(parents=True)
        with ZipFile(options.python_embed) as archive:
            archive.extractall(runtime)
        pth = runtime / "python313._pth"
        if not pth.is_file():
            parser.error("expected the Python 3.13 embeddable package")
        pth.write_text("python313.zip\n.\nLib/site-packages\n..\n", encoding="ascii")
        pip_args = []
        if options.wheelhouse:
            pip_args.append("--no-index")
            for path in options.wheelhouse:
                pip_args.extend(("--find-links", str(path.resolve())))
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "--platform", "win_amd64", "--implementation", "cp", "--python-version", "3.13",
                        "--only-binary=:all:", "--target", str(runtime / "Lib/site-packages"),
                        *pip_args, "-r", str(ROOT / "requirements.txt"),
                        "-r", str(ROOT / "requirements-opencl.txt")], check=True)
        shutil.copytree(ROOT / "vanity", folder / "vanity", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("README.md", "Search.cmd", "Benchmark.cmd", "search.py", "strings.txt"):
            shutil.copy2(ROOT / name, folder)
        (folder / "address.txt").touch()
        print(shutil.make_archive(str(output / "Solana-Vanity-Windows-x64"), "zip", scratch, folder.name))


if __name__ == "__main__":
    main()

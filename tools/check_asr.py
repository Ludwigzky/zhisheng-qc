# -*- coding: utf-8 -*-
"""Check the engine actually selected by this demo; optionally decode a WAV."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import asr_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", help="Optional WAV path; no reference answer is sent to the recognizer")
    args = parser.parse_args()
    engine = asr_engine.get_engine()
    try:
        print(json.dumps(engine.info(), ensure_ascii=False, indent=2))
        if not engine.available:
            return 1
        if args.wav:
            result = engine.transcribe(str(Path(args.wav).resolve()))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("text") and not result.get("error") else 1
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    sys.exit(main())

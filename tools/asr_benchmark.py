# -*- coding: utf-8 -*-
"""Replay annotated phone WAVs through the real demo pipeline, without touching demo.db.

JSONL: {"id":"a1","wav":"a1.wav","template":"ipqc","current_key":"span",
        "reference":"跨度22501","expected":{"span":22501},"speaker":"p1","condition":"quiet"}
Paths are relative to the manifest. Gold reference/values are used ONLY after decoding.
"""
import argparse
import io
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import asr_engine
import cnnum
import judge
import lab
import seed

TEMPLATES = {"iqc": seed.TPL_IQC, "ipqc": seed.TPL_IPQC, "fqc": seed.TPL_FQC}


def same_value(actual, expected):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-8)
    return actual == expected


def run(args):
    manifest = Path(args.manifest).resolve()
    samples = [json.loads(line) for line in manifest.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not samples:
        raise ValueError("标注清单为空")
    # Validate the entire batch before loading a large model or starting a replay.
    for sample in samples:
        if sample.get("template", "ipqc") not in TEMPLATES:
            raise ValueError("未知模板: %s" % sample.get("template"))
        if not isinstance(sample.get("expected"), dict) or not sample["expected"]:
            raise ValueError("每条录音需要非空 expected 字段真值")
        schema = TEMPLATES[sample.get("template", "ipqc")]
        keys = {f["key"] for f in judge.flatten_fields(schema) if not f.get("derived")}
        if not set(sample["expected"]).issubset(keys):
            raise ValueError("expected 含不属于模板的可填字段")
        if sample.get("current_key") and sample["current_key"] not in keys:
            raise ValueError("current_key 不属于模板可填字段")
        if any(isinstance(v, (int, float)) and (isinstance(v, bool) or not math.isfinite(v))
               for v in sample["expected"].values()):
            raise ValueError("expected 数值必须有限且不能是布尔值")
        if not (manifest.parent / sample["wav"]).is_file():
            raise ValueError("录音不存在: %s" % sample["wav"])
    os.environ["ASR_ENGINE"] = args.engine
    engine = asr_engine.get_engine()
    if not engine.available:
        raise RuntimeError("识别引擎不可用: %s" % engine.info().get("reason"))
    engine_info = engine.info()
    import app as server
    results = []
    original = {name: getattr(server.db, name) for name in ("DATA_DIR", "AUDIO_DIR", "REPORT_DIR")}
    grammar_dir = server.GRAMMAR_DIR
    try:
        with tempfile.TemporaryDirectory(prefix="qc-asr-benchmark-") as directory:
            for name in original:
                setattr(server.db, name, directory)
            server.GRAMMAR_DIR = directory
            for i, sample in enumerate(samples):
                schema = TEMPLATES[sample.get("template", "ipqc")]
                payload = (manifest.parent / sample["wav"]).read_bytes()
                # No reference/expected fields are passed into capture, context, grammar or decoding.
                form = {"audio": (io.BytesIO(payload), "sample.wav"), "asr_mode": args.mode}
                if sample.get("current_key"):
                    form["current_key"] = sample["current_key"]
                started = time.monotonic()
                with server.app.test_request_context("/api/asr/audio", method="POST", data=form):
                    recognized = server._process_audio(args.denoise, schema)
                elapsed = int((time.monotonic() - started) * 1000)
                parsed = cnnum.parse_utterance(recognized.get("text") or "", judge.flatten_fields(schema),
                                               sample.get("current_key"),
                                               asr_confidence=recognized.get("confidence", 0))
                assignments = [a for a in parsed["assignments"] if not a.get("derived")]
                got = {a["key"]: a["value"] for a in assignments}
                expected = sample["expected"]
                correct = sum(k in got and same_value(got[k], v) for k, v in expected.items())
                automatic = [a for a in assignments if a["confidence"] >= .75]
                false_auto = sum(a["key"] not in expected or not same_value(a["value"], expected[a["key"]])
                                 for a in automatic)
                row = {"id": sample.get("id", str(i + 1)), "speaker": sample.get("speaker"),
                       "condition": sample.get("condition"), "wav": sample["wav"],
                       "reference": sample.get("reference"), "expected": expected, "got": got,
                       "correct_fields": correct, "expected_fields": len(expected),
                       "auto_fields": len(automatic), "false_auto_fields": false_auto,
                       "pending_fields": sum(a["confidence"] < .75 for a in assignments),
                       "cer": lab.cer(sample["reference"], recognized.get("text") or "")
                              if sample.get("reference") else None,
                       "elapsed_ms": elapsed, "result": recognized}
                results.append(row)
                print("%s: fields %d/%d, false-auto %d, %d ms" %
                      (row["id"], correct, len(expected), false_auto, elapsed), file=sys.stderr)
    finally:
        for name, value in original.items():
            setattr(server.db, name, value)
        server.GRAMMAR_DIR = grammar_dir
        engine.close()
    total = sum(r["expected_fields"] for r in results)
    autos = sum(r["auto_fields"] for r in results)
    latencies = sorted(r["elapsed_ms"] for r in results)
    summary = {"samples": len(results), "expected_fields": total,
               "field_accuracy": sum(r["correct_fields"] for r in results) / total,
               "auto_fields": autos, "false_auto_fields": sum(r["false_auto_fields"] for r in results),
               "false_auto_rate": sum(r["false_auto_fields"] for r in results) / autos if autos else None,
               "pending_fields": sum(r["pending_fields"] for r in results),
               "missing_fields": sum(r["expected_fields"] - sum(k in r["got"] for k in r["expected"]) for r in results),
               "mean_elapsed_ms": round(sum(latencies) / len(latencies), 1),
               "p95_elapsed_ms": latencies[max(0, math.ceil(len(latencies) * .95) - 1)]}
    report = {"engine": args.engine, "engine_info": engine_info, "mode": args.mode, "denoise": args.denoise,
              "manifest": str(manifest), "summary": summary, "samples": results}
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("report: %s" % output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--engine", choices=["auto", "sapi", "faster-whisper", "sherpa-onnx"], default="auto")
    parser.add_argument("--mode", choices=["grammar", "dictation"], default="grammar")
    parser.add_argument("--denoise", action="store_true")
    parser.add_argument("--output", default="_probe/asr-benchmark.json")
    try:
        run(parser.parse_args())
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        parser.exit(1, "评测失败: %s\n" % exc)

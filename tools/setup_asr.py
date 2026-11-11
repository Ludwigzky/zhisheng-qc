# -*- coding: utf-8 -*-
"""Install the Mandarin offline engine inside this project, then verify it.

No system packages or demo database are changed. Model weights are downloaded
only by this explicit setup command, never by recognition requests.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[1]
ENV_DIR = ROOT / ".venv-asr"
MODEL_DIR = ROOT / "models" / "paraformer-zh"
RUNTIME_VERSION = "2.0.1"
MODEL_REPO = "csukuangfj/sherpa-onnx-paraformer-zh-2024-03-09"
MODEL_REVISION = "906992d326ebf0c5171cde675aa0902be9e5bc6c"
FILES = [
    {"name": "model.int8.onnx", "size": 227330205,
     "sha256": "90bc03034ae1bef9575f8cc798cd1519c8be8aa9e8b458a033e32017ff4d584c"},
    {"name": "tokens.txt", "size": 75354, "git_blob_sha1": "a9216726314d8a0ea179afcede272f20a0c97bed"},
]


def verified(path, spec):
    if not path.is_file() or path.stat().st_size != spec["size"]:
        return False
    if "sha256" in spec:
        digest = hashlib.sha256()
    else:
        digest = hashlib.sha1(("blob %d\0" % spec["size"]).encode("ascii"))
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == spec.get("sha256", spec.get("git_blob_sha1"))


def download(spec):
    target = MODEL_DIR / spec["name"]
    if verified(target, spec):
        print("[model] already verified: %s" % target.name, flush=True)
        return
    partial = target.with_name(target.name + ".part")
    url = "https://huggingface.co/%s/resolve/%s/%s" % (MODEL_REPO, MODEL_REVISION, spec["name"])
    for attempt in range(1, 4):
        try:
            print("[model] downloading %s (%d bytes), attempt %d" %
                  (target.name, spec["size"], attempt), flush=True)
            request = urllib.request.Request(url, headers={"User-Agent": "qc-demo-asr-setup/1"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                count, next_log = 0, 32 * 1024 * 1024
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    count += len(block)
                    if count > spec["size"]:
                        raise ValueError("下载内容超过预期大小")
                    if count >= next_log:
                        print("[model] %s %.0f%%" % (target.name, count * 100.0 / spec["size"]), flush=True)
                        next_log += 32 * 1024 * 1024
            if not verified(partial, spec):
                raise ValueError("模型大小或内容校验不匹配")
            partial.replace(target)
            print("[model] verified: %s" % target.name, flush=True)
            return
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1)


def main():
    if sys.version_info < (3, 9):
        raise RuntimeError("需要 Python 3.9 或更高版本")
    python = ENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        print("[runtime] creating project environment", flush=True)
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(str(ENV_DIR))
    print("[runtime] installing sherpa-onnx==%s" % RUNTIME_VERSION, flush=True)
    subprocess.run([str(python), "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                    "--no-cache-dir", "--only-binary=:all:", "--index-url", "https://pypi.org/simple",
                    "sherpa-onnx==" + RUNTIME_VERSION], check=True)
    # Reuse this computer's existing demo dependencies when available; install
    # missing packages only in the local environment when setting up elsewhere.
    check = subprocess.run([str(python), "-c", "import flask, numpy, scipy, cryptography; from PIL import Image"], capture_output=True)
    if check.returncode:
        subprocess.run([str(python), "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                        "--no-cache-dir", "--only-binary=:all:", "--index-url", "https://pypi.org/simple",
                        "-r", str(ROOT / "requirements.txt")], check=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for spec in FILES:
        download(spec)
    metadata = {"model_id": MODEL_REPO, "revision": MODEL_REVISION,
                "runtime_version": RUNTIME_VERSION, "files": FILES}
    (MODEL_DIR / "model-info.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    # Publish the default only after a real native runtime/model initialization.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", ASR_SHERPA_MODEL_DIR=str(MODEL_DIR))
    subprocess.run([str(python), "-c",
                    "import sys,json; sys.path.insert(0,'backend'); from asr_sherpa import SherpaOnnxEngine; "
                    "e=SherpaOnnxEngine(); print(json.dumps(e.info(),ensure_ascii=False)); "
                    "ok=e.available; e.close(); sys.exit(0 if ok else 1)"], cwd=str(ROOT), env=env, check=True)
    configuration = {"engine": "sherpa-onnx", "model_dir": "models/paraformer-zh", "num_threads": 2}
    pending = ROOT / "asr-config.json.tmp"
    pending.write_text(json.dumps(configuration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(ROOT / "asr-config.json")
    print("[ready] Mandarin model loaded; start.bat / run-https.bat will use the local environment", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ASR setup failed: %s" % exc, file=sys.stderr, flush=True)
        sys.exit(1)

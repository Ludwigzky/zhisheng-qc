# -*- coding: utf-8 -*-
"""ASR 信号回归：末位数字不能因分帧被截掉。无需麦克风或识别模型。"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import audio  # noqa: E402


def main():
    rng = np.random.RandomState(27)
    # 无抑制时应逐样本重建，覆盖非整 hop 长度及首尾脉冲。
    for n in (512, 513, 640, 16000, 16001, 16127, 16500):
        x = rng.uniform(-0.3, 0.3, n).astype(np.float32)
        x[0], x[-1] = 0.75, -0.65
        y, _ = audio.denoise(16000, x, over_sub=0, floor_gain=1, smooth=1)
        assert len(y) == n
        assert np.all(np.isfinite(y))
        assert np.max(np.abs(x - y)) < 1e-6, (n, x[-1], y[-1])
    print("OK: no-op STFT reconstructs every sample including both edges")

    # 真降噪也不能将不足一 hop 的尾段静默清零。
    x = np.zeros(16127, dtype=np.float32)
    t = np.arange(1800) / 16000.0
    x[-1800:] = 0.2 * np.sin(2 * np.pi * 350 * t + 0.3)
    y, _ = audio.denoise(16000, x)
    assert len(y) == len(x)
    assert np.mean(y[-128:] ** 2) > 1e-5
    assert np.all(np.isfinite(y)) and np.max(np.abs(y)) <= 1
    print("OK: end-of-utterance speech survives denoise")

    for n in (0, 100, 511):
        x = np.zeros(n, dtype=np.float32)
        y, info = audio.denoise(16000, x)
        assert not info["applied"] and np.array_equal(x, y)
    print("OK: short and empty recordings remain safe")


if __name__ == "__main__":
    main()

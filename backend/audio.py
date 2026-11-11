# -*- coding: utf-8 -*-
"""
音频处理：WAV 读写、重采样、质量指标、保守谱减降噪。

为什么要自己算指标：没有这些数字，"降噪有没有用"就只能靠听感，而听感和识别率经常相反。
"""
import io
import math
import os
import wave

import numpy as np

TARGET_SR = 16000


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------
def read_wav(path):
    """读 16-bit PCM WAV → (sr, float32[-1,1])。不是 WAV 抛 ValueError。"""
    with open(path, "rb") as f:
        head = f.read(12)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise ValueError("不是 WAV 文件")
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(n)
    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError("不支持的位深: %d" % width)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return sr, x.astype(np.float32)


def write_wav(path, sr, x):
    x = np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm)
    return path


def to_mono16k(sr, x):
    """重采样到 16k（优先 scipy 多相，退回线性插值）。"""
    if sr == TARGET_SR:
        return TARGET_SR, x.astype(np.float32)
    try:
        from scipy.signal import resample_poly
        g = math.gcd(int(sr), TARGET_SR)
        y = resample_poly(x, TARGET_SR // g, int(sr) // g)
        return TARGET_SR, np.asarray(y, dtype=np.float32)
    except Exception:
        n = int(round(len(x) * TARGET_SR / float(sr)))
        if n <= 1:
            return TARGET_SR, x.astype(np.float32)
        idx = np.linspace(0, len(x) - 1, n)
        return TARGET_SR, np.interp(idx, np.arange(len(x)), x).astype(np.float32)


# --------------------------------------------------------------------------
# 分帧
# --------------------------------------------------------------------------
def _frames(x, win, hop):
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx], n


def _dbfs(v):
    return 10.0 * math.log10(max(float(v), 1e-12))


# --------------------------------------------------------------------------
# 质量指标
# --------------------------------------------------------------------------
def analyze(sr, x):
    """返回一组可直接拿来做结论的数字。"""
    x = np.asarray(x, dtype=np.float32)
    n = len(x)
    if n == 0:
        return {"duration": 0.0, "rms_dbfs": -120.0, "peak_dbfs": -120.0,
                "noise_dbfs": -120.0, "speech_dbfs": -120.0, "snr_db": 0.0,
                "clipping_pct": 0.0, "speech_ratio": 0.0, "silence_ratio": 1.0,
                "samples": 0, "sample_rate": sr}
    win = int(0.025 * sr) or 400
    hop = int(0.010 * sr) or 160
    fr, _ = _frames(x, win, hop)
    energy = 10.0 * np.log10(np.mean(fr.astype(np.float64) ** 2, axis=1) + 1e-12)
    noise = float(np.percentile(energy, 10))
    speech = float(np.percentile(energy, 90))
    thr = max(noise + 6.0, -45.0)
    ratio = float(np.mean(energy > thr))
    peak = float(np.max(np.abs(x)))
    clip = float(np.mean(np.abs(x) >= 0.999))
    return {
        "duration": round(n / float(sr), 3),
        "samples": int(n),
        "sample_rate": int(sr),
        "rms_dbfs": round(_dbfs(np.mean(x.astype(np.float64) ** 2)), 1),
        "peak_dbfs": round(20.0 * math.log10(max(peak, 1e-6)), 1),
        "noise_dbfs": round(noise, 1),
        "speech_dbfs": round(speech, 1),
        "snr_db": round(speech - noise, 1),
        "clipping_pct": round(clip * 100.0, 2),
        "speech_ratio": round(ratio * 100.0, 1),
        "silence_ratio": round((1.0 - ratio) * 100.0, 1),
        "dc_offset": round(float(np.mean(x)), 4),
    }


# --------------------------------------------------------------------------
# 保守谱减（只做"适当降噪"，不追求听感）
# --------------------------------------------------------------------------
def _stft(x, n_fft, hop, win):
    # 居中补边，令原始首尾样本都落在窗口内部。直接对原信号分帧会使
    # Hann 首样本为零，并漏掉不足一个 hop 的尾音（数字末位尤其敏感）。
    pad = n_fft // 2
    padded = np.pad(x, (pad, pad), mode="reflect")
    extra = (-(len(padded) - n_fft)) % hop
    if extra:
        padded = np.pad(padded, (0, extra), mode="reflect")
    fr, cnt = _frames(padded, n_fft, hop)
    return np.fft.rfft(fr * win, axis=1), cnt


def _istft(spec, n_fft, hop, length, win):
    frames = np.fft.irfft(spec, n=n_fft, axis=1) * win
    covered = (frames.shape[0] - 1) * hop + n_fft
    out = np.zeros(covered, dtype=np.float64)
    wsum = np.zeros(covered, dtype=np.float64)
    w2 = win.astype(np.float64) ** 2
    for i in range(frames.shape[0]):
        s = i * hop
        out[s:s + n_fft] += frames[i]
        wsum[s:s + n_fft] += w2
    start = n_fft // 2
    trimmed = slice(start, start + length)
    return (out[trimmed] / np.maximum(wsum[trimmed], 1e-8)).astype(np.float32)


def denoise(sr, x, over_sub=1.8, floor_gain=0.10, noise_pct=15, smooth=3):
    """
    谱减 + 增益下限 + 时间平滑。
    floor_gain 别设太小（过度抑制会吃掉清辅音，反而伤识别）。
    """
    x = np.asarray(x, dtype=np.float32)
    n_fft, hop = 512, 128
    if len(x) < n_fft:
        return x, {"applied": False, "reason": "音频太短"}
    win = np.hanning(n_fft).astype(np.float32)
    spec, cnt = _stft(x, n_fft, hop, win)
    mag = np.abs(spec)
    noise = np.percentile(mag, noise_pct, axis=0, keepdims=True)
    gain = 1.0 - over_sub * noise / (mag + 1e-9)
    gain = np.clip(gain, floor_gain, 1.0)
    if smooth >= 3:
        k = np.ones(smooth) / float(smooth)
        gain = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 0, gain)
    y = _istft(spec * np.clip(gain, 0.0, 1.0), n_fft, hop, len(x), win)
    # 谱减可能瞬时过冲，做一次硬限幅，避免下游（WAV/ASR）看到越界样本
    y = np.clip(y, -1.0, 1.0)
    return y, {"applied": True, "frames": int(cnt),
               "mean_gain_db": round(float(20.0 * np.log10(max(float(np.mean(gain)), 1e-6))), 1),
               "over_sub": over_sub, "floor_gain": floor_gain}


def split_for_upload(data):
    """把浏览器上传的字节流交给 read_wav 前先落盘用。返回是否为 WAV。"""
    return data[:4] == b"RIFF" and data[8:12] == b"WAVE"

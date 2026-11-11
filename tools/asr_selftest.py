# -*- coding: utf-8 -*-
"""音频链路自检：合成语音样例 + 噪声，验证指标与降噪是真的在动。"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

import audio  # noqa: E402
import lab  # noqa: E402

OUT = os.path.join(HERE, "..", "_probe")
if not os.path.isdir(OUT):
    os.makedirs(OUT)

fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  BAD ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


SR = 16000


def speech_like(dur=1.6):
    """用谐波 + 包络合成一段“类语音”，只用来验证指标与降噪是否有效，不代表识别效果。"""
    t = np.arange(int(SR * dur)) / float(SR)
    f0 = 140 + 20 * np.sin(2 * np.pi * 1.7 * t)
    sig = np.zeros_like(t)
    for k, amp in enumerate([1.0, 0.55, 0.35, 0.22, 0.12], start=1):
        sig += amp * np.sin(2 * np.pi * k * f0 * t)
    env = 0.5 * (1 + np.sin(2 * np.pi * 3.1 * t - 1.2))
    env = np.clip(env, 0, 1) ** 2
    # 每 0.4s 一个“词”，中间留气口
    gate = (np.sin(2 * np.pi * 2.5 * t) > -0.2).astype(np.float32)
    return (sig * env * gate * 0.16).astype(np.float32)


def add_noise(x, snr_db, rng):
    noise = rng.normal(0, 1, len(x)).astype(np.float32)
    # 粉红化一点，更接近风机的谱
    b = np.cumsum(noise)
    b = (b - b.mean()) / (b.std() + 1e-9)
    noise = 0.6 * noise + 0.4 * b
    p_sig = float(np.mean(x ** 2))
    p_noise = float(np.mean(noise ** 2))
    target = p_sig / (10 ** (snr_db / 10.0))
    g = np.sqrt(target / (p_noise + 1e-12))
    return (x + g * noise).astype(np.float32)


print("=== 1. 中文数字与单位（回归）===")
check("十二点五", lab.cnnum.cn_to_number("十二点五"), 12.5)
check("两千两百", lab.cnnum.cn_to_number("两千两百"), 2200.0)

print("\n=== 2. WAV 往返 ===")
x = speech_like()
p = os.path.join(OUT, "synth_clean.wav")
audio.write_wav(p, SR, x)
sr2, y = audio.read_wav(p)
check("采样率一致", sr2, SR)
check("长度一致", len(y), len(x))
check("波形接近", float(np.max(np.abs(y - x))) < 0.001, round(float(np.max(np.abs(y - x))), 5))

print("\n=== 3. 指标：静音 / 干净 / 加噪 ===")
m_sil = audio.analyze(SR, np.zeros(SR, dtype=np.float32))
m_clean = audio.analyze(SR, x)
n5 = add_noise(x, 5.0, np.random.RandomState(7))
m_n5 = audio.analyze(SR, n5)
print("    干净:", m_clean["snr_db"], "dB  语音占比", m_clean["speech_ratio"], "%")
print("    5dB :", m_n5["snr_db"], "dB  语音占比", m_n5["speech_ratio"], "%")
check("静音判为低电平", m_sil["rms_dbfs"] < -100, m_sil["rms_dbfs"])
check("干净语音 SNR 为正", m_clean["snr_db"] > 0, m_clean["snr_db"])
check("加噪后 SNR 下降", m_n5["snr_db"] < m_clean["snr_db"], (m_clean["snr_db"], m_n5["snr_db"]))
check("能识别出语音段", m_n5["speech_ratio"] > 10, m_n5["speech_ratio"])

print("\n=== 4. 降噪支路是否真的提 SNR ===")
for snr in (10.0, 5.0, 0.0):
    noisy = add_noise(x, snr, np.random.RandomState(int(snr) + 3))
    den, info = audio.denoise(SR, noisy)
    before = audio.analyze(SR, noisy)["snr_db"]
    after = audio.analyze(SR, den)["snr_db"]
    print("    输入 SNR %4.0f dB → 输出 %5.1f dB （提升 %+.1f dB，平均增益 %s dB）"
          % (snr, after, after - before, info.get("mean_gain_db")))
    check("输入 %.0fdB 时降噪有提升" % snr, after > before + 1.0, round(after - before, 1))
    check("输出没有削顶" % (), float(np.max(np.abs(den))) <= 1.0)

print("\n=== 5. 降噪不该吃语音能量 ===")
den, _ = audio.denoise(SR, x)
e_in = float(np.mean(x ** 2))
e_out = float(np.mean(den ** 2))
ratio = 10 * np.log10(e_out / (e_in + 1e-12))
print("    能量变化: %+.1f dB" % ratio)
check("能量损失可控（>-12 dB）", ratio > -12.0, round(ratio, 1))

print("\n=== 6. 评测判分（用干净/污染文本模拟）===")
cases = [
    ("s1", "跨度 22501", True),
    ("s4", "主梁上拱度 26.5", True),
    ("s7", "焊缝外观合格", True),
    ("s5", "水平度 0.53", False),
    ("s1", "跨度 二万二千五百", False),
]
for sid, text, want in cases:
    j = lab.judge_sample(sid, text)
    check("判分 %-22s → %s" % (text, "命中" if j["ok"] else "未命中"),
          j["ok"] == want, "cer=%s got=%s" % (j["cer"], j["got"]))

print("\n=== 7. 字符错误率 ===")
check("完全相同 CER=0", lab.cer("跨度 22501", "跨度22501") == 0.0, lab.cer("跨度 22501", "跨度22501"))
check("错一字 CER>0", lab.cer("跨度 22501", "跨度 22502") > 0, lab.cer("跨度 22501", "跨度 22502"))

print("\n=== 结果 ===")
print("FAILS:", len(fails), fails if fails else "")
print("OK" if not fails else "FAIL")

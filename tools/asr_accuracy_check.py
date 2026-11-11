# -*- coding: utf-8 -*-
"""质检口报准确性回归。只使用确定文本/前端桩，不表示真人录音识别率。"""
import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import cnnum
import judge
import seed


class AccuracyRegression(unittest.TestCase):
    def test_engineering_label_spoken_index(self):
        fields = judge.flatten_fields(seed.TPL_IPQC)
        parsed = cnnum.parse_utterance("对角线 l 二二二五零零", fields, "diag_l2", asr_confidence=0)
        self.assertEqual([(a["key"], a["value"]) for a in parsed["assignments"]], [("diag_l2", 22500)])
        self.assertEqual(parsed["assignments"][0]["confidence"], 0)

    def parse(self, text, template=seed.TPL_IPQC, key=None, confidence=None):
        return cnnum.parse_utterance(text, judge.flatten_fields(template), key,
                                     asr_confidence=confidence)

    def test_spoken_and_mixed_numbers(self):
        cases = [
            ("跨度 二 二 五 零 一", 22501), ("跨度 2 2 五 零 1", 22501),
            ("跨度 二万二千五百零一", 22501), ("跨度 2万2千501", 22501),
            ("跨度 ２２５０１", 22501), ("跨度 幺 二 零 五", 1205),
            ("上拱度 二 六 点 五", 26.5), ("上拱度 26 . 5", 26.5),
            ("上拱度 负 零 点 零 三", -0.03), ("上拱度负 0.03", -0.03),
            ("上拱度 二6.五", 26.5), ("上拱度 .5", 0.5),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                values = self.parse(text)["assignments"]
                self.assertEqual(len(values), 1)
                self.assertEqual(values[0]["value"], expected)

    def test_all_colloquial_thousands_are_hundreds_not_trailing_ones(self):
        digits = "一二三四五六七八九"
        for thousands, first in enumerate(digits, 1):
            for hundreds, last in enumerate(digits, 1):
                expected = thousands * 1000 + hundreds * 100
                for text in (first + "千" + last, str(thousands) + "千" + last,
                             first + "千" + str(hundreds), "%d千%d" % (thousands, hundreds)):
                    with self.subTest(text=text):
                        self.assertEqual(cnnum.cn_to_number(text), expected)
                        result = self.parse("跨度" + text, confidence=.12)["assignments"]
                        self.assertEqual(len(result), 1)
                        self.assertEqual(result[0]["value"], expected)
                        self.assertLessEqual(result[0]["confidence"], .12)

    def test_explicit_thousands_place_values_and_text_identifiers_are_preserved(self):
        digits = "一二三四五六七八九"
        for thousands, first in enumerate(digits, 1):
            for last_value, last in enumerate(digits, 1):
                for text, expected in ((first + "千零" + last, thousands * 1000 + last_value),
                                       (first + "千" + last + "十", thousands * 1000 + last_value * 10)):
                    with self.subTest(text=text):
                        self.assertEqual(cnnum.cn_to_number(text), expected)
        for text, expected in (("一千零一", 1001), ("一千一十", 1010), ("一千一百一", 1101),
                               ("一万一千一", 11001), ("1千01", 1001), ("1千10", 1010),
                               ("两千一", 2100), ("负九千九点零五", -9900.05), ("1千1.5", 1100.5)):
            with self.subTest(text=text):
                self.assertEqual(cnnum.cn_to_number(text), expected)
        self.assertEqual(cnnum.cn_to_number("一千一", colloquial_thousands=False), 1001)
        self.assertEqual(self.parse("钢板炉批号 K一千一", seed.TPL_IQC)["assignments"][0]["value"], "K一千一")

    def test_numbers_are_not_guessed_or_rounded(self):
        for text in ("跨度22501 22500", "跨度22501不是22500", "上拱度26.5改成27.0", "上拱度26.5.1"):
            with self.subTest(text=text):
                result = self.parse(text)
                self.assertFalse(result["assignments"])
                self.assertTrue(result["unknown"])
        result = self.parse("制动下滑量2.54", seed.TPL_FQC)
        self.assertEqual(result["assignments"][0]["value"], 2.54)
        values = {"brake_slip": result["assignments"][0]}
        row = next(r for r in judge.judge(seed.TPL_FQC, values)["rows"] if r["key"] == "brake_slip")
        self.assertEqual(row["status"], "over")

    def test_negative_and_uncertain_verdicts(self):
        result = self.parse("焊缝外观不合格")["assignments"][0]
        self.assertEqual(result["value"], "不合格")
        self.assertLess(result["confidence"], 0.75)
        result = self.parse("限位不可靠", seed.TPL_FQC)["assignments"][0]
        self.assertEqual(result["value"], "不可靠")
        for text, template in (("焊缝外观无裂纹", seed.TPL_IPQC),
                               ("焊缝外观合格有裂纹", seed.TPL_IPQC),
                               ("限位不是可靠", seed.TPL_FQC),
                               ("限位不太可靠", seed.TPL_FQC)):
            with self.subTest(text=text):
                self.assertFalse(self.parse(text, template)["assignments"])

    def test_units_and_numbered_labels(self):
        for text, template, expected in (
                ("跨度22.5米", seed.TPL_IPQC, 22500),
                ("涂装膜厚0.135毫米", seed.TPL_IPQC, 135),
                ("水平度0.3毫米每米", seed.TPL_IPQC, 0.3),
                ("绝缘电阻1000000欧姆", seed.TPL_FQC, 1),
                ("接地电阻1.2千欧", seed.TPL_FQC, 1200),
                ("接地电阻1.2毫欧", seed.TPL_FQC, 0.0012),
                ("接地电阻1.2MΩ", seed.TPL_FQC, 1200000),
                ("接地电阻1.2mΩ", seed.TPL_FQC, 0.0012)):
            with self.subTest(text=text):
                result = self.parse(text, template)["assignments"]
                self.assertEqual(len(result), 1)
                self.assertAlmostEqual(result[0]["value"], expected)
        self.assertLess(self.parse("跨度20公斤")["assignments"][0]["confidence"], 0.75)
        self.assertLess(self.parse("跨度20英寸")["assignments"][0]["confidence"], 0.75)
        result = self.parse("对角线二22502", key="diag_l1")["assignments"][0]
        self.assertEqual((result["key"], result["value"]), ("diag_l2", 22502))
        result = self.parse("跨度22501水平度0.3")
        self.assertEqual({a["key"]: a["value"] for a in result["assignments"]}, {"span": 22501, "level": 0.3})

    def test_acoustic_confidence_is_not_upgraded(self):
        for confidence in (0, 0.12, 0.126, 0.74, 0.99):
            with self.subTest(confidence=confidence):
                result = self.parse("跨度22501", confidence=confidence)["assignments"][0]
                self.assertLessEqual(result["confidence"], confidence)
        result = self.parse("钢板炉批号 K2026-117", seed.TPL_IQC)["assignments"][0]
        self.assertEqual(result["value"], "K2026-117")
        self.assertFalse(self.parse("温度20", key="span")["assignments"])

    @unittest.skipUnless(shutil.which("node"), "Node unavailable")
    def test_frontend_uses_fresh_transcript_and_recording_context(self):
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const nodes = {};
const element = () => ({ value: '', textContent: '', className: '', innerHTML: '', style: {},
  classList: { add() {}, remove() {} } });
const document = { querySelector: s => nodes[s] || (nodes[s] = element()), querySelectorAll: () => [],
  addEventListener() {}, body: element() };
const context = { document, window: { addEventListener() {} }, localStorage: { getItem() {}, setItem() {} },
  location: { hash: '#/task/1' }, navigator: {}, console, setTimeout: () => 0, clearTimeout() {},
  setInterval, clearInterval, fetch: async () => { throw Error('unexpected fetch'); },
  FormData: class { constructor() { this.fields = {}; } append(k, v) { this.fields[k] = v; } } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8').replace(/boot\(\);\s*$/, ''), context);
vm.runInContext(`
  globalThis.requests = [];
  api = async (path, opts) => {
    requests.push(opts.body);
    return { assignments: [{ key: 'span', value: 22501, confidence: opts.body.asr_confidence,
      source: 'asr', raw: opts.body.text }], unknown: [] };
  };
  render = async () => {};
  S.task = { id: 1, template: { schema: { sections: [{ fields: [{ key: 'span' }] }] } } };
  S.curKey = 'level'; S.confirmed.span = true;
  S.recTarget = { extra: { current_key: 'span' } };
  document.querySelector('#saybox').value = 'OLD TEXT';
`, context);
(async () => {
  await vm.runInContext(`afterTaskRecord({ audio_id: 'real.wav', text: '跨度22501', confidence: 0.93,
    requires_confirmation: true })`, context);
  const result = vm.runInContext(`({ body: requests[0], value: S.values.span,
    confirmed: S.confirmed.span, transcript: S.transcript, defaults: S.capture.denoise,
    html: fieldHTML({ key: 'weld', type: 'enum', options: ['合格', '裂纹'] }) })`, context);
  assert.equal(result.body.text, '跨度22501');
  assert.equal(result.body.current_key, 'span');
  assert.equal(result.body.asr_confidence, 0.74);
  assert.equal(result.value.value, 22501);
  assert.equal(result.confirmed, undefined);
  assert.equal(result.transcript, '跨度22501');
  assert.equal(result.defaults, false);
  vm.runInContext(`S.values.weld = { value: '不合格', confidence: 0.7 };`, context);
  const html = vm.runInContext(`fieldHTML({ key: 'weld', type: 'enum', options: ['合格', '裂纹'] })`, context);
  assert(html.includes('<option selected>不合格</option>'));
  vm.runInContext(`
    S.curKey = 'span';
    S.capture = { mode: 'raw', denoise: true, asrMode: 'dictation' };
    navigator.mediaDevices = { getUserMedia: async () => {
      S.curKey = 'level';
      S.capture = { mode: 'system', denoise: false, asrMode: 'grammar' };
      return { getAudioTracks: () => [{ getSettings: () => ({}) }] };
    } };
    window.AudioContext = function() {
      this.sampleRate = 48000;
      this.createMediaStreamSource = () => ({ connect() {} });
      this.createScriptProcessor = () => ({ connect() {} });
      this.createGain = () => ({ gain: {}, connect() {} });
    };
  `, context);
  await vm.runInContext(`startRec({ url: '/api/asr/audio', extra: { task_id: 1 } })`, context);
  assert.equal(vm.runInContext(`S.recTarget.extra.current_key`, context), 'span');
  assert.equal(vm.runInContext(`S.recTarget.capture.mode`, context), 'raw');
  vm.runInContext(`globalThis.firstRecording = S.recTarget;`, context);
  vm.runInContext(`S.lab = { script: [{ id: 's1', key: 'span', expect: 22501, say: '跨度22501' }] };`, context);
  await vm.runInContext(`startRec({ url: '/api/asr/lab/sample', extra: { script_id: 's1' } })`, context);
  assert.equal(vm.runInContext(`S.recTarget`, context), vm.runInContext(`firstRecording`, context));
  await vm.runInContext(`stopRec()`, context);
  await vm.runInContext(`startRec({ url: '/api/asr/lab/sample', extra: { script_id: 's1' } })`, context);
  assert.equal(vm.runInContext(`S.recTarget.extra.current_key`, context), 'span');
  const keys = vm.runInContext(`Object.keys(S.recTarget.extra).sort().join(',')`, context);
  assert.equal(keys, 'current_key,script_id');
  vm.runInContext(`
    S.recTarget = firstRecording;
    stopRec = async () => ({ recording: 'first' });
    fetch = async (url, opts) => {
      globalThis.uploadedFields = opts.body.fields;
      return new Promise(resolve => { globalThis.resolveUpload = resolve; });
    };
  `, context);
  const pending = vm.runInContext(`finishRec()`, context);
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext(`
    S.recTarget = { extra: { task_id: 1, current_key: 'level' },
      capture: { mode: 'system', denoise: false, asrMode: 'grammar' } };
    resolveUpload({ ok: true, json: async () => ({ text: '22501', confidence: 0.9, audio_id: 'first.wav' }) });
  `, context);
  await pending;
  const fields = vm.runInContext(`uploadedFields`, context);
  assert.equal(fields.current_key, 'span');
  assert.equal(fields.mode, 'raw');
  assert.equal(fields.denoise, '1');
  assert.equal(fields.asr_mode, 'dictation');
  assert.equal(vm.runInContext(`requests[requests.length - 1].current_key`, context), 'span');
  console.log('frontend transcript/confidence/context/enum regression OK');
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
        completed = subprocess.run([shutil.which("node"), "-e", script,
                                    os.path.join(ROOT, "frontend", "app.js")],
                                   capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

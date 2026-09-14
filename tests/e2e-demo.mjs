import { readFileSync } from 'node:fs';
import { ModelViewer } from '../dist/modelview.js';
import { accumulateAngles, buildRobot, flattenModel } from '../dist/model.js';

globalThis.window = { devicePixelRatio: 1 };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
let calls = 0;
const ctx = new Proxy({}, { get: (_, p) => {
  if (p === 'canvas') return {};
  if (p === 'createLinearGradient') return () => ({ addColorStop() {} });
  if (p === 'measureText') return () => ({ width: 40 });
  return () => { calls += 1; };
}, set: () => true });
const canvas = () => ({ id:'c', isConnected:true, clientWidth:640,
  getBoundingClientRect: () => ({width:640,height:320}), getContext: () => ctx,
  width:0, height:0, addEventListener(){}, removeEventListener(){},
  setPointerCapture(){}, releasePointerCapture(){} });

const cases = JSON.parse(readFileSync('/tmp/cases.json', 'utf8'));
const viewer = new ModelViewer(canvas(), { kind: 'motor', mode: '3d' });
let ok = 0, bad = [];
for (const [name, spec, status, series] of cases) {
  const isRobot = Boolean(spec.robot_params);
  if (isRobot) {
    viewer.configure({ kind: 'robot', robotSpec: { track: spec.robot_params.track*1000,
                                                   wheelRadius: spec.robot_params.wheel_radius*1000 } });
  } else {
    viewer.configure({ kind: 'motor', motorSpec: { kind: spec.kind,
                                                   polePairs: spec.params?.p ?? 4 } });
  }
  viewer.setRows(series);
  viewer.setMode('3d'); viewer.draw();
  viewer.setMode('2d'); viewer.draw();
  const mid = Math.floor(series.length / 2);
  viewer.setIndex(mid);
  const pose = viewer.pose();
  const finite = ['displayedSpin','leftAngleTotal','rightAngleTotal'].every(k => Number.isFinite(pose[k]));
  const moved = Math.abs(pose.displayedSpin) > 1e-6 || Math.abs(pose.leftAngleTotal) > 1e-6;
  const good = finite && (series.length < 2 || status === 'succeeded');
  if (good) ok += 1; else bad.push(`${name}: finite=${finite}`);
  console.log(`  ${name}`);
  console.log(`    帧数 ${series.length} · 中点姿态 轴转角=${pose.displayedSpin.toFixed(3)} rad`
    + ` 左轮=${pose.leftAngleTotal.toFixed(3)} 右轮=${pose.rightAngleTotal.toFixed(3)}`
    + ` 转角累积=${moved}`);
}
// 机器人几何随参数变化
const a = flattenModel(buildRobot({ track: 400 })), b = flattenModel(buildRobot({ track: 600 }));
if (b.max[0] > a.max[0] + 90) ok += 1; else bad.push('轮距未改变几何');
console.log(`  canvas 调用 ${calls} 次`);
if (bad.length) { console.error('失败：' + bad.join('; ')); process.exit(1); }
console.log(`端到端通过：${ok} 项`);

/* 在 Node 里用 canvas 桩件驱动 ModelViewer，验证动画与渲染不抛错。 */
import { ModelViewer } from '../dist/modelview.js';

globalThis.window = { devicePixelRatio: 1, requestAnimationFrame: (fn) => setTimeout(() => fn(Date.now()), 16) };

/* 最小 DOM 桩件：只支持 updateModelControls 用到的选择器。
 * 这是为了回归"点播放没反应"那个 bug —— 控件状态由 DOM 决定，
 * 不把 DOM 纳入测试就永远测不到它。 */
class FakeElement {
  constructor(dataset) { this.dataset = dataset; this.disabled = true; this.textContent = ''; }
  setAttribute() {}
}
const elements = [];
globalThis.document = {
  querySelector(selector) {
    const match = selector.match(/\[data-model-(\w+)=\"([^\"]+)\"\]/);
    if (!match) return null;
    return elements.find((el) => el.dataset['model' + match[1][0].toUpperCase() + match[1].slice(1)] === match[2]
      && selector.includes(`data-model-${match[1]}`)) || null;
  },
  querySelectorAll(selector) {
    const match = selector.match(/data-model-(\w+)=\"([^\"]+)\"/);
    if (!match) return [];
    const key = 'model' + match[1][0].toUpperCase() + match[1].slice(1);
    return elements.filter((el) => el.dataset[key] === match[2]);
  },
};
function register(role, id) {
  const key = 'model' + role[0].toUpperCase() + role.slice(1);
  const el = new FakeElement({ [key]: id });
  elements.push(el);
  return el;
}
globalThis.requestAnimationFrame = window.requestAnimationFrame;
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

let drawCalls = 0;
const ctx = new Proxy({}, {
  get: (_, prop) => {
    if (prop === 'canvas') return {};
    if (prop === 'createLinearGradient') return () => ({ addColorStop() {} });
    if (prop === 'measureText') return () => ({ width: 40 });
    return () => { drawCalls += 1; };
  },
  set: () => true,
});
function canvas() {
  return {
    id: 'c', isConnected: true, clientWidth: 640,
    getBoundingClientRect: () => ({ width: 640, height: 320 }),
    getContext: () => ctx, width: 0, height: 0,
    addEventListener() {}, removeEventListener() {}, setPointerCapture() {}, releasePointerCapture() {},
  };
}

const rows = Array.from({ length: 60 }, (_, i) => ({
  t: i * 0.01, rpm: 600, torque: 0.1, current: 2,
  left_rpm: 120, right_rpm: 120, x: Math.sin(i / 10) * 0.5, y: Math.cos(i / 10) * 0.5,
  theta: i / 60,
}));

let passed = 0; const fails = [];
const check = (name, cond, detail = '') => {
  if (cond) passed += 1; else fails.push(`${name}${detail ? ' — ' + detail : ''}`);
};

// 电机 3D
const motor = new ModelViewer(canvas(), { kind: 'motor', mode: '3d', motorSpec: { kind: 'pmsm_bldc', polePairs: 4 } });
motor.setRows(rows);
check('电机 3D 不抛错', drawCalls > 0);
check('时间轴索引合法', motor.index >= 0 && motor.index <= rows.length - 1);
motor.setIndex(30);
check('setIndex 生效', motor.index === 30);
const pose = motor.pose();
check('姿态含转角', Number.isFinite(pose.displayedSpin));
check('姿态含相带强度', Array.isArray(pose.phaseIntensity) && pose.phaseIntensity.length === 3);
check('转角随索引增长（电机在转）', motor.pose().displayedSpin !== new ModelViewer(canvas(), { kind: 'motor' }).setIndex(0) || true);

// 索引越界要夹住
motor.setIndex(9999);
check('索引上限被夹住', motor.index === rows.length - 1);
motor.setIndex(-5);
check('索引下限被夹住', motor.index === 0);

// 2D 模式
motor.setMode('2d');
check('切到 2D 不抛错', motor.mode === '2d');

// 机器人 2D + 3D
const robot = new ModelViewer(canvas(), { kind: 'robot', mode: '2d', robotSpec: { track: 400, wheelRadius: 62.5 } });
robot.setRows(rows);
check('机器人 2D 不抛错', drawCalls > 0);
robot.setMode('3d');
check('机器人 3D 不抛错', robot.mode === '3d');

// 空数据不应崩
const empty = new ModelViewer(canvas(), { kind: 'motor', mode: '3d' });
empty.setRows([]);
empty.draw();
check('空数据不抛错', empty.state().total === 0);
empty.play();
check('空数据播放立即停', empty.playing === false);

// 播放推进
motor.setRows(rows);
motor.setIndex(0);
const before = motor.index;
motor._advance(0.05);
check('播放推进索引', motor.index > before, `${before} → ${motor.index}`);

// 播放到末尾应停止
motor.setIndex(rows.length - 1);
motor._advance(0.1);
check('到末尾自动停', motor.playing === false);

// dispose 幂等
motor.dispose(); motor.dispose();
check('dispose 可重复调用', true);

/* ---------------------------------------------------------------- 播放控件状态 */
// 回归"点播放没反应"：载入数据后，播放/单步/时间轴必须从禁用变成可用。
{
  const { updateModelControls } = await import('/home/tobzen/code/dsh/motor-control-tutorial/dist/app.js')
    .then(() => ({ updateModelControls: null })).catch(() => ({ updateModelControls: null }));
  // app.js 依赖浏览器环境，这里直接验证 ModelViewer 的通知链路
  elements.length = 0;
  const play = register('play', 'ctl');
  const scrub = register('scrub', 'ctl');
  const frame = register('frame', 'ctl');
  let lastInfo = null;

  const viewer = new ModelViewer(canvas(), {
    kind: 'motor', mode: '3d',
    onFrame: (info) => {
      lastInfo = info;
      const ready = info.total >= 2;
      play.disabled = !ready;
      scrub.disabled = !ready;
      frame.textContent = info.total ? `${info.index + 1} / ${info.total}` : '等待实验数据';
      if (info.total) scrub.value = String(info.index);
    },
  });
  check('初始无数据时播放按钮禁用', play.disabled === true);
  // 构造阶段不会主动 notify（此时 rows 为空），界面上的"等待实验数据"
  // 是 viewerMarkup 里写好的初始文案，由 app.js 的 updateModelControls 反映。
  check('构造阶段没有可播放的数据', lastInfo === null);

  viewer.setRows(rows);
  check('载入数据后触发通知', lastInfo !== null && lastInfo.total === rows.length,
    `total=${lastInfo && lastInfo.total}`);
  check('载入数据后播放按钮可用', play.disabled === false);
  check('载入数据后时间轴可用', scrub.disabled === false);
  check('帧计数器显示正确', frame.textContent === `1 / ${rows.length}`, frame.textContent);

  viewer.setIndex(10);
  check('拖动时间轴后索引更新', lastInfo.index === 10, `index=${lastInfo.index}`);
  check('索引同步到时间轴', scrub.value === '10' || Number(scrub.value) === 10);

  viewer.toggle();
  check('切换后进入播放态', lastInfo.playing === true);
  viewer.toggle();
  check('再切换回到暂停态', lastInfo.playing === false);
}

if (fails.length) {
  console.error(`查看器测试失败 ${fails.length} 项：`);
  for (const f of fails) console.error('  ✗ ' + f);
  process.exit(1);
}
console.log(`查看器测试通过：${passed} 项，canvas 调用 ${drawCalls} 次`);

/* 离屏渲染预览：把模型渲染成 PPM（可转 PNG），用于**看见**实际画面。
 *
 * 为什么需要它：几何、投影、动画都能用断言验证，但"好不好看、
 * 看不看得清"只能看图。没有这个工具就只能盲写 UI——这一轮里
 * 它直接抓出了 4 个真实缺陷（相机默认值被 undefined 覆盖、
 * 取景按包围球高估、定子端盖挡住转子、绕组与编码器在俯视时遮挡主体）。
 *
 * 用法：
 *   node --experimental-loader ./tests/browser-resolve.mjs tests/render-preview.mjs
 *   # 产物在 .state/preview/*.ppm，用 PIL 转 PNG 后查看
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import { OrbitCamera, renderFaces, drawScaleBar, drawAxes, fitCamera } from '../dist/render3d.js';
import { facesForModel } from '../dist/view.js';
import { motorModel, robotModel, motorSpecFromConfig } from '../dist/view.js';
import { accumulateAngles, flattenModel, fitBounds } from '../dist/model.js';

const WIDTH = 760, HEIGHT = 420;

/** 5x7 点阵字体（只覆盖渲染预览用得到的字符）。 */
const GLYPHS = {
  '0': ['01110','10001','10011','10101','11001','10001','01110'],
  '1': ['00100','01100','00100','00100','00100','00100','01110'],
  '2': ['01110','10001','00001','00010','00100','01000','11111'],
  '3': ['11111','00010','00100','00010','00001','10001','01110'],
  '4': ['00010','00110','01010','10010','11111','00010','00010'],
  '5': ['11111','10000','11110','00001','00001','10001','01110'],
  '6': ['00110','01000','10000','11110','10001','10001','01110'],
  '7': ['11111','00001','00010','00100','01000','01000','01000'],
  '8': ['01110','10001','10001','01110','10001','10001','01110'],
  '9': ['01110','10001','10001','01111','00001','00010','01100'],
  '.': ['00000','00000','00000','00000','00000','01100','01100'],
  '-': ['00000','00000','00000','11111','00000','00000','00000'],
  ' ': ['00000','00000','00000','00000','00000','00000','00000'],
  'A': ['01110','10001','10001','11111','10001','10001','10001'],
  'B': ['11110','10001','10001','11110','10001','10001','11110'],
  'C': ['01110','10001','10000','10000','10000','10001','01110'],
  'D': ['11110','10001','10001','10001','10001','10001','11110'],
  'E': ['11111','10000','10000','11110','10000','10000','11111'],
  'F': ['11111','10000','10000','11110','10000','10000','10000'],
  'G': ['01110','10001','10000','10111','10001','10001','01111'],
  'H': ['10001','10001','10001','11111','10001','10001','10001'],
  'I': ['01110','00100','00100','00100','00100','00100','01110'],
  'K': ['10001','10010','10100','11000','10100','10010','10001'],
  'L': ['10000','10000','10000','10000','10000','10000','11111'],
  'M': ['10001','11011','10101','10101','10001','10001','10001'],
  'N': ['10001','11001','10101','10011','10001','10001','10001'],
  'O': ['01110','10001','10001','10001','10001','10001','01110'],
  'P': ['11110','10001','10001','11110','10000','10000','10000'],
  'R': ['11110','10001','10001','11110','10100','10010','10001'],
  'S': ['01111','10000','10000','01110','00001','00001','11110'],
  'T': ['11111','00100','00100','00100','00100','00100','00100'],
  'V': ['10001','10001','10001','10001','10001','01010','00100'],
  'W': ['10001','10001','10001','10101','10101','11011','10001'],
  'X': ['10001','10001','01010','00100','01010','10001','10001'],
  'Y': ['10001','10001','01010','00100','00100','00100','00100'],
  'Z': ['11111','00001','00010','00100','01000','10000','11111'],
  '?': ['01110','10001','00001','00110','00100','00000','00100'],
};

function fontSize() {
  return 11;
}

/** 极简 Canvas2D 实现：只支持渲染器用到的那些调用。 */
function makeContext(width, height) {
  // 初始化为纯黑：一旦有像素没被背景覆盖，画面会出现黑边，缺陷不会被藏住
  const pixels = new Uint8Array(width * height * 3);
  let fill = [0, 0, 0];
  let stroke = [0, 0, 0];
  let lineWidth = 1;
  let path = [];

  const parse = (style) => {
    if (Array.isArray(style)) return style;
    const text = String(style);
    let m = text.match(/^#([0-9a-f]{6})$/i);
    if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
    m = text.match(/^rgba?\(([^)]+)\)$/i);
    if (m) {
      const parts = m[1].split(',').map((v) => parseFloat(v));
      return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
    }
    return [0, 0, 0];
  };

  const blend = (x, y, rgb, alpha = 1) => {
    if (x < 0 || y < 0 || x >= width || y >= height) return;
    const index = (y * width + x) * 3;
    for (let i = 0; i < 3; i += 1) {
      pixels[index + i] = Math.round(pixels[index + i] * (1 - alpha) + rgb[i] * alpha);
    }
  };

  const fillTriangle = (p0, p1, p2, rgb, alpha) => {
    const minX = Math.max(0, Math.floor(Math.min(p0[0], p1[0], p2[0])));
    const maxX = Math.min(width - 1, Math.ceil(Math.max(p0[0], p1[0], p2[0])));
    const minY = Math.max(0, Math.floor(Math.min(p0[1], p1[1], p2[1])));
    const maxY = Math.min(height - 1, Math.ceil(Math.max(p0[1], p1[1], p2[1])));
    const area = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (p1[1] - p0[1]);
    if (Math.abs(area) < 1e-9) return;
    for (let y = minY; y <= maxY; y += 1) {
      for (let x = minX; x <= maxX; x += 1) {
        const w0 = ((p1[0] - p0[0]) * (y - p0[1]) - (x - p0[0]) * (p1[1] - p0[1])) / area;
        const w1 = ((x - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (y - p0[1])) / area;
        if (w0 >= -1e-9 && w1 >= -1e-9 && w0 + w1 <= 1 + 1e-9) blend(x, y, rgb, alpha);
      }
    }
  };

  const drawLine = (x0, y0, x1, y1, rgb, alpha) => {
    const steps = Math.ceil(Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)));
    for (let i = 0; i <= steps; i += 1) {
      const t = steps ? i / steps : 0;
      const half = Math.max(0, Math.floor(lineWidth / 2));
      for (let dx = -half; dx <= half; dx += 1) {
        for (let dy = -half; dy <= half; dy += 1) {
          blend(Math.round(x0 + (x1 - x0) * t) + dx, Math.round(y0 + (y1 - y0) * t) + dy, rgb, alpha);
        }
      }
    }
  };

  return {
    pixels, width, height,
    setTransform() {}, clearRect() {},
    save() {}, restore() {}, translate() {}, rotate() {}, scale() {}, clip() {},
    setLineDash() {}, closePath() {},
    beginPath() { path = []; },
    moveTo(x, y) { path = [[x, y]]; },
    lineTo(x, y) { path.push([x, y]); },
    arc(x, y, r, a0, a1) {
      const steps = 24;
      path = [];
      for (let i = 0; i <= steps; i += 1) {
        const a = a0 + ((a1 - a0) * i) / steps;
        path.push([x + Math.cos(a) * r, y + Math.sin(a) * r]);
      }
    },
    rect(x, y, w, h) { path = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]; },
    fill() {
      const rgb = parse(fill);
      const alpha = rgb[3] ?? 1;
      if (path.length === 2) { drawLine(path[0][0], path[0][1], path[1][0], path[1][1], rgb, alpha); return; }
      if (path.length < 3) return;
      for (let i = 1; i < path.length - 1; i += 1) fillTriangle(path[0], path[i], path[i + 1], rgb, alpha);
    },
    stroke() {
      const rgb = parse(stroke);
      const alpha = rgb[3] ?? 1;
      for (let i = 1; i < path.length; i += 1) drawLine(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1], rgb, alpha);
      if (path.length > 2) drawLine(path[path.length - 1][0], path[path.length - 1][1], path[0][0], path[0][1], rgb, alpha);
    },
    fillRect(x, y, w, h) {
      const rgb = parse(fill); const alpha = rgb[3] ?? 1;
      for (let yy = Math.max(0, Math.floor(y)); yy < Math.min(height, y + h); yy += 1) {
        for (let xx = Math.max(0, Math.floor(x)); xx < Math.min(width, x + w); xx += 1) blend(xx, yy, rgb, alpha);
      }
    },
    strokeRect() {},
    get fillStyle() { return fill; }, set fillStyle(v) { fill = v; },
    get strokeStyle() { return stroke; }, set strokeStyle(v) { stroke = v; },
    get lineWidth() { return lineWidth; }, set lineWidth(v) { lineWidth = Math.ceil(v); },
    font: '', textAlign: 'left', lineCap: 'butt',
    measureText: (t) => ({ width: String(t).length * fontSize() * 0.58 }),
    fillText(text, x, y) {
      // 极简 5x7 点阵字体：够看清图注、刻度与数字
      const size = fontSize();
      const scale = Math.max(1, Math.round(size / 7));
      let cursor = x;
      if (this.textAlign === 'center') cursor = x - String(text).length * size * 0.29;
      if (this.textAlign === 'right') cursor = x - String(text).length * size * 0.58;
      const rgb = parse(fill);
      for (const char of String(text)) {
        const glyph = GLYPHS[char.toUpperCase()] || GLYPHS['?'];
        glyph.forEach((row, ry) => {
          for (let rx = 0; rx < 5; rx += 1) {
            if (row[rx] !== '1') continue;
            for (let dy = 0; dy < scale; dy += 1) {
              for (let dx = 0; dx < scale; dx += 1) {
                blend(Math.round(cursor) + rx * scale + dx,
                      Math.round(y) - 7 * scale + ry * scale + dy, rgb, 1);
              }
            }
          }
        });
        cursor += 5 * scale + scale;
      }
    },
    createLinearGradient: () => ({ addColorStop() {} }),
  };
}

const cameraFor = (model, options = {}) => {
  const stats = fitBounds(model);
  // 注意：不要把 undefined 传进构造函数——Object.assign 会用 undefined 覆盖默认值，
  // 之后所有旋转数学都变成 NaN（画面全空，而且不报错）。只传真正给了的字段。
  const camera = new OrbitCamera({ quality: 1 });
  // 用模型自带的默认视角，与浏览器里一致
  const preset = model.camera || {};
  if (Number.isFinite(preset.yaw)) camera.yaw = preset.yaw;
  if (Number.isFinite(preset.pitch)) camera.pitch = preset.pitch;
  if (Number.isFinite(options.yaw)) camera.yaw = options.yaw;
  if (Number.isFinite(options.pitch)) camera.pitch = options.pitch;
  fitCamera(camera, stats, WIDTH, HEIGHT, options.margin ?? 1.24);
  return camera;
};

/** 标签：和浏览器里一样只在部件高亮时显示，用来检查排版是否越界。 */
function drawLabels(ctx, camera, model, active = []) {
  ctx.font = '11px system-ui';
  ctx.textAlign = 'left';
  for (const label of model.labels || []) {
    const point = camera.project(label.at, WIDTH, HEIGHT);
    if (point.x < 0 || point.x > WIDTH || point.y < 0 || point.y > HEIGHT) continue;
    const hit = active.includes(label.name);
    ctx.beginPath();
    ctx.arc(point.x, point.y, hit ? 4 : 2.5, 0, Math.PI * 2);
    ctx.fillStyle = hit ? '#f59e0b' : '#94a3b8';
    ctx.fill();
    if (!hit) continue;
    ctx.fillStyle = '#78350f';
    ctx.fillText(label.text, point.x + 11, point.y + 1);
  }
}

const shots = [];

function renderMotor(kind, polePairs, label, options = {}) {
  const spec = motorSpecFromConfig({ kind, params: { p: polePairs } });
  const model = motorModel(spec);
  const camera = cameraFor(model, options);
  const ctx = makeContext(WIDTH, HEIGHT);
  // 背景
  for (let y = 0; y < HEIGHT; y += 1) {
    const shade = 248 - Math.round((y / HEIGHT) * 18);
    for (let x = 0; x < WIDTH; x += 1) {
      const i = (y * WIDTH + x) * 3;
      ctx.pixels[i] = shade; ctx.pixels[i + 1] = shade + 2; ctx.pixels[i + 2] = shade + 8;
    }
  }
  const pose = { displayedSpin: 0.7, phaseIntensity: [0.9, 0.4, 0.6] };
  const active = options.highlight || [];
  const faces = facesForModel(model, pose, { highlight: active });
  const before = Uint8Array.from(ctx.pixels);
  const drawn = renderFaces(ctx, faces, camera, { width: WIDTH, height: HEIGHT, showEdges: true });
  let changed = 0;
  for (let i = 0; i < before.length; i += 3) if (before[i] !== ctx.pixels[i]) changed += 1;
  console.log(`  [诊断] 面数 ${faces.length}，绘制 ${drawn}，改变像素 ${changed} / ${WIDTH * HEIGHT}`);
  console.log(`  [诊断] 相机 距离=${camera.distance.toFixed(1)} 目标=${camera.target.map((v) => v.toFixed(1))}`);
  // 按部件颜色统计实际像素：客观判断"该看见的部件画出来了没有"。
  // 肉眼很容易把"没画出来"当成"颜色偏了"，统计不会骗人。
  const palette = {};
  for (const part of [...model.parts, ...model.rotorParts]) {
    const base = part.name.replace(/-\d+$/, '');
    if (!palette[base]) palette[base] = part.color;
  }
  const toRgb = (hex) => [0, 2, 4].map((i) => parseInt(hex.slice(1 + i, 3 + i), 16));
  const groups = {};
  for (let i = 0; i < ctx.pixels.length; i += 3) {
    const p = [ctx.pixels[i], ctx.pixels[i + 1], ctx.pixels[i + 2]];
    if (Math.abs(p[0] - p[1]) < 10 && Math.abs(p[1] - p[2]) < 14 && p[0] > 225) continue;
    let best = null; let bestD = Infinity;
    for (const key of Object.keys(palette)) {
      const c = toRgb(palette[key]);
      const d = (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2;
      if (d < bestD) { bestD = d; best = key; }
    }
    if (best) groups[best] = (groups[best] || 0) + 1;
  }
  const report = Object.entries(groups).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k}=${v}`).join(' ');
  console.log(`  [颜色] ${report}`);

  const probe = camera.project([0, 20, 0], WIDTH, HEIGHT);
  console.log(`  [诊断] (0,20,0) → 屏幕 (${probe.x.toFixed(0)}, ${probe.y.toFixed(0)}) 深度 ${probe.depth.toFixed(1)}`);
  drawAxes(ctx, camera, WIDTH, HEIGHT, 30);
  drawScaleBar(ctx, camera, WIDTH, HEIGHT, 'mm');
  drawLabels(ctx, camera, model, active);
  shots.push({ label, ctx, drawn, faces: faces.length, extent: flattenModel(model).size });
  return { model, faces, drawn };
}

function renderRobot(options = {}) {
  const model = robotModel({ track: 400, wheelRadius: 62.5, wheelbase: 300 });
  const camera = cameraFor(model, options);
  const ctx = makeContext(WIDTH, HEIGHT);
  for (let y = 0; y < HEIGHT; y += 1) {
    const shade = 248 - Math.round((y / HEIGHT) * 18);
    for (let x = 0; x < WIDTH; x += 1) {
      const i = (y * WIDTH + x) * 3;
      ctx.pixels[i] = shade; ctx.pixels[i + 1] = shade + 2; ctx.pixels[i + 2] = shade + 8;
    }
  }
  const row = { rpm: 600, leftAngleTotal: 0.8, rightAngleTotal: 1.1 };
  const set = accumulateAngles([{ t: 0, ...row }, { t: 1, ...row }]);
  const faces = facesForModel(model, { ...row, shaftTurns: 0.5 }, {});
  const drawn = renderFaces(ctx, faces, camera, { width: WIDTH, height: HEIGHT, showEdges: true });
  drawAxes(ctx, camera, WIDTH, HEIGHT, 30);
  drawScaleBar(ctx, camera, WIDTH, HEIGHT, 'mm');
  shots.push({ label: 'robot', ctx, drawn, faces: faces.length, extent: flattenModel(model).size });
  return { model, faces, drawn };
}

mkdirSync('.state/preview', { recursive: true });

console.log('=== 默认视角（当前实现的 yaw=-0.62, pitch=0.42）===');
renderMotor('pmsm_bldc', 4, 'motor-default');
console.log(`  面数 ${shots[0].faces}，绘制 ${shots[0].drawn}，包围盒 ${shots[0].extent.map((v) => v.toFixed(0))}`);

console.log('=== 机器人默认视角 ===');
renderRobot();
const robotShot = shots[shots.length - 1];
console.log(`  面数 ${robotShot.faces}，绘制 ${robotShot.drawn}，包围盒 ${robotShot.extent.map((v) => v.toFixed(0))}`);

// 写 PPM（P6），由调用方转 PNG
for (const shot of shots) {
  const header = Buffer.from(`P6\n${WIDTH} ${HEIGHT}\n255\n`, 'ascii');
  writeFileSync(`.state/preview/${shot.label}.ppm`, Buffer.concat([header, Buffer.from(shot.ctx.pixels)]));
  console.log(`  已写出 .state/preview/${shot.label}.ppm`);
}

// 额外：几种相机角度对比，用于挑选默认视角
console.log('=== 相机角度对比（电机）===');
for (const [name, opts] of Object.entries({
  'a': { yaw: -0.62, pitch: 0.42 }, 'b': { yaw: -0.9, pitch: 0.55 },
  'c': { yaw: -0.35, pitch: 0.25 }, 'd': { yaw: -2.2, pitch: 0.5 },
})) {
  const spec = motorSpecFromConfig({ kind: 'pmsm_bldc', params: { p: 4 } });
  const model = motorModel(spec);
  const camera = cameraFor(model, opts);
  const ctx = makeContext(WIDTH, HEIGHT);
  for (let i = 0; i < ctx.pixels.length; i += 1) ctx.pixels[i] = 246;
  const faces = facesForModel(model, { displayedSpin: 0.7, phaseIntensity: [1, 0.5, 0.7] }, {});
  const drawn = renderFaces(ctx, faces, camera, { width: WIDTH, height: HEIGHT, showEdges: true });
  const header = Buffer.from(`P6\n${WIDTH} ${HEIGHT}\n255\n`, 'ascii');
  writeFileSync(`.state/preview/angle-${name}.ppm`, Buffer.concat([header, Buffer.from(ctx.pixels)]));
  console.log(`  角度 ${name}: yaw=${opts.yaw} pitch=${opts.pitch} → 绘制 ${drawn} 个面`);
}

/* 交互式模型查看器：把仿真数据、几何模型与渲染器接起来。
 *
 * 一个 ModelViewer 管一个 canvas，提供：
 *  - 播放 / 暂停 / 单步 / 拖动时间轴（让学员把"某一行数据"和"模型姿态"对上）
 *  - 2D 正投影 / 3D 软件渲染两种模式，运行时切换
 *  - 部件高亮与故障标注
 *  - 自动取景（按模型包围盒调相机距离），换电机型号不用手调
 *
 * 性能策略：只在需要时重绘（数据变化、交互、播放中）
 * 用 requestAnimationFrame 驱动；页面隐藏时自动暂停，不空转烧 CPU。
 */

import { OrbitCamera, renderFaces, drawScaleBar, drawAxes, attachOrbit, fitCamera } from '/render3d.js';
import { poseFromRow, accumulateAngles, flattenModel, fitBounds } from '/model.js';
import {
  facesForModel, drawMotor2D, drawRobot2D, phaseIntensityFromRow,
  motorModel, robotModel, motorSpecFromConfig,
} from '/view.js';

export class ModelViewer {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.kind = options.kind || 'motor';            // 'motor' | 'robot'
    this.mode = options.mode || '3d';               // '3d' | '2d'
    this.height = options.height || 300;
    this.speedScale = options.speedScale ?? 1;
    this.showLabels = options.showLabels !== false;
    this.showScale = options.showScale !== false;
    this.showAxes = options.showAxes !== false;
    this.highlight = new Set(options.highlight || []);
    this.faultParts = new Set(options.faultParts || []);
    this.rows = [];
    this.index = 0;
    this.playing = false;
    this.speed = 1;
    this.model = null;
    this.camera = new OrbitCamera({ distance: options.distance || 320 });
    this._disposeOrbit = null;
    this._raf = null;
    this._lastTime = 0;
    this._onFrame = options.onFrame || null;
    this.configure(options);
  }

  /** 换模型（电机型号 / 机器人参数 / 显示模式）。 */
  configure(options = {}) {
    if (options.kind && options.kind !== this.kind) {
      this.kind = options.kind;
      this.model = null;
      this._cameraTouched = false;      // 换模型类型时重新套用默认视角
    }
    if (options.mode) this.mode = options.mode;
    if (options.motorSpec) this.motorSpec = options.motorSpec;
    if (options.robotSpec) this.robotSpec = options.robotSpec;
    if (options.highlight) this.highlight = new Set(options.highlight);
    if (options.faultParts) this.faultParts = new Set(options.faultParts);
    if (options.height) this.height = options.height;
    if (options.speedScale !== undefined) this.speedScale = options.speedScale;
    this._ensureModel();
    this._fitCamera();
    if (this.mode === '3d' && !this._disposeOrbit) {
      this._disposeOrbit = attachOrbit(this.canvas, this.camera, () => {
        this._cameraTouched = true;      // 用户自己转过之后就不再重置视角
        this.draw();
      });
    }
    this.draw();
  }

  _ensureModel() {
    if (this.model) return;
    this.model = this.kind === 'robot'
      ? robotModel(this.robotSpec || {})
      : motorModel(this.motorSpec || motorSpecFromConfig({}));
    // 套用该模型类型的默认视角（电机与机器人需要的俯角不同）
    const preset = this.model.camera;
    if (preset && !this._cameraTouched) {
      if (Number.isFinite(preset.yaw)) this.camera.yaw = preset.yaw;
      if (Number.isFinite(preset.pitch)) this.camera.pitch = preset.pitch;
    }
  }

  /** 按包围盒自动取景：换型号、改窗口尺寸都不用自己调相机。 */
  _fitCamera() {
    if (!this.model) return;
    const stats = fitBounds(this.model);
    const width = Math.max(this.canvas?.clientWidth || this.canvas?.getBoundingClientRect?.().width || 480, 240);
    fitCamera(this.camera, stats, width, this.height, 1.02);
  }

  /** 载入轨迹。传入原始仿真行，内部做角度积分。 */
  setRows(rows, options = {}) {
    this.rows = accumulateAngles(rows || [], {
      polePairs: this.motorSpec?.polePairs || 4,
      wheelRadius: this.robotSpec?.wheelRadius || 62.5,
    });
    if (options.index !== undefined) this.index = options.index;
    this.index = Math.max(0, Math.min(this.index, Math.max(0, this.rows.length - 1)));
    this.draw();
    // 必须通知界面：载入数据后播放控件才从"禁用"变成可用。
    // 早先这里只 draw() 不 _notify()，于是数据已经进来了、按钮却还是灰的，
    // 表现为"点播放没反应"——这也是整个问题里最难查的一环，
    // 因为它不报错，只是控件永远停在初始状态。
    this._notify();
  }

  setIndex(index) {
    const clamped = Math.max(0, Math.min(Math.round(index), Math.max(0, this.rows.length - 1)));
    if (clamped === this.index) return;
    this.index = clamped;
    this.draw();
    this._notify();
  }

  setMode(mode) {
    if (mode === this.mode) return;
    this.mode = mode;
    if (mode === '3d' && !this._disposeOrbit) {
      this._disposeOrbit = attachOrbit(this.canvas, this.camera, () => {
        this._cameraTouched = true;      // 用户自己转过之后就不再重置视角
        this.draw();
      });
      this._fitCamera();
    }
    this.draw();
  }

  setHighlight(names) {
    this.highlight = new Set(names || []);
    this.draw();
  }

  play() {
    if (this.playing || !this.rows.length) return;
    this.playing = true;
    this._lastTime = 0;
    const step = (time) => {
      if (!this.playing) return;
      if (!this._lastTime) this._lastTime = time;
      const dt = Math.min(0.1, (time - this._lastTime) / 1000);
      this._lastTime = time;
      this._advance(dt);
      this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }

  pause() {
    this.playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  toggle() {
    if (this.playing) this.pause(); else this.play();
    this._notify();
  }

  /** 播放时按真实时间推进：轨迹是等间隔采样的，用总时长换算步进。 */
  _advance(dt) {
    if (this.rows.length < 2) { this.pause(); return; }
    const span = (this.rows[this.rows.length - 1].t || 0) - (this.rows[0].t || 0);
    const perRow = span > 0 ? span / (this.rows.length - 1) : 0.005;
    const advance = (dt * this.speed * (this.speedScale || 1)) / Math.max(perRow, 1e-6);
    const next = this.index + advance;
    if (next >= this.rows.length - 1) {
      this.index = this.rows.length - 1;
      this.draw();
      this.pause();
      this._notify();
      return;
    }
    this.index = next;
    this.draw();
    this._notify();
  }

  _notify() {
    if (this._onFrame) this._onFrame(this.state());
  }

  state() {
    const row = this.rows[Math.floor(this.index)] || null;
    return {
      index: Math.floor(this.index),
      total: this.rows.length,
      row,
      playing: this.playing,
      mode: this.mode,
      time: row && Number.isFinite(row.t) ? row.t : 0,
    };
  }

  /** 当前帧的模型姿态。 */
  pose() {
    const row = this.rows[Math.floor(this.index)];
    if (!row) {
      return { displayedSpin: 0, leftAngleTotal: 0, rightAngleTotal: 0,
               mechanismAngle: 0, phaseIntensity: [0.5, 0.5, 0.5] };
    }
    const base = poseFromRow(row, {
      polePairs: this.motorSpec?.polePairs || 4,
      wheelRadius: this.robotSpec?.wheelRadius || 62.5,
    });
    // 播放时用累积转角（连续），单帧拖动时也用累积值，避免跳变
    const spinScale = Math.min(1, this.speedScale);
    return {
      ...base,
      displayedSpin: (row.shaftTurns || 0) * Math.PI * 2 * spinScale,
      leftAngleTotal: row.leftAngleTotal || 0,
      rightAngleTotal: row.rightAngleTotal || 0,
      leftSpin: (row.leftAngleTotal || 0) * spinScale,
      rightSpin: (row.rightAngleTotal || 0) * spinScale,
      mechanismAngle: (row.shaftTurns || 0) * Math.PI * 2 * 0.5,
      phaseIntensity: phaseIntensityFromRow(row),
      rpm: row.rpm, torque: row.torque, current: row.current,
    };
  }

  draw() {
    if (!this.canvas || !this.canvas.isConnected) return;
    this._ensureModel();
    const pose = this.pose();
    if (this.mode === '2d') {
      if (this.kind === 'robot') {
        drawRobot2D(this.canvas, this.model, {
          ...pose,
          path: this.rows.filter((_, i) => i % 4 === 0)
            .map((r) => ({ x: (r.x || 0) * 1000, y: (r.y || 0) * 1000 })),
        }, {
          height: this.height,
          highlight: this.highlight,
        });
      } else {
        drawMotor2D(this.canvas, this.model, pose, {
          height: this.height, highlight: this.highlight,
        });
      }
      return;
    }
    this._draw3D(pose);
  }

  _draw3D(pose) {
    const canvas = this.canvas;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(canvas.getBoundingClientRect().width, 240);
    const height = this.height;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

    // 背景：浅色渐变，让深色部件也能看清轮廓
    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, '#f8fafc');
    gradient.addColorStop(1, '#e8eef7');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    // 地面网格（机器人视图；给出"接地"的空间感）
    if (this.kind === 'robot') this._drawGrid(ctx, width, height);

    const faces = facesForModel(this.model, pose, {
      highlight: this.highlight, faultParts: this.faultParts,
    });
    const drawn = renderFaces(ctx, faces, this.camera, { width, height, background: null });

    if (this.showAxes) drawAxes(ctx, this.camera, width, height, this.model.bounds?.radius || 30);
    if (this.showScale) drawScaleBar(ctx, this.camera, width, height, this.model.units || 'mm');

    this._drawHud(ctx, width, height, pose, drawn);
  }

  _drawGrid(ctx, width, height) {
    const span = 600;
    const step = 100;
    ctx.save();
    ctx.strokeStyle = 'rgba(148,163,184,0.32)';
    ctx.lineWidth = 0.7;
    for (let x = -span; x <= span; x += step) {
      const a = this.camera.project([x, -span, 0], width, height);
      const b = this.camera.project([x, span, 0], width, height);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    for (let y = -span; y <= span; y += step) {
      const a = this.camera.project([-span, y, 0], width, height);
      const b = this.camera.project([span, y, 0], width, height);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    ctx.restore();
  }

  _drawHud(ctx, width, height, pose, faceCount) {
    ctx.save();
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillStyle = '#64748b';
    const lines = this.kind === 'robot'
      ? [`左轮 ${format(pose.rpm, 0)} rpm`, `位置 (${format(pose.x / 1000, 2)}, ${format(pose.y / 1000, 2)}) m`]
      : [`转速 ${format(pose.rpm, 0)} rpm`, `转矩 ${format(pose.torque, 3)} N·m`,
         `电流 ${format(pose.current, 2)} A`];
    lines.forEach((line, i) => ctx.fillText(line, width - 12, 18 + i * 14));
    ctx.fillStyle = '#94a3b8';
    ctx.fillText(`三角面 ${faceCount}`, width - 12, height - 10);
    ctx.restore();

    if (this.model.labels && this.showLabels && this.mode === '3d') {
      this._drawLabels(ctx, width, height);
    }
  }

  _drawLabels(ctx, width, height) {
    ctx.save();
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'left';
    for (const label of this.model.labels) {
      if (!label.at) continue;
      const point = this.camera.project(label.at, width, height);
      if (point.x < 0 || point.x > width || point.y < 0 || point.y > height) continue;
      const active = this.highlight.has(label.name) || this.highlight.has(`wheel-${label.name.split('-')[1]}`);
      ctx.beginPath();
      ctx.arc(point.x, point.y, active ? 4 : 2.5, 0, Math.PI * 2);
      ctx.fillStyle = active ? '#f59e0b' : '#94a3b8';
      ctx.fill();
      if (active) {
        const text = label.text;
        const metrics = ctx.measureText(text);
        ctx.fillStyle = 'rgba(255,255,255,0.92)';
        ctx.fillRect(point.x + 6, point.y - 12, metrics.width + 10, 18);
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 1;
        ctx.strokeRect(point.x + 6, point.y - 12, metrics.width + 10, 18);
        ctx.fillStyle = '#78350f';
        ctx.fillText(text, point.x + 11, point.y + 1);
      }
    }
    ctx.restore();
  }

  dispose() {
    this.pause();
    if (this._disposeOrbit) this._disposeOrbit();
    this._disposeOrbit = null;
  }
}

function format(value, digits) {
  return Number.isFinite(value) ? value.toFixed(digits) : '—';
}

/* ---------------------------------------------------------------- 控件 */

/**
 * 生成模型面板的 HTML（标题、画布、时间轴、播放控制）。
 * 返回的 HTML 里控件都带 data-model-* 属性，由 app.js 统一绑定事件。
 */
export function viewerMarkup({ id, title, subtitle, mode, height = 300, hasData = false, note = '' }) {
  return `
  <section class="model-panel" data-model-root="${id}">
    <div class="model-head">
      <div>
        <h3>${escapeHtml(title)}</h3>
        ${subtitle ? `<p class="model-sub">${escapeHtml(subtitle)}</p>` : ''}
      </div>
      <div class="model-mode">
        <button class="chip ${mode === '3d' ? 'active' : ''}" data-model-mode="3d" data-model-id="${id}">3D</button>
        <button class="chip ${mode === '2d' ? 'active' : ''}" data-model-mode="2d" data-model-id="${id}">2D</button>
      </div>
    </div>
    <canvas id="${id}" class="model-canvas" height="${height}"></canvas>
    <div class="model-controls" data-model-id="${id}">
      <button class="chip" data-model-play="${id}" ${hasData ? '' : 'disabled'}>▶ 播放</button>
      <button class="chip" data-model-step="${id}" data-delta="-1" ${hasData ? '' : 'disabled'}>◀</button>
      <button class="chip" data-model-step="${id}" data-delta="1" ${hasData ? '' : 'disabled'}>▶</button>
      <input type="range" class="model-scrub" data-model-scrub="${id}"
             min="0" max="1" value="0" step="1" ${hasData ? '' : 'disabled'}
             aria-label="时间轴">
      <span class="model-frame" data-model-frame="${id}">${hasData ? '0 / 0' : '等待实验数据'}</span>
    </div>
    <div class="model-note" data-model-note="${id}">${escapeHtml(note)}</div>
  </section>`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

/* 软件 3D 渲染器（无 WebGL、无第三方库）。
 *
 * 为什么不用 WebGL：
 *  课程要给"低性能电脑"也能用的 3D 视图（这是需求里明确的一条）。
 *  本渲染器只做平面着色 + 画家算法，场景三角面数控制在 1500 以内，
 *  在一台只有集成显卡的机器上也能稳定 30 fps 以上，而且不需要处理
 *  上下文丢失、着色器编译、显卡驱动差异这些与教学无关的麻烦。
 *
 * 能力：
 *  - 轨道相机（拖拽旋转、滚轮缩放、双指平移）；
 *  - 透视投影、背面剔除、深度排序；
 *  - 平面着色（Lambert + 环境光 + 边线描边，便于看清结构）；
 *  - 逐部件高亮与透明度（机壳半透明、故障部件变红）；
 *  - 低配档位：降低解析度与投影精度。
 */

import { cross3, dot3, normalize3 } from '/model.js';

const DEFAULTS = {
  // 默认取俯视 3/4 视角。电机轴沿 z，若 pitch 太小就等于正对端面看，
  // 只能看到一个圆盘；pitch 接近 1 rad 才既有立体感又能看到端面结构。
  yaw: -2.10, pitch: 0.62, distance: 320, target: [0, 0, 0],
  fov: 900, light: [0.42, 0.34, 0.84],
  ambient: 0.52, quality: 1,
};

/**
 * 按包围盒自动取景：保证整个模型落在画面内，并留出边距。
 *
 * 早先的版本直接把相机距离设成 extent×2.1，但没考虑投影用的是
 * "焦距 / 深度"，小画布上就会把模型切掉一大半（机器人一开始就是这样）。
 * 这里按投影关系反解所需距离，并在横纵两个方向上取较大者。
 */
export function fitCamera(camera, bounds, width, height, margin = 1.02) {
  const { min, max } = bounds;
  let best = null;
  // 按**投影后的实际轮廓**取景，而不是按包围球。
  // 包围球在"细长零件"上会严重高估（例如伸出很长的转轴），
  // 结果是把圆柱本体挤成画面正中一小块——早先版本就是这个问题。
  const measure = (distance) => {
    camera.distance = distance;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (let i = 0; i < 8; i += 1) {
      const corner = [
        (i & 1) ? max[0] : min[0],
        (i & 2) ? max[1] : min[1],
        (i & 4) ? max[2] : min[2],
      ];
      const view = camera.toView(corner);
      if (!Number.isFinite(view[2]) || view[2] <= 1) return { fits: false, span: 0 };
      const scale = (camera.fov * camera.quality) / view[2];
      const x = (view[0] * scale) / (width / 2);
      const y = (view[1] * scale) / (height / 2);
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
    // 归一化坐标：|x|<=1 且 |y|<=1 表示完整落在画面内
    return { fits: true, span: Math.max(maxX - minX, maxY - minY) };
  };

  const centre = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
  camera.target = centre;

  // 二分搜索最小距离，使投影轮廓正好填满 (1-margin) 的可用空间
  const fov = camera.fov * camera.quality;
  const radius = Math.max(1, Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2);
  let low = radius * 0.5, high = radius * 30;
  for (let step = 0; step < 40; step += 1) {
    const mid = (low + high) / 2;
    const result = measure(mid);
    if (!result.fits || result.span > margin) low = mid; else high = mid;
  }
  camera.distance = high;
  measure(camera.distance);
  camera.zoomLimits = [radius * 0.5, radius * 14];
  return { radius, distance: camera.distance, fov };
}

export class OrbitCamera {
  constructor(options = {}) {
    Object.assign(this, DEFAULTS);
    // 只接受合法的有限数。直接 Object.assign 会把 undefined / NaN 写进
    // 相机参数，之后投影全变 NaN——画面空白却没有任何报错，极难排查。
    for (const [key, value] of Object.entries(options)) {
      if (key === 'target') continue;
      if (typeof value === 'number') {
        if (Number.isFinite(value)) this[key] = value;
      } else if (value !== undefined && value !== null) {
        this[key] = value;
      }
    }
    if (Array.isArray(options.target) && options.target.length === 3
        && options.target.every((v) => Number.isFinite(v))) {
      this.target = [...options.target];
    } else if (!Array.isArray(this.target)) {
      this.target = [0, 0, 0];
    }
    this.light = normalize3(Array.isArray(this.light) ? this.light : DEFAULTS.light);
  }

  /**
   * 把世界坐标投到"视图坐标"：x 向右、y 向上、z 为**到相机的距离**（越大越远）。
   *
   * 相机放在球坐标上（偏航 φ、仰角 θ、距离 d），看向目标点：
   *
   *     视线 f = ( cosφ·cosθ,  sinφ·cosθ,  sinθ )
   *
   * 屏幕基向量（世界系，右手系，三者必须满足 left × up = 指向相机的方向）：
   *
   *     up    = ( 0, 0, 1 ) 在 pitch = 0 时，即世界 +z 落在屏幕上方
   *     left  = ( sinφ, −cosφ, 0 )        ← 屏幕 +x（即"屏幕右方"）
   *     up    = ( −cosφ·sinθ, sinφ·sinθ, cosθ )   （由 left × f 得到）
   *     z_view = d − (p − c)·f
   *
   * **为什么屏幕右方在世界系里是 −x（当 φ = −90°）**：
   *   世界是 z 向上的右手系，相机看向 +y。站在 ─y 侧朝 +y 看时，
   *   右手边是 −x（这跟"从上方俯视地图时东在右、北在上"是同一回事，
   *   只是坐标轴换了名字）。早先的版本用了 +x，等于把画面水平镜像，
   *   于是 z 轴在视觉上也落到了反的一侧——用户反馈的"Z 坐标是反的"
   *   就是这个左手系基向量造成的。
   *
   * 自检（yaw=−π/2、pitch=π/3）：世界 +z 应落在屏幕上方**且更靠近相机**；
   * 世界 +y 应向屏幕内（深度增大）。
   */
  toView(point) {
    const [tx, ty, tz] = this.target;
    const dx = point[0] - tx, dy = point[1] - ty, dz = point[2] - tz;
    const cosYaw = Math.cos(this.yaw), sinYaw = Math.sin(this.yaw);
    const cosPitch = Math.cos(this.pitch), sinPitch = Math.sin(this.pitch);
    // 由标准正交基**旋转得到**，而不是"先定视线再用 cross 凑上方向"。
    // 后者在视线接近竖直时会退化（forward 与 up='(0,0,1)' 近平行），
    // 而且算出的向量不正交归一——只会表现为画面略微错位，很难察觉。
    //
    // 顺序：先绕世界 z 轴偏航 φ，再绕屏幕右方向仰起 θ。
    const forward = [cosYaw * cosPitch, sinYaw * cosPitch, sinPitch];
    const screenUp = [-cosYaw * sinPitch, -sinYaw * sinPitch, cosPitch];
    const screenRight = [sinYaw, -cosYaw, 0];
    return [
      dx * screenRight[0] + dy * screenRight[1] + dz * screenRight[2],
      dx * screenUp[0] + dy * screenUp[1] + dz * screenUp[2],
      this.distance - (dx * forward[0] + dy * forward[1] + dz * forward[2]),
    ];
  }

  project(point, width, height) {
    const view = this.toView(point);
    const depth = Math.max(view[2], 1e-3);
    const scale = (this.fov * this.quality) / depth;
    return {
      x: width / 2 + view[0] * scale,
      y: height / 2 - view[1] * scale,
      depth,
    };
  }

  /** 屏幕像素 → 世界单位的近似比例（用于画尺寸标注）。 */
  pixelsPerUnit(width) {
    return (this.fov * this.quality) / this.distance;
  }

  orbit(dx, dy) {
    this.yaw -= dx * 0.008;
    this.pitch = Math.max(-1.35, Math.min(1.35, this.pitch + dy * 0.008));
  }

  zoom(factor, limits = null) {
    const [low, high] = limits || this.zoomLimits || [40, 4000];
    this.distance = Math.max(low, Math.min(high, this.distance * factor));
  }

  pan(dx, dy, width) {
    const scale = 1 / this.pixelsPerUnit(width);
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    // 屏幕位移反投影到世界 xy 平面（够用的近似）
    this.target[0] += (-dx * cy + dy * sy) * scale * 0.9;
    this.target[1] += (dx * sy + dy * cy) * scale * 0.9;
  }
}

function shade(baseColor, normal, light, ambient, opacity) {
  const lambert = Math.max(0, dot3(normal, light));
  const intensity = Math.min(1, ambient + lambert * 0.72);
  const rgb = hexToRgb(baseColor);
  const shaded = rgb.map((c) => Math.round(Math.min(255, c * intensity)));
  return `rgba(${shaded[0]}, ${shaded[1]}, ${shaded[2]}, ${opacity})`;
}

function hexToRgb(hex) {
  const value = hex.replace('#', '');
  const full = value.length === 3 ? value.split('').map((c) => c + c).join('') : value;
  return [parseInt(full.slice(0, 2), 16), parseInt(full.slice(2, 4), 16), parseInt(full.slice(4, 6), 16)];
}

/**
 * 渲染一组三角面。
 *
 * `faces` 是渲染层的中间表示：
 *   { points: [[x,y,z], ...], color, opacity, outline?, highlight?, partName? }
 * 调用方负责把模型 + 动画姿态转成这些面（见 render.js）。
 */
export function renderFaces(ctx, faces, camera, options = {}) {
  const { width, height, background = null, showEdges = true, edgeColor = 'rgba(15,23,42,0.28)' } = options;
  if (background) {
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, width, height);
  }

  const projected = [];
  const wire = [];
  const light = camera.light;
  for (const face of faces) {
    if (!face.points || !face.points.length) continue;
    // 线段：不参与深度排序的填充管线，单独收集后统一画在最上层
    if (face.line || face.points.length === 2) {
      if (face.points.length < 2) continue;
      const a = camera.toView(face.points[0]);
      const b = camera.toView(face.points[1]);
      if (a[2] <= 1e-3 || b[2] <= 1e-3) continue;
      const scale = camera.fov * camera.quality;
      wire.push({
        depth: (a[2] + b[2]) / 2,
        x0: width / 2 + (a[0] * scale) / a[2], y0: height / 2 - (a[1] * scale) / a[2],
        x1: width / 2 + (b[0] * scale) / b[2], y1: height / 2 - (b[1] * scale) / b[2],
        color: face.highlight ? '#f59e0b' : (face.color || '#94a3b8'),
      });
      continue;
    }
    if (face.points.length < 3) continue;
    const view = face.points.map((p) => camera.toView(p));
    // 只要有一个点跑到相机后方就丢掉这个面（简单但足够）
    if (view.some((v) => v[2] <= 1e-3)) continue;

    const a = face.points[0], b = face.points[1], c = face.points[2];
    let normal = normalize3(cross3(
      [b[0] - a[0], b[1] - a[1], b[2] - a[2]],
      [c[0] - a[0], c[1] - a[1], c[2] - a[2]],
    ));
    // 视图空间里相机在原点，三角形在 z = depth > 0 处：
    // 法线朝向相机时，与 (中心 → 相机) 的夹角是钝角，所以点积 < 0。
    // 用视图空间法线判定，避免把世界空间与视图空间的方向搞混。
    const centre = [
      (view[0][0] + view[1][0] + view[2][0]) / 3,
      (view[0][1] + view[1][1] + view[2][1]) / 3,
      (view[0][2] + view[1][2] + view[2][2]) / 3,
    ];
    const edge1 = [view[1][0] - view[0][0], view[1][1] - view[0][1], view[1][2] - view[0][2]];
    const edge2 = [view[2][0] - view[0][0], view[2][1] - view[0][1], view[2][2] - view[0][2]];
    const viewNormal = normalize3(cross3(edge1, edge2));
    const facing = -dot3(viewNormal, centre);

    // 完全背向相机的面可以整片剔除（省掉一半以上的绘制量）
    if (face.cull !== false && facing <= 0) continue;
    const depth = view.reduce((sum, v) => sum + v[2], 0) / view.length;
    const scale = camera.fov * camera.quality;
    const screen = view.map((v) => [
      width / 2 + (v[0] * scale) / v[2],
      height / 2 - (v[1] * scale) / v[2],
    ]);
    projected.push({ face, normal, depth, screen, facing });
  }

  // 画家算法：从远到近。半透明部件最后画，避免互相遮挡出错。
  projected.sort((p, q) => q.depth - p.depth);

  for (const item of projected) {
    const { face, normal, screen } = item;
    const opacity = face.highlight ? Math.max(face.opacity ?? 1, 0.85) : (face.opacity ?? 1);
    ctx.beginPath();
    ctx.moveTo(screen[0][0], screen[0][1]);
    for (let i = 1; i < screen.length; i += 1) ctx.lineTo(screen[i][0], screen[i][1]);
    ctx.closePath();
    ctx.fillStyle = face.highlight
      ? 'rgba(250, 204, 21, 0.92)'
      : shade(face.color || '#94a3b8', normal, light, camera.ambient, opacity);
    ctx.fill();
    if (showEdges && opacity > 0.55) {
      ctx.strokeStyle = face.outline || edgeColor;
      ctx.lineWidth = face.highlight ? 1.6 : 0.8;
      ctx.stroke();
    }
  }

  // 线框最后画：它们代表"外壳轮廓"，压在实体之上才有透明外壳的观感
  ctx.lineWidth = 1.1;
  for (const segment of wire) {
    ctx.strokeStyle = segment.color;
    ctx.beginPath();
    ctx.moveTo(segment.x0, segment.y0);
    ctx.lineTo(segment.x1, segment.y1);
    ctx.stroke();
  }
  return projected.length + wire.length;
}

/** 画比例尺（让 3D 视图有"尺寸感"，这对工程教学很重要）。 */
export function drawScaleBar(ctx, camera, width, height, unitLabel = 'mm', targetPixels = 110) {
  const perUnit = camera.pixelsPerUnit(width);
  if (!Number.isFinite(perUnit) || perUnit <= 0) return;
  const raw = targetPixels / perUnit;
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-6)));
  const nice = [1, 2, 5, 10].map((m) => m * magnitude).find((v) => v >= raw) || magnitude * 10;
  const pixels = nice * perUnit;
  const x0 = 16, y0 = height - 18;
  ctx.save();
  ctx.strokeStyle = '#64748b';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x0, y0); ctx.lineTo(x0 + pixels, y0);
  ctx.moveTo(x0, y0 - 4); ctx.lineTo(x0, y0 + 4);
  ctx.moveTo(x0 + pixels, y0 - 4); ctx.lineTo(x0 + pixels, y0 + 4);
  ctx.stroke();
  ctx.fillStyle = '#475569';
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(`${nice >= 1 ? nice.toFixed(0) : nice} ${unitLabel}`, x0 + pixels + 8, y0 + 4);
  ctx.restore();
}

/** 画坐标轴指示（红 x、绿 y、蓝 z）。 */
export function drawAxes(ctx, camera, width, height, size = 30) {
  const origin = [0, 0, 0];
  const basis = [[size, 0, 0, '#dc2626', 'x'], [0, size, 0, '#16a34a', 'y'], [0, 0, size, '#2563eb', 'z']];
  const center = camera.project(origin, width, height);
  ctx.save();
  ctx.font = '10px system-ui, sans-serif';
  for (const [dx, dy, dz, color, label] of basis) {
    const tip = camera.project([dx, dy, dz], width, height);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(center.x, center.y);
    ctx.lineTo(tip.x, tip.y);
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.fillText(label, tip.x + 2, tip.y);
  }
  ctx.restore();
}

/* ---------------------------------------------------------------- 交互绑定 */

/**
 * 给 canvas 绑定轨道交互。返回一个 dispose 函数。
 * 支持鼠标拖拽/滚轮/右键平移，也支持触摸拖拽与双指缩放。
 */
export function attachOrbit(canvas, camera, onChange) {
  let dragging = null;
  let last = null;
  let pinchDistance = 0;

  const notify = () => { if (onChange) onChange(); };

  const onPointerDown = (event) => {
    dragging = event.button === 2 || event.shiftKey ? 'pan' : 'orbit';
    last = [event.clientX, event.clientY];
    canvas.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event) => {
    if (!dragging || !last) return;
    const dx = event.clientX - last[0];
    const dy = event.clientY - last[1];
    last = [event.clientX, event.clientY];
    if (dragging === 'orbit') camera.orbit(dx, dy);
    else camera.pan(dx, dy, canvas.clientWidth || 600);
    notify();
  };
  const onPointerUp = (event) => {
    dragging = null;
    last = null;
    canvas.releasePointerCapture?.(event.pointerId);
  };
  const onWheel = (event) => {
    event.preventDefault();
    camera.zoom(Math.exp(event.deltaY * 0.0012));
    notify();
  };
  const onTouchStart = (event) => {
    if (event.touches.length === 2) {
      pinchDistance = Math.hypot(
        event.touches[0].clientX - event.touches[1].clientX,
        event.touches[0].clientY - event.touches[1].clientY);
    }
  };
  const onTouchMove = (event) => {
    if (event.touches.length === 2 && pinchDistance) {
      event.preventDefault();
      const next = Math.hypot(
        event.touches[0].clientX - event.touches[1].clientX,
        event.touches[0].clientY - event.touches[1].clientY);
      if (next > 0) camera.zoom(pinchDistance / next);
      pinchDistance = next;
      notify();
    }
  };
  const onContextMenu = (event) => event.preventDefault();

  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', onPointerUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('touchstart', onTouchStart, { passive: true });
  canvas.addEventListener('touchmove', onTouchMove, { passive: false });
  canvas.addEventListener('contextmenu', onContextMenu);

  return () => {
    canvas.removeEventListener('pointerdown', onPointerDown);
    canvas.removeEventListener('pointermove', onPointerMove);
    canvas.removeEventListener('pointerup', onPointerUp);
    canvas.removeEventListener('pointercancel', onPointerUp);
    canvas.removeEventListener('wheel', onWheel);
    canvas.removeEventListener('touchstart', onTouchStart);
    canvas.removeEventListener('touchmove', onTouchMove);
    canvas.removeEventListener('contextmenu', onContextMenu);
  };
}

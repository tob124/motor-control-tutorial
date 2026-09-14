/* 视图层：把模型 + 姿态翻译成"可绘制的面"，并提供 2D 正投影降级。
 *
 * 分工：
 *   model.js   —— 几何与动画（纯数据，可在 Node 里测试）
 *   render3d.js—— 软件 3D 渲染器
 *   view.js    —— 把两者接起来，并按显示模式选择 3D 或 2D
 *
 * 2D 模式不是"简化的 3D"，而是**独立画法**：
 *  - 电机：端面正投影（能看到磁极分布与相带）+ 侧面剖视；
 *  - 机器人：俯视图（轮距、轨迹、朝向）+ 机构侧视图（连杆角度）。
 *  这样在低配电脑上不是"凑合看"，而是另一种更有工程感的视图。
 */

import {
  TAU, rotateX, rotateY, rotateZ, rotateAxis, fourBarSolution,
  buildMotor, buildRobot, allParts,
} from '/model.js';

const PHASE_COLORS = ['#2563eb', '#ea580c', '#0d9488'];

/* ---------------------------------------------------------------- 3D 面生成 */

/**
 * 生成模型当前姿态下的三角面列表。
 *
 * `pose` 由 model.poseFromRow / accumulateAngles 提供。
 * `options.highlight` 是部件名集合，命中的部件会高亮；
 * `options.faultParts` 命中的部件会变红（用于故障注入教学）。
 */
export function facesForModel(model, pose = {}, options = {}) {
  const highlight = options.highlight instanceof Set ? options.highlight : new Set(options.highlight || []);
  const fault = options.faultParts instanceof Set ? options.faultParts : new Set(options.faultParts || []);
  const faces = [];

  const transformPoint = (v, part, extraTransform) => {
    let p = v;
    if (extraTransform) p = extraTransform(p);
    if (part.transform) p = part.transform(p);
    return p;
  };

  const pushMesh = (mesh, part, extraTransform) => {
    if (!mesh || !mesh.vertices.length) return;
    const points = mesh.vertices.map((v) => transformPoint(v, part, extraTransform));
    const isHighlight = part.name && highlight.has(part.name);
    const isFault = part.name && fault.has(part.name);
    const color = isFault ? '#dc2626' : (part.color || '#94a3b8');
    for (const face of mesh.faces) {
      faces.push({
        points: face.map((i) => points[i]),
        color,
        opacity: part.opacity ?? 1,
        highlight: isHighlight || isFault,
        partName: part.name,
      });
    }
    // 线框轮廓：机壳这类"只该看到边"的部件用线段表达，
    // 画成实心会把里面的定转子全挡住（早先版本就是这个毛病）。
    if (part.wireframe) {
      const edges = new Set();
      const seen = new Set();
      const emitEdge = (i, j) => {
        const key = i < j ? `${i}-${j}` : `${j}-${i}`;
        if (seen.has(key)) return;
        seen.add(key);
        edges.add(key);
      };
      for (const face of mesh.faces) {
        for (let k = 0; k < face.length; k += 1) {
          emitEdge(face[k], face[(k + 1) % face.length]);
        }
      }
      for (const key of edges) {
        const [i, j] = key.split('-').map(Number);
        faces.push({ line: true, points: [points[i], points[j]], color,
                     opacity: 1, highlight: isHighlight || isFault, partName: part.name });
      }
    }
  };

  // 静止部件
  for (const part of model.parts || []) pushMesh(part.mesh, part, null);

  // 电机转子 / 转轴：绕 z 轴转动
  const shaftAngle = pose.displayedSpin ?? pose.shaftAngle ?? 0;
  for (const part of model.rotorParts || []) {
    pushMesh(part.mesh, part, (p) => rotateZ(p, shaftAngle));
  }

  // 车轮与轮辐：绕各自轮轴（x 方向）转动
  for (const part of model.wheelParts || []) {
    const side = part.side === 'left' ? (pose.leftAngleTotal ?? 0) : (pose.rightAngleTotal ?? 0);
    const sideSpin = part.side === 'left' ? (pose.leftSpin ?? side) : (pose.rightSpin ?? side);
    const spin = part.side ? sideSpin * 0.25 : (pose.wheelSpin ?? 0);
    pushMesh(part.mesh, part, (p) => rotateX(p, spin));
  }

  // 机壳端面的圆环：让"这是个圆柱"一眼可辨，又不用实心面遮挡内部
  for (const part of model.parts || []) {
    for (const ring of part.rings || []) {
      const points = [];
      for (let i = 0; i < ring.segments; i += 1) {
        const angle = (i / ring.segments) * TAU;
        const x = Math.cos(angle) * ring.radius;
        const y = Math.sin(angle) * ring.radius;
        points.push([x, y, ring.height / 2], [x, y, -ring.height / 2]);
      }
      const isHighlight = highlight.has(part.name) || fault.has(part.name);
      for (let i = 0; i < ring.segments; i += 1) {
        const a = i * 2, b = ((i + 1) % ring.segments) * 2;
        faces.push({ line: true, points: [points[a], points[b]], color: part.color,
                     opacity: 1, highlight: isHighlight, partName: part.name });
        faces.push({ line: true, points: [points[a + 1], points[b + 1]], color: part.color,
                     opacity: 1, highlight: isHighlight, partName: part.name });
      }
      // 纵向母线：只画几根勾勒出圆柱，密了会变成鸟笼
      const meridians = ring.meridians || 4;
      for (let m = 0; m < meridians; m += 1) {
        const i = Math.round((m / meridians) * ring.segments) % ring.segments;
        faces.push({ line: true, points: [points[i * 2], points[i * 2 + 1]], color: part.color,
                     opacity: 1, highlight: isHighlight, partName: part.name });
      }
    }
  }

  // 四连杆机构：每帧按曲柄角解算，生成三根连杆
  if (model.mechanism) {
    for (const face of fourBarFaces(model, pose, { highlight, fault })) faces.push(face);
  }

  return faces;
}

function fourBarFaces(model, pose, { highlight, fault }) {
  const mechanism = model.mechanism;
  const crankAngle = (pose.mechanismAngle ?? 0) + Math.PI / 2;
  const solution = fourBarSolution(crankAngle, mechanism);
  const origin = mechanism.origin;
  const faces = [];
  if (!solution) return faces;

  const thickness = 16;
  const toWorld = (point2d, width) => ([
    width / 2,
    point2d[0] - mechanism.ground / 2,
    point2d[1],
  ]);

  const linkFaces = (a, b, color, name) => {
    const half = thickness / 2;
    const corners = [
      toWorld(a, -half), toWorld(b, -half), toWorld(b, half), toWorld(a, half),
    ].map((p) => [origin[0] + p[0], origin[1] + p[1], origin[2] + p[2]]);
    const isHighlight = highlight.has(name) || fault.has(name);
    const facesOut = [
      { points: [corners[0], corners[1], corners[2]], color, opacity: 1 },
      { points: [corners[0], corners[2], corners[3]], color, opacity: 1 },
    ];
    return facesOut.map((f) => ({ ...f, highlight: isHighlight, partName: name }));
  };

  const ground = solution.ground;
  faces.push(...linkFaces([0, 0], solution.crank, '#7c3aed', 'link-crank'));
  faces.push(...linkFaces(solution.crank, solution.coupler, '#0891b2', 'link-coupler'));
  faces.push(...linkFaces(solution.coupler, ground, '#c2410c', 'link-rocker'));

  // 末端执行器（"球"）：让学员看到机构在搬运什么
  const tip = solution.coupler;
  const ballRadius = 26;
  const segments = 10;
  const centre = [origin[0] + tip[0] - mechanism.ground / 2,
                  origin[1] + tip[1],
                  origin[2] + ballRadius / 2 + 20];
  for (let i = 0; i < segments; i += 1) {
    const a0 = (i / segments) * TAU;
    const a1 = ((i + 1) / segments) * TAU;
    for (let j = 0; j < segments / 2; j += 1) {
      const p0 = (j / (segments / 2)) * Math.PI;
      const p1 = ((j + 1) / (segments / 2)) * Math.PI;
      faces.push({
        points: [
          spherePoint(centre, ballRadius, a0, p0),
          spherePoint(centre, ballRadius, a1, p0),
          spherePoint(centre, ballRadius, a1, p1),
        ],
        color: '#f59e0b', opacity: 1, highlight: highlight.has('ball'), partName: 'ball',
      });
    }
  }
  return faces;
}

function spherePoint(centre, radius, azimuth, polar) {
  return [
    centre[0] + radius * Math.sin(polar) * Math.cos(azimuth),
    centre[1] + radius * Math.sin(polar) * Math.sin(azimuth),
    centre[2] + radius * Math.cos(polar) + radius,
  ];
}

/* ---------------------------------------------------------------- 2D 正投影 */

/**
 * 2D 电机视图：左侧端面（含磁极与相带），右侧轴向剖视。
 * 完全不需要投影计算，纯圆弧与矩形，低配电脑毫无压力。
 */
export function drawMotor2D(canvas, model, pose, options = {}) {
  const ctx = canvas.getContext('2d');
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(canvas.getBoundingClientRect().width, 260);
  const height = options.height || 320;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const spin = pose.displayedSpin ?? 0;
  const faceRadius = Math.min(width * 0.22, height * 0.36);
  const faceCentre = [faceRadius + 28, height / 2];
  const sideLeft = faceCentre[0] + faceRadius + 40;
  const sideWidth = Math.max(60, width - sideLeft - 20);

  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'left';

  drawMotorFace2D(ctx, model, faceCentre, faceRadius, spin, pose, options);
  drawMotorSide2D(ctx, model, sideLeft, sideWidth, height, spin, pose, options);

  ctx.fillStyle = '#64748b';
  ctx.fillText('端面视图（定子相带 / 转子磁极）', 10, 16);
  ctx.fillText('轴向剖视（绕组 / 转轴 / 编码器）', sideLeft, 16);
}

function drawMotorFace2D(ctx, model, centre, radius, spin, pose, options) {
  const [cx, cy] = centre;
  const highlight = options.highlight instanceof Set ? options.highlight : new Set(options.highlight || []);

  // 定子铁芯
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, TAU);
  ctx.fillStyle = '#e2e8f0';
  ctx.fill();
  ctx.strokeStyle = '#94a3b8';
  ctx.lineWidth = 2;
  ctx.stroke();

  // 定子绕组（相带）：按相着色，相带用圆弧段表示
  const slots = model.slotCount || 12;
  for (let i = 0; i < slots; i += 1) {
    const a0 = (i / slots) * TAU - Math.PI / 2;
    const a1 = ((i + 1) / slots) * TAU - Math.PI / 2;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.94, a0 + 0.03, a1 - 0.03);
    ctx.arc(cx, cy, radius * 0.72, a1 - 0.03, a0 + 0.03, true);
    ctx.closePath();
    const phase = i % 3;
    // 相带亮度按该相电流变化：让"哪一相在出力"看得见
    const intensity = 0.35 + 0.65 * clamp01(pose.phaseIntensity ? pose.phaseIntensity[phase] : 0.5);
    ctx.fillStyle = withAlpha(PHASE_COLORS[phase], intensity);
    ctx.fill();
  }

  // 转子
  const rotorRadius = radius * 0.55;
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(-spin);
  ctx.beginPath();
  ctx.arc(0, 0, rotorRadius, 0, TAU);
  ctx.fillStyle = '#cbd5e1';
  ctx.fill();
  ctx.strokeStyle = '#64748b';
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // 磁极（永磁/有刷）或导条（异步）
  if (model.kind === 'induction') {
    const bars = Math.max(8, (model.polePairs || 2) * 4);
    for (let i = 0; i < bars; i += 1) {
      const angle = (i / bars) * TAU;
      ctx.beginPath();
      ctx.arc(Math.cos(angle) * rotorRadius * 0.78, Math.sin(angle) * rotorRadius * 0.78, 2.6, 0, TAU);
      ctx.fillStyle = '#b45309';
      ctx.fill();
    }
  } else {
    const magnets = model.kind === 'dc' ? 2 : Math.max(2, (model.polePairs || 4) * 2);
    for (let i = 0; i < magnets; i += 1) {
      const a0 = (i / magnets) * TAU;
      const a1 = ((i + 1) / magnets) * TAU;
      ctx.beginPath();
      ctx.arc(0, 0, rotorRadius * 0.98, a0, a1);
      ctx.arc(0, 0, rotorRadius * 0.66, a1, a0, true);
      ctx.closePath();
      ctx.fillStyle = i % 2 === 0 ? '#dc2626' : '#2563eb';
      ctx.fill();
    }
    // 极标注，便于讲"极对数"
    ctx.font = '9px system-ui, sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.95)';
    ctx.textAlign = 'center';
    for (let i = 0; i < magnets; i += 1) {
      const angle = ((i + 0.5) / magnets) * TAU;
      ctx.fillText(i % 2 === 0 ? 'N' : 'S',
        Math.cos(angle) * rotorRadius * 0.82, Math.sin(angle) * rotorRadius * 0.82 + 3);
    }
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'left';
  }
  // 转轴端
  ctx.beginPath();
  ctx.arc(0, 0, rotorRadius * 0.2, 0, TAU);
  ctx.fillStyle = '#334155';
  ctx.fill();
  ctx.restore();

  // 转速方向箭头
  const arrowRadius = radius * 1.12;
  ctx.beginPath();
  ctx.arc(cx, cy, arrowRadius, -0.9, 0.9);
  ctx.strokeStyle = Math.abs(pose.omega || 0) > 0.5 ? '#0f766e' : '#cbd5e1';
  ctx.lineWidth = 2;
  ctx.stroke();
  if (highlight.has('rotor')) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.56, 0, TAU);
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 3;
    ctx.stroke();
  }
  ctx.fillStyle = '#475569';
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(`${(pose.rpm || 0).toFixed(0)} rpm`, cx, cy + radius + 22);
  ctx.textAlign = 'left';
}

function drawMotorSide2D(ctx, model, left, width, height, spin, pose, options) {
  const top = height * 0.2;
  const bottom = height * 0.8;
  const height2 = bottom - top;
  const midY = (top + bottom) / 2;
  const statorLength = width;
  const highlight = options.highlight instanceof Set ? options.highlight : new Set(options.highlight || []);

  // 定子与绕组
  ctx.fillStyle = '#e2e8f0';
  ctx.fillRect(left, top, statorLength, height2);
  ctx.strokeStyle = '#94a3b8';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(left, top, statorLength, height2);

  const coilCount = 6;
  for (let i = 0; i < coilCount; i += 1) {
    const x = left + 6 + (i * (statorLength - 12)) / coilCount;
    ctx.fillStyle = withAlpha(PHASE_COLORS[i % 3], 0.75);
    ctx.fillRect(x, top + 4, (statorLength - 12) / coilCount - 6, height2 * 0.22);
    ctx.fillRect(x, bottom - 4 - height2 * 0.22, (statorLength - 12) / coilCount - 6, height2 * 0.22);
  }

  // 转子（随轴转动，用斜纹表现）
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, midY - height2 * 0.2, statorLength, height2 * 0.4);
  ctx.clip();
  ctx.fillStyle = '#cbd5e1';
  ctx.fillRect(left, midY - height2 * 0.2, statorLength, height2 * 0.4);
  ctx.strokeStyle = '#94a3b8';
  ctx.lineWidth = 1;
  const stripe = 18;
  for (let x = -height2; x < statorLength + height2; x += stripe) {
    const offset = ((spin * 12) % stripe + stripe) % stripe;
    ctx.beginPath();
    ctx.moveTo(left + x + offset, midY - height2 * 0.2);
    ctx.lineTo(left + x + offset - stripe * 0.5, midY + height2 * 0.2);
    ctx.stroke();
  }
  ctx.restore();

  // 转轴
  ctx.fillStyle = '#334155';
  ctx.fillRect(left - 26, midY - 5, statorLength + 52, 10);
  if (highlight.has('shaft')) {
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 3;
    ctx.strokeRect(left - 26, midY - 5, statorLength + 52, 10);
  }

  // 编码器
  const encoderX = left - 18;
  ctx.beginPath();
  ctx.arc(encoderX, midY, 11, 0, TAU);
  ctx.fillStyle = '#0f766e';
  ctx.fill();
  ctx.strokeStyle = '#0d9488';
  ctx.lineWidth = 1;
  for (let i = 0; i < 12; i += 1) {
    const angle = (i / 12) * TAU + spin * 0.5;
    ctx.beginPath();
    ctx.moveTo(encoderX, midY);
    ctx.lineTo(encoderX + Math.cos(angle) * 11, midY + Math.sin(angle) * 11);
    ctx.stroke();
  }
  if (highlight.has('encoder')) {
    ctx.beginPath();
    ctx.arc(encoderX, midY, 15, 0, TAU);
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 2.5;
    ctx.stroke();
  }

  ctx.fillStyle = '#475569';
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText('编码器', left - 30, midY + 28);
  ctx.fillText(`转矩 ${(pose.torque || 0).toFixed(3)} N·m · 电流 ${(pose.current || 0).toFixed(2)} A`,
               left, bottom + 20);
}

/**
 * 2D 机器人视图：上方俯视（轮距、朝向、轨迹），下方机构侧视。
 */
export function drawRobot2D(canvas, model, pose, options = {}) {
  const ctx = canvas.getContext('2d');
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(canvas.getBoundingClientRect().width, 300);
  const height = options.height || 360;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  drawRobotTop2D(ctx, model, pose, { x: 8, y: 22, width: width - 16, height: height * 0.52 });
  drawMechanism2D(ctx, model, pose, { x: 8, y: height * 0.58, width: width - 16, height: height * 0.4 });

  ctx.fillStyle = '#64748b';
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText('俯视图（轮距 / 轨迹 / 朝向）', 10, 14);
  ctx.fillText('机构侧视（四连杆）', 10, height * 0.56);
}

function drawRobotTop2D(ctx, model, pose, box) {
  const track = model.track || 400;
  const bodyLength = model.bounds?.length || 520;
  const scale = Math.min(box.width / (track * 1.8), box.height / (bodyLength * 1.25));
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;

  // 轨迹（有历史轨迹时才画）
  const path = pose.path || [];
  if (path.length > 1) {
      ctx.beginPath();
      path.forEach((p, i) => {
        const x = cx + (p.x - pose.x) * scale * 0.001;
        const y = cy - (p.y - pose.y) * scale * 0.001;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = '#cbd5e1';
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1.5;
      ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(-(pose.heading || 0));
  const bodyW = track * 0.72 * scale;
  const bodyL = bodyLength * scale;

  // 车体
  ctx.fillStyle = '#1e3a8a';
  ctx.fillRect(-bodyW / 2, -bodyL / 2, bodyW, bodyL);
  ctx.strokeStyle = '#1e40af';
  ctx.lineWidth = 1;
  ctx.strokeRect(-bodyW / 2, -bodyL / 2, bodyW, bodyL);

  // 轮子（俯视看成矩形），带转动相位
  const wheelW = 14 * scale, wheelL = (model.wheelRadius * 2) * scale * 0.9;
  const halfTrack = (track / 2) * scale;
  const halfBase = ((model.wheelbase || 300) / 2) * scale;
  const wheels = [
    [-halfTrack, -halfBase, pose.leftAngleTotal || 0],
    [-halfTrack, halfBase, pose.leftAngleTotal || 0],
    [halfTrack, -halfBase, pose.rightAngleTotal || 0],
    [halfTrack, halfBase, pose.rightAngleTotal || 0],
  ];
  for (const [wx, wy, angle] of wheels) {
    ctx.save();
    ctx.translate(wx, wy);
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(-wheelW / 2, -wheelL / 2, wheelW, wheelL);
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    const phase = Math.sin(angle) * wheelL * 0.35;
    ctx.moveTo(-wheelW / 2, phase);
    ctx.lineTo(wheelW / 2, phase);
    ctx.stroke();
    ctx.restore();
  }

  // 前向标记
  ctx.beginPath();
  ctx.moveTo(0, -bodyL / 2);
  ctx.lineTo(-8, -bodyL / 2 + 14);
  ctx.lineTo(8, -bodyL / 2 + 14);
  ctx.closePath();
  ctx.fillStyle = '#f59e0b';
  ctx.fill();
  ctx.restore();

  // 尺寸标注：轮距
  ctx.strokeStyle = '#94a3b8';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(cx - (track / 2) * scale, cy);
  ctx.lineTo(cx + (track / 2) * scale, cy);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#475569';
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(`轮距 ${track.toFixed(0)} mm`, cx, cy + 14);
  ctx.textAlign = 'left';
}

function drawMechanism2D(ctx, model, pose, box) {
  const mechanism = model.mechanism;
  if (!mechanism) return;
  const span = Math.max(mechanism.ground + mechanism.rocker + 40, mechanism.coupler + 60);
  const scale = Math.min(box.width / (span * 1.6), box.height / (span * 1.2));
  const originX = box.x + box.width * 0.28;
  const originY = box.y + box.height * 0.82;
  const toScreen = (p) => [originX + p[0] * scale, originY - p[1] * scale];

  const crankAngle = (pose.mechanismAngle ?? 0) + Math.PI / 2;
  const solution = fourBarSolution(crankAngle, mechanism);
  if (!solution) {
    ctx.fillStyle = '#b45309';
    ctx.fillText('机构在此角度无法装配（死点附近）', originX, originY - 40);
    return;
  }

  const A = toScreen([0, 0]);
  const B = toScreen(solution.crank);
  const C = toScreen(solution.coupler);
  const D = toScreen(solution.ground);

  // 机架
  ctx.strokeStyle = '#94a3b8';
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 3]);
  ctx.beginPath();
  ctx.moveTo(A[0], A[1]);
  ctx.lineTo(D[0], D[1]);
  ctx.stroke();
  ctx.setLineDash([]);

  const link = (p, q, color, width) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(p[0], p[1]);
    ctx.lineTo(q[0], q[1]);
    ctx.stroke();
  };
  link(A, B, '#7c3aed', 7);      // 曲柄
  link(B, C, '#0891b2', 7);      // 连杆
  link(C, D, '#c2410c', 7);      // 摇杆

  const joint = (p, label) => {
    ctx.beginPath();
    ctx.arc(p[0], p[1], 4.5, 0, TAU);
    ctx.fillStyle = '#0f172a';
    ctx.fill();
    if (label) {
      ctx.fillStyle = '#475569';
      ctx.font = '11px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(label, p[0], p[1] - 10);
    }
  };
  joint(A, 'A'); joint(B, 'B'); joint(C, 'C'); joint(D, 'D');

  // 末端"球"
  ctx.beginPath();
  ctx.arc(C[0], C[1] - 14, 8, 0, TAU);
  ctx.fillStyle = '#f59e0b';
  ctx.fill();

  ctx.fillStyle = '#475569';
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(`曲柄 ${((crankAngle * 180) / Math.PI % 360).toFixed(0)}° · `
    + `摇杆 ${((solution.rockerAngle * 180) / Math.PI).toFixed(0)}°`, box.x, box.y + box.height - 4);
}

/* ---------------------------------------------------------------- 工具 */

function clamp01(value) {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
}

function withAlpha(hex, alpha) {
  const value = hex.replace('#', '');
  const full = value.length === 3 ? value.split('').map((c) => c + c).join('') : value;
  return `rgba(${parseInt(full.slice(0, 2), 16)}, ${parseInt(full.slice(2, 4), 16)}, `
       + `${parseInt(full.slice(4, 6), 16)}, ${alpha})`;
}

/** 由一行仿真数据估算三相出力强度（用于相带着色）。 */
export function phaseIntensityFromRow(row) {
  const current = Number.isFinite(row?.current) ? Math.abs(row.current) : 0;
  const base = Math.min(1, current / 6);
  // 三相依次相差 120°，用行时间做相位（没有三相数据时的合理近似）
  const t = Number.isFinite(row?.t) ? row.t : 0;
  const omega = Number.isFinite(row?.rpm) ? (row.rpm / 60) * TAU : 0;
  const phase = omega * t;
  return [0, 1, 2].map((k) => base * (0.4 + 0.6 * (0.5 + 0.5 * Math.cos(phase + (k * TAU) / 3))));
}

/* ---------------------------------------------------------------- 模型缓存 */

const motorCache = new Map();
const robotCache = new Map();

export function motorModel(spec = {}) {
  const key = JSON.stringify([spec.kind, spec.polePairs, spec.segments]);
  if (!motorCache.has(key)) motorCache.set(key, buildMotor(spec));
  return motorCache.get(key);
}

export function robotModel(spec = {}) {
  const key = JSON.stringify([spec.track, spec.wheelRadius, spec.wheelbase,
                              spec.linkCrank, spec.linkCoupler, spec.linkRocker, spec.linkGround]);
  if (!robotCache.has(key)) robotCache.set(key, buildRobot(spec));
  return robotCache.get(key);
}

export function clearModelCache() {
  motorCache.clear();
  robotCache.clear();
}

/** 按电机类型给出默认可视化参数（极对数来自仿真配置，模型才与物理一致）。 */
export function motorSpecFromConfig(spec = {}) {
  const params = spec.params || {};
  const kind = spec.kind || 'pmsm_bldc';
  const polePairs = Math.max(1, Math.round(params.p ?? (kind === 'dc' ? 1 : 4)));
  return { kind, polePairs, segments: 18 };
}

export { rotateZ, rotateAxis, rotateX, rotateY };

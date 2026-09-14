/* Canvas 绘图内核。
 *
 * 没有第三方图表库，也不需要：课程要画的就那么几类图。
 * 全部用 Canvas 2D 手绘，好处是零依赖、可控、且能和机器人俯视图共用一套坐标变换。
 *
 * 约定：
 *  - 所有绘图函数接受 devicePixelRatio，保证在高分屏上不模糊；
 *  - 空数据时画"运行实验后显示曲线"，绝不画假数据；
 *  - 每条线必须带单位标注，这是课程对图表的基本要求。
 */

const DPR = () => Math.min(window.devicePixelRatio || 1, 2);

function prepare(canvas, height) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(rect.width, 220);
  const h = height || 200;
  canvas.width = Math.round(width * DPR());
  canvas.height = Math.round(h * DPR());
  const ctx = canvas.getContext('2d');
  ctx.setTransform(DPR(), 0, 0, DPR(), 0, 0);
  ctx.clearRect(0, 0, width, h);
  return { ctx, width, height: h };
}

function emptyState(ctx, width, height, message) {
  ctx.fillStyle = '#94a3b8';
  ctx.font = '13px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(message || '运行实验后显示计算曲线', width / 2, height / 2);
}

function niceNumber(value) {
  if (!Number.isFinite(value) || value === 0) return '0';
  const abs = Math.abs(value);
  if (abs >= 10000 || abs < 0.001) return value.toExponential(1);
  if (abs >= 100) return value.toFixed(0);
  if (abs >= 1) return value.toFixed(abs >= 10 ? 1 : 2);
  return value.toFixed(3);
}

/** 多序列时序图。series 是行数组；lines 描述要画哪些键。 */
export function timeSeries(canvas, rows, lines, options = {}) {
  const { ctx, width, height } = prepare(canvas, options.height || 240);
  const left = 62, right = 16, top = 22, bottom = 34;
  const iw = width - left - right, ih = height - top - bottom;
  if (!rows || !rows.length) { emptyState(ctx, width, height, options.empty); return; }

  const times = rows.map((row) => row.t || 0);
  const tEnd = Math.max(times[times.length - 1] || 1, 1e-6);
  // 只聚合被引用的列，避免把整张表都算进极值
  let yMin = Infinity, yMax = -Infinity;
  for (const row of rows) {
    for (const line of lines) {
      const v = row[line.key];
      if (Number.isFinite(v)) { if (v < yMin) yMin = v; if (v > yMax) yMax = v; }
    }
  }
  if (!Number.isFinite(yMin)) { emptyState(ctx, width, height, options.empty); return; }
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  const pad = (yMax - yMin) * 0.08;
  yMin -= pad; yMax += pad;

  const xOf = (t) => left + (t / tEnd) * iw;
  const yOf = (v) => top + ih - ((v - yMin) / (yMax - yMin)) * ih;

  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i += 1) {
    const y = top + (ih * i) / 4;
    ctx.strokeStyle = '#e8edf5';
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillStyle = '#8792a6';
    ctx.fillText(niceNumber(yMax - ((yMax - yMin) * i) / 4), left - 8, y + 4);
  }
  ctx.textAlign = 'center';
  for (let i = 0; i <= 5; i += 1) {
    const x = left + (iw * i) / 5;
    ctx.fillStyle = '#8792a6';
    ctx.fillText(niceNumber((tEnd * i) / 5), x, height - 12);
  }

  for (const line of lines) {
    ctx.strokeStyle = line.color;
    ctx.lineWidth = line.width || 1.8;
    ctx.setLineDash(line.dash || []);
    ctx.beginPath();
    let started = false;
    for (const row of rows) {
      const v = row[line.key];
      if (!Number.isFinite(v)) continue;
      const x = xOf(row.t || 0), y = yOf(v);
      if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);

  // 轴标题
  ctx.fillStyle = '#5b6577';
  ctx.textAlign = 'center';
  ctx.font = '11px system-ui, sans-serif';
  ctx.fillText(options.xLabel || '时间 t / s', left + iw / 2, height - 0.5);
  if (options.yLabel) {
    ctx.save();
    ctx.translate(14, top + ih / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText(options.yLabel, 0, 0);
    ctx.restore();
  }
}

/** XY 曲线族：每个 family 一组点，用于机械特性、转矩—转差等。 */
export function family(canvas, families, options = {}) {
  const { ctx, width, height } = prepare(canvas, options.height || 280);
  const left = 62, right = 16, top = 22, bottom = 36;
  const iw = width - left - right, ih = height - top - bottom;
  const usable = (families || []).filter((f) => (f.points || []).length);
  if (!usable.length) { emptyState(ctx, width, height, options.empty); return; }

  const xKey = options.xKey || 'speed_rpm';
  const yKey = options.yKey || 'torque_nm';
  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  for (const f of usable) {
    for (const p of f.points) {
      const x = p[xKey], y = p[yKey];
      if (Number.isFinite(x) && Number.isFinite(y)) {
        xMin = Math.min(xMin, x); xMax = Math.max(xMax, x);
        yMin = Math.min(yMin, y); yMax = Math.max(yMax, y);
      }
    }
  }
  for (const marker of options.markers || []) {
    if (Number.isFinite(marker.x)) { xMin = Math.min(xMin, marker.x); xMax = Math.max(xMax, marker.x); }
  }
  if (!Number.isFinite(xMin)) { emptyState(ctx, width, height, options.empty); return; }
  if (xMin === xMax) { xMin -= 1; xMax += 1; }
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  yMin = Math.min(yMin, 0); yMax = Math.max(yMax, 0);
  const padY = (yMax - yMin) * 0.08;
  yMin -= padY; yMax += padY;
  const padX = (xMax - xMin) * 0.04;
  xMin -= padX; xMax += padX;

  const xOf = (v) => left + ((v - xMin) / (xMax - xMin)) * iw;
  const yOf = (v) => top + ih - ((v - yMin) / (yMax - yMin)) * ih;

  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i += 1) {
    const y = top + (ih * i) / 4;
    ctx.strokeStyle = '#e8edf5';
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillStyle = '#8792a6';
    ctx.fillText(niceNumber(yMax - ((yMax - yMin) * i) / 4), left - 8, y + 4);
  }
  ctx.textAlign = 'center';
  for (let i = 0; i <= 4; i += 1) {
    const x = left + (iw * i) / 4;
    ctx.fillStyle = '#8792a6';
    ctx.fillText(niceNumber(xMin + ((xMax - xMin) * i) / 4), x, height - 16);
  }

  // 零线
  if (yMin < 0 && yMax > 0) {
    ctx.strokeStyle = '#cbd5e1';
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(left, yOf(0)); ctx.lineTo(width - right, yOf(0)); ctx.stroke();
    ctx.setLineDash([]);
  }

  const palette = ['#1d4ed8', '#c2410c', '#0f766e', '#7c3aed', '#b91c1c', '#0369a1'];
  usable.forEach((f, index) => {
    ctx.strokeStyle = f.color || palette[index % palette.length];
    ctx.lineWidth = 1.9;
    ctx.beginPath();
    f.points.forEach((p, i) => {
      const x = xOf(p[xKey]), y = yOf(p[yKey]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  for (const marker of options.markers || []) {
    if (!Number.isFinite(marker.x)) continue;
    const x = xOf(marker.x);
    ctx.strokeStyle = '#94a3b8';
    ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + ih); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'center';
    ctx.fillText(marker.label, x, top - 6);
  }

  ctx.fillStyle = '#5b6577';
  ctx.fillText(options.xLabel || '', left + iw / 2, height - 1);
  if (options.yLabel) {
    ctx.save();
    ctx.translate(14, top + ih / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText(options.yLabel, 0, 0);
    ctx.restore();
  }
  // 图例
  let legendX = left + 8, legendY = top + 12;
  ctx.textAlign = 'left';
  usable.forEach((f, index) => {
    if (!f.label) return;
    ctx.fillStyle = f.color || palette[index % palette.length];
    ctx.fillRect(legendX, legendY - 7, 14, 3);
    ctx.fillStyle = '#475569';
    ctx.fillText(f.label, legendX + 19, legendY - 3);
    legendY += 15;
  });
}

/** 轨迹族（时间序列的多个版本，如不同增益下的阶跃响应）。 */
export function traceFamily(canvas, families, options = {}) {
  const { ctx, width, height } = prepare(canvas, options.height || 260);
  const left = 62, right = 16, top = 22, bottom = 36;
  const iw = width - left - right, ih = height - top - bottom;
  const usable = (families || []).filter((f) => (f.points || []).length);
  if (!usable.length) { emptyState(ctx, width, height, options.empty); return; }

  const xKey = options.xKey || 't';
  const yKey = options.yKey || 'rpm';
  let xMax = 0, yMin = Infinity, yMax = -Infinity;
  for (const f of usable) {
    for (const p of f.points) {
      const x = p[xKey], y = p[yKey];
      if (Number.isFinite(x)) xMax = Math.max(xMax, x);
      if (Number.isFinite(y)) { yMin = Math.min(yMin, y); yMax = Math.max(yMax, y); }
    }
  }
  if (!Number.isFinite(yMin)) { emptyState(ctx, width, height, options.empty); return; }
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  const pad = (yMax - yMin) * 0.1;
  yMin -= pad; yMax += pad;
  xMax = Math.max(xMax, 1e-6);

  const xOf = (v) => left + (v / xMax) * iw;
  const yOf = (v) => top + ih - ((v - yMin) / (yMax - yMin)) * ih;

  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i += 1) {
    const y = top + (ih * i) / 4;
    ctx.strokeStyle = '#e8edf5';
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillStyle = '#8792a6';
    ctx.fillText(niceNumber(yMax - ((yMax - yMin) * i) / 4), left - 8, y + 4);
  }
  ctx.textAlign = 'center';
  for (let i = 0; i <= 4; i += 1) {
    const x = left + (iw * i) / 4;
    ctx.fillStyle = '#8792a6';
    ctx.fillText(niceNumber((xMax * i) / 4), x, height - 16);
  }

  const palette = ['#1d4ed8', '#c2410c', '#0f766e', '#7c3aed', '#b91c1c'];
  let legendY = top + 12;
  usable.forEach((f, index) => {
    const color = palette[index % palette.length];
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.9;
    ctx.beginPath();
    f.points.forEach((p, i) => {
      const x = xOf(p[xKey] || 0), y = yOf(p[yKey]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    if (f.label) {
      ctx.fillStyle = color;
      ctx.textAlign = 'left';
      ctx.fillRect(left + 8, legendY - 7, 14, 3);
      ctx.fillStyle = '#475569';
      ctx.fillText(f.label, left + 27, legendY - 3);
      legendY += 15;
    }
  });

  ctx.fillStyle = '#5b6577';
  ctx.textAlign = 'center';
  ctx.fillText(options.xLabel || '时间 t / s', left + iw / 2, height - 1);
  if (options.yLabel) {
    ctx.save();
    ctx.translate(14, top + ih / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText(options.yLabel, 0, 0);
    ctx.restore();
  }
}

/** Bode 图：上幅值下相位。 */
export function bode(canvas, points, options = {}) {
  const { ctx, width, height } = prepare(canvas, options.height || 300);
  const usable = (points || []).filter((p) => Number.isFinite(p.frequency_hz) && Number.isFinite(p.gain_db));
  if (!usable.length) { emptyState(ctx, width, height, '运行扫频后显示频率响应'); return; }
  const left = 58, right = 16, gap = 26, bottom = 34, top = 18;
  const totalH = height - top - bottom - gap;
  const magH = totalH * 0.58, phaseH = totalH * 0.42;
  const iw = width - left - right;

  const fMin = Math.log10(Math.min(...usable.map((p) => p.frequency_hz)));
  const fMax = Math.log10(Math.max(...usable.map((p) => p.frequency_hz)));
  const xOf = (f) => left + ((Math.log10(f) - fMin) / Math.max(fMax - fMin, 1e-6)) * iw;

  const gains = usable.map((p) => p.gain_db);
  let gMin = Math.min(-30, Math.floor(Math.min(...gains) / 5) * 5);
  const gMax = Math.ceil(Math.max(...gains, 3) / 5) * 5;
  const yMag = (g) => top + magH - ((g - gMin) / (gMax - gMin)) * magH;

  const phases = usable.filter((p) => Number.isFinite(p.phase_deg)).map((p) => p.phase_deg);
  const pMin = phases.length ? Math.min(-180, Math.floor(Math.min(...phases) / 30) * 30) : -180;
  const pMax = phases.length ? Math.max(0, Math.ceil(Math.max(...phases) / 30) * 30) : 0;
  const phaseTop = top + magH + gap;
  const yPhase = (v) => phaseTop + phaseH - ((v - pMin) / Math.max(pMax - pMin, 1e-6)) * phaseH;

  ctx.font = '11px system-ui, sans-serif';
  // 幅值网格
  ctx.textAlign = 'right';
  for (let g = gMin; g <= gMax; g += 5) {
    const y = yMag(g);
    ctx.strokeStyle = '#e8edf5';
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillStyle = '#8792a6';
    ctx.fillText(`${g}`, left - 8, y + 4);
  }
  // −3 dB 参考线
  if (gMin <= -3 && gMax >= -3) {
    ctx.strokeStyle = '#f59e0b';
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(left, yMag(-3)); ctx.lineTo(width - right, yMag(-3)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#b45309';
    ctx.textAlign = 'left';
    ctx.fillText('−3 dB', left + 4, yMag(-3) - 4);
  }
  // 相位网格
  ctx.textAlign = 'right';
  for (let p = pMin; p <= pMax; p += 45) {
    const y = yPhase(p);
    ctx.strokeStyle = '#eef2f8';
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillStyle = '#8792a6';
    ctx.fillText(`${p}°`, left - 8, y + 4);
  }
  // 频率刻度
  ctx.textAlign = 'center';
  for (let f = Math.ceil(fMin); f <= fMax; f += 1) {
    const x = xOf(10 ** f);
    ctx.fillStyle = '#8792a6';
    ctx.fillText(10 ** f >= 1000 ? `${10 ** f / 1000}k` : `${10 ** f}`, x, height - 14);
  }

  ctx.strokeStyle = '#1d4ed8';
  ctx.lineWidth = 1.9;
  ctx.beginPath();
  usable.forEach((p, i) => {
    const x = xOf(p.frequency_hz), y = yMag(p.gain_db);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  const withPhase = usable.filter((p) => Number.isFinite(p.phase_deg));
  if (withPhase.length) {
    ctx.strokeStyle = '#0f766e';
    ctx.beginPath();
    withPhase.forEach((p, i) => {
      const x = xOf(p.frequency_hz), y = yPhase(p.phase_deg);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  ctx.fillStyle = '#5b6577';
  ctx.textAlign = 'center';
  ctx.fillText('频率 / Hz（对数轴）', left + iw / 2, height - 1);
  ctx.textAlign = 'left';
  ctx.fillStyle = '#1d4ed8';
  ctx.fillText('幅值 dB', left + 6, top + 8);
  ctx.fillStyle = '#0f766e';
  ctx.fillText('相位 °', left + 70, top + 8);
  if (Number.isFinite(options.bandwidth)) {
    ctx.fillStyle = '#b45309';
    ctx.fillText(`−3 dB 带宽 ≈ ${options.bandwidth} Hz`, left + 150, top + 8);
  }
}

/** 俯视图：机器人位姿、路径与轨迹。 */
export function topView(canvas, rows, options = {}) {
  const { ctx, width, height } = prepare(canvas, options.height || 320);
  if (!rows || !rows.length) { emptyState(ctx, width, height, '运行路径实验后显示轨迹'); return; }
  const pad = 34;
  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  const consider = (x, y) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    xMin = Math.min(xMin, x); xMax = Math.max(xMax, x);
    yMin = Math.min(yMin, y); yMax = Math.max(yMax, y);
  };
  rows.forEach((row) => consider(row.x, row.y));
  for (const p of options.path || []) consider(p.x, p.y);
  if (!Number.isFinite(xMin)) { emptyState(ctx, width, height, '轨迹里没有位姿数据'); return; }
  const spanX = Math.max(xMax - xMin, 0.5), spanY = Math.max(yMax - yMin, 0.5);
  const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY);
  const cx = (xMin + xMax) / 2, cy = (yMin + yMax) / 2;
  const toPx = (x, y) => [width / 2 + (x - cx) * scale, height / 2 - (y - cy) * scale];

  ctx.font = '11px system-ui, sans-serif';
  ctx.fillStyle = '#94a3b8';
  ctx.textAlign = 'right';
  ctx.fillText('俯视图 · 单位 m', width - 10, height - 8);

  // 参考路径
  if (options.path && options.path.length) {
    ctx.strokeStyle = '#cbd5e1';
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 2;
    ctx.beginPath();
    options.path.forEach((p, i) => {
      const [px, py] = toPx(p.x, p.y);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }
  // 里程计轨迹
  if (options.showOdom) {
    ctx.strokeStyle = '#c2410c';
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    rows.forEach((row, i) => {
      const [px, py] = toPx(row.odom_x, row.odom_y);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }
  // 真实轨迹
  ctx.strokeStyle = '#1d4ed8';
  ctx.lineWidth = 2;
  ctx.beginPath();
  rows.forEach((row, i) => {
    const [px, py] = toPx(row.x, row.y);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  ctx.stroke();

  // 起始点与当前姿态
  const first = rows[0];
  const last = rows[rows.length - 1];
  const [fx, fy] = toPx(first.x, first.y);
  ctx.fillStyle = '#16a34a';
  ctx.beginPath(); ctx.arc(fx, fy, 4, 0, Math.PI * 2); ctx.fill();
  const [lx, ly] = toPx(last.x, last.y);
  arrow(ctx, lx, ly, last.theta || 0, scale, '#1d4ed8');

  ctx.fillStyle = '#64748b';
  ctx.textAlign = 'left';
  ctx.fillText('● 起点', 10, 16);
  if (options.showOdom) {
    ctx.fillStyle = '#c2410c';
    ctx.fillText('— 里程计', 10, 32);
  }
}

function arrow(ctx, x, y, theta, scale, color) {
  const size = Math.max(10, Math.min(22, scale * 0.25));
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(-theta);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(size * 0.6, 0);
  ctx.lineTo(-size * 0.5, size * 0.42);
  ctx.lineTo(-size * 0.2, 0);
  ctx.lineTo(-size * 0.5, -size * 0.42);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/** 柱状图（编码器量化台阶等）。 */
export function bars(canvas, items, options = {}) {
  const { ctx, width, height } = prepare(canvas, options.height || 240);
  const left = 68, right = 18, top = 26, bottom = 42;
  const iw = width - left - right, ih = height - top - bottom;
  if (!items || !items.length) { emptyState(ctx, width, height); return; }
  const max = Math.max(...items.map((item) => item.value || 0), 1e-9);
  const barW = iw / items.length * 0.6;
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i += 1) {
    const y = top + (ih * i) / 4;
    ctx.strokeStyle = '#e8edf5';
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillStyle = '#8792a6';
    ctx.fillText(niceNumber(max - (max * i) / 4), left - 8, y + 4);
  }
  items.forEach((item, index) => {
    const x = left + (iw * (index + 0.5)) / items.length - barW / 2;
    const h = (Math.max(item.value, 0) / max) * ih;
    ctx.fillStyle = '#1d4ed8';
    ctx.fillRect(x, top + ih - h, barW, h);
    ctx.fillStyle = '#475569';
    ctx.textAlign = 'center';
    ctx.fillText(item.label, x + barW / 2, top + ih + 15);
    ctx.fillStyle = '#1e293b';
    ctx.fillText(niceNumber(item.value), x + barW / 2, top + ih - h - 5);
  });
  ctx.fillStyle = '#5b6577';
  ctx.fillText(options.yLabel || '', left + iw / 2, 14);
}

/** 通用表格渲染（返回 HTML 字符串）。 */
export function table(headers, rows) {
  const head = headers.map((h) => `<th>${escapeHtml(h)}</th>`).join('');
  const body = rows.map((row) => `<tr>${row.map((cell) =>
    `<td>${cell === null || cell === undefined ? '—' : (typeof cell === 'number' ? niceNumber(cell) : escapeHtml(String(cell)))}</td>`).join('')}</tr>`).join('');
  return `<div class="table-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

export const COLORS = {
  blue: '#1d4ed8', orange: '#c2410c', teal: '#0f766e',
  purple: '#7c3aed', red: '#b91c1c', gray: '#64748b',
};

export function redrawAll(root) {
  root.querySelectorAll('canvas[data-redraw]').forEach((canvas) => {
    const fn = canvas.__redraw;
    if (typeof fn === 'function') fn();
  });
}

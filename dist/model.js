/* 仿真模型的几何与动画核心。
 *
 * 设计原则：
 *  1. **几何由物理参数生成**，不是画死的。改极对数、轮径、轮距、连杆长度，
 *     模型跟着变——否则"可交互"就是假的。
 *  2. **纯函数、无 DOM**：buildMotor/buildRobot 只返回顶点数据，
 *     渲染层（2D 正投影或软件 3D）各自去画。这样才能在 Node 里跑测试。
 *  3. **低配友好**：三角面数按需控制，2D 模式完全不用投影计算。
 *
 * 单位：模型内部用毫米（mm），与真实电机尺寸同量级，便于标注。
 */

export const TAU = Math.PI * 2;

/* ------------------------------------------------------------------ 数学 */

export function normalize3(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}

export function cross3(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

export function dot3(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

export function rotateX(point, angle) {
  const c = Math.cos(angle), s = Math.sin(angle);
  return [point[0], point[1] * c - point[2] * s, point[1] * s + point[2] * c];
}

export function rotateY(point, angle) {
  const c = Math.cos(angle), s = Math.sin(angle);
  return [point[0] * c + point[2] * s, point[1], -point[0] * s + point[2] * c];
}

export function rotateZ(point, angle) {
  const c = Math.cos(angle), s = Math.sin(angle);
  return [point[0] * c - point[1] * s, point[0] * s + point[1] * c, point[2]];
}

/** 绕任意轴旋转（Rodrigues 公式）。 */
export function rotateAxis(point, axis, angle) {
  const k = normalize3(axis);
  const c = Math.cos(angle), s = Math.sin(angle);
  const kp = cross3(k, point);
  const kd = dot3(k, point);
  return [
    point[0] * c + kp[0] * s + k[0] * kd * (1 - c),
    point[1] * c + kp[1] * s + k[1] * kd * (1 - c),
    point[2] * c + kp[2] * s + k[2] * kd * (1 - c),
  ];
}

/* ------------------------------------------------------------------ 网格 */

/** 生成圆柱侧面 + 两端盖。axis 为 'x' | 'y' | 'z'。 */
export function cylinderMesh({ radius = 10, height = 20, segments = 16, axis = 'z', caps = true } = {}) {
  const vertices = [];
  const faces = [];
  const half = height / 2;
  const place = (u, v, w) => {
    if (axis === 'z') return [u, v, w];
    if (axis === 'y') return [u, w, v];
    return [w, u, v];
  };
  const ring = [];
  for (let i = 0; i < segments; i += 1) {
    const a = (i / segments) * TAU;
    const x = Math.cos(a) * radius, y = Math.sin(a) * radius;
    const base = vertices.length;
    vertices.push(place(x, y, -half), place(x, y, half));
    ring.push(base);
  }
  for (let i = 0; i < segments; i += 1) {
    const a = ring[i], b = ring[(i + 1) % segments];
    faces.push([a, b, b + 1], [a, b + 1, a + 1]);
  }
  if (caps) {
    const bottom = vertices.length;
    vertices.push(place(0, 0, -half));
    const top = vertices.length;
    vertices.push(place(0, 0, half));
    for (let i = 0; i < segments; i += 1) {
      const a = ring[i], b = ring[(i + 1) % segments];
      faces.push([bottom, b, a], [top, a + 1, b + 1]);
    }
  }
  return { vertices, faces };
}

/** 长方体（以原点为中心）。 */
export function boxMesh({ size = [10, 10, 10] } = {}) {
  const [sx, sy, sz] = size.map((v) => v / 2);
  const vertices = [
    [-sx, -sy, -sz], [sx, -sy, -sz], [sx, sy, -sz], [-sx, sy, -sz],
    [-sx, -sy, sz], [sx, -sy, sz], [sx, sy, sz], [-sx, sy, sz],
  ];
  const faces = [
    [0, 3, 2], [0, 2, 1],      // -z
    [4, 5, 6], [4, 6, 7],      // +z
    [0, 1, 5], [0, 5, 4],      // -y
    [3, 7, 6], [3, 6, 2],      // +y
    [0, 4, 7], [0, 7, 3],      // -x
    [1, 2, 6], [1, 6, 5],      // +x
  ];
  return { vertices, faces };
}

/** 由四边形条带生成"板"（用于磁钢、标签面）。 */
export function plateMesh(corners) {
  return { vertices: corners.map((c) => [...c]), faces: [[0, 1, 2], [0, 2, 3]] };
}

/** 合并多个网格，可选对每个子网格做平移/旋转/缩放。 */
export function mergeParts(parts) {
  const vertices = [];
  const faces = [];
  for (const part of parts) {
    if (!part || !part.mesh) continue;
    const offset = vertices.length;
    const { vertices: vs, faces: fs } = part.mesh;
    const transform = part.transform || ((p) => p);
    for (const v of vs) vertices.push(transform(v));
    for (const f of fs) faces.push(f.map((i) => i + offset));
  }
  return { vertices, faces };
}

/* ------------------------------------------------------------------ 电机模型 */

/**
 * 生成一台电机的可视化模型。
 *
 * 支持两种构型，按极对数自动选择外观：
 *  - `pmsm_bldc`：外定子 + 内转子，永磁体贴在转子表面（体表贴式常见构型）；
 *  - `induction`：外定子 + 笼型转子（端环 + 导条）；
 *  - `dc`：定子磁极 + 电枢 + 换向器 + 电刷（有刷机的经典结构）。
 *
 * 返回的 `parts` 里每一项都带 `name` 与中文 `label`，
 * 供"部件高亮 + 图注"使用；`animatable` 标记哪些部件随转子转动。
 */
export function buildMotor(options = {}) {
  const {
    kind = 'pmsm_bldc',
    polePairs = 4,
    housingRadius = 32,       // 外壳（画成线框轮廓，不遮挡内部）
    statorOuter = 28,         // 定子铁芯外径
    statorInner = 19,         // 定子内径（齿顶）
    rotorRadius = 17.5,       // 转子半径
    shaftRadius = 4.5,
    stackLength = 44,         // 铁芯叠厚
    shaftLength = 96,         // 轴总长
    segments = 20,
  } = options;

  // 真实电机里定转子之间的气隙只有 0.5–2 mm。几何必须反映这一点：
  // 气隙画大了，学员会以为"磁场要跨过一大段空气"，那是错的物理图像。
  const airGap = statorInner - rotorRadius;

  const parts = [];
  const rotorParts = [];

  // 外壳：只画轮廓（wireframe），否则实心圆柱会把里面全挡住
  parts.push({
    name: 'housing', label: '机壳（线框示意）', color: '#64748b',
    wireframe: true, opacity: 1,
    // 轮廓只用少量线条：细分太密会变成"鸟笼"，反而看不清里面的定转子
    mesh: { vertices: [], faces: [] },
    rings: [{ radius: housingRadius, height: stackLength + 10, segments: 24,
              meridians: 4 }],
  });

  // 定子铁芯：只画侧壁、不画端盖。盖上端盖会把转子和磁钢全挡住，
  // 从俯视就只剩一个大圆盘——那样嵌套关系（磁钢→气隙→定子→绕组）就看不见了。
  parts.push({
    name: 'stator-core', label: '定子铁芯', color: '#7c8798', opacity: 1,
    mesh: cylinderMesh({ radius: (statorOuter + statorInner) / 2, height: stackLength,
                         segments, axis: 'z', caps: false }),
  });
  const slotCount = Math.max(6, polePairs * 3);
  for (let i = 0; i < slotCount; i += 1) {
    const angle = (i / slotCount) * TAU;
    const radius = (statorOuter + statorInner) / 2 + 1.5;
    parts.push({
      name: `coil-${i}`, label: `定子绕组 #${i + 1}`,
      // 三相着色：学员一眼看出"哪一相在出力"
      color: ['#2563eb', '#ea580c', '#0d9488'][i % 3],
      opacity: 0.95,
      phase: i % 3,
      // 绕组不能高于铁芯：高了就会在俯视时把转子整个盖住
      // （早先版本用了 stackLength+4，从上往下只能看到一个彩色圆盘）。
      mesh: boxMesh({ size: [6.5, 6.5, stackLength - 8] }),
      transform: (p) => rotateZ([p[0] + radius, p[1], p[2]], angle),
    });
  }

  // 转子铁芯
  rotorParts.push({
    name: 'rotor', label: '转子铁芯', color: '#e2e8f0',
    mesh: cylinderMesh({ radius: rotorRadius, height: stackLength, segments, axis: 'z' }),
  });

  if (kind === 'induction') {
    const bars = Math.max(10, polePairs * 5);
    for (let i = 0; i < bars; i += 1) {
      const angle = (i / bars) * TAU;
      rotorParts.push({
        name: `bar-${i}`, label: `转子导条 #${i + 1}`, color: '#b45309', opacity: 1,
        mesh: cylinderMesh({ radius: 1.6, height: stackLength + 2, segments: 6, axis: 'z' }),
        transform: (p) => rotateZ([p[0] + rotorRadius - 1.6, p[1], p[2]], angle),
      });
    }
  } else if (kind === 'dc') {
    // 有刷直流机：定子磁极在铁芯内侧，电枢在中间
    for (let i = 0; i < 2; i += 1) {
      const angle = i * Math.PI;
      parts.push({
        name: `pole-${i}`, label: i === 0 ? 'N 极磁钢' : 'S 极磁钢',
        color: i === 0 ? '#dc2626' : '#2563eb', opacity: 1,
        mesh: boxMesh({ size: [6, 22, stackLength] }),
        transform: (p) => rotateZ([p[0] + statorInner - 3, p[1], p[2]], angle),
      });
    }
    rotorParts.push({
      name: 'commutator', label: '换向器', color: '#a16207',
      mesh: cylinderMesh({ radius: shaftRadius + 6, height: 13, segments: 12, axis: 'z' }),
      transform: (p) => [p[0], p[1], p[2] - stackLength / 2 - 9],
    });
  } else {
    // 永磁同步 / 无刷：磁钢贴在转子表面，极数 = 2 × 极对数
    const magnets = Math.max(2, polePairs * 2);
    for (let i = 0; i < magnets; i += 1) {
      const angle = (i / magnets) * TAU;
      const north = i % 2 === 0;
      rotorParts.push({
        name: `magnet-${i}`,
        label: north ? `N 极磁钢 #${i / 2 + 1}` : `S 极磁钢 #${(i - 1) / 2 + 1}`,
        color: north ? '#dc2626' : '#2563eb', opacity: 1,
        // 磁钢略高于铁芯（真实表贴磁钢就是这样）。若与铁芯齐平，
        // 俯视时会被转子的端盖完全挡住，看不到 N/S 极分布。
        // 尺寸与位置都刻意做"厚"一点：贴合转子表面并把顶面明显抬出铁芯，
        // 这样俯视时能直接读出 N/S 极的分布（这是讲极对数时最该看到的画面）。
        mesh: boxMesh({ size: [4.2, 12.5, stackLength + 10] }),
        transform: (p) => rotateZ([p[0] + rotorRadius - 1.0, p[1], p[2]], angle),
      });
    }
  }

  // 转轴：伸出较长，取景时不把它算进去，否则本体被挤小
  rotorParts.push({
    name: 'shaft', label: '转轴', color: '#475569', excludeFromFit: true,
    mesh: cylinderMesh({ radius: shaftRadius, height: shaftLength, segments: 14, axis: 'z' }),
  });

  // 编码器码盘：让"反馈从哪来"看得见。
  // 早期版本把它放在转轴轴线上、半径又比轴大很多，从俯视正好挡住整个转子——
  // 所以按真实电机的常见做法偏置到轴侧，并且不参与自动取景。
  rotorParts.push({
    name: 'encoder', label: '编码器码盘', color: '#0f766e',
    excludeFromFit: true,
    mesh: cylinderMesh({ radius: 9, height: 2.5, segments: 20, axis: 'z', caps: true }),
    transform: (p) => [p[0] + shaftRadius + 10, p[1], p[2] - stackLength / 2 - 16],
  });

  return {
    kind, polePairs, slotCount, airGap,
    units: 'mm',
    // 电机轴沿 z，需要较高的俯角才能同时看到端面与侧面立体感
    camera: { yaw: -2.10, pitch: 0.62 },
    bounds: { radius: housingRadius, length: shaftLength },
    parts, rotorParts,
    labels: [
      { name: 'coil-0', text: '定子绕组：三相电流在这里产生旋转磁场', at: [0, housingRadius, stackLength * 0.2] },
      { name: 'rotor', text: '转子：被磁场拖动，输出转矩', at: [0, -rotorRadius - 6, 0] },
      { name: 'shaft', text: '转轴：输出到减速器', at: [0, 0, shaftLength / 2 + 10] },
      { name: 'encoder', text: '编码器：反馈转速与位置',
        at: [shaftRadius + 10, 0, -stackLength / 2 - 16] },
    ],
  };
}

function slotsRotor(polePairs) {
  return Math.max(8, polePairs * 4);
}

/* ------------------------------------------------------------------ 机器人模型 */

/**
 * 生成一台 ROBOCON 差速底盘 + 四连杆取球机构的可视化模型。
 *
 * 几何**从真实参数算出**：轮距、轮半径、车体尺寸、连杆长度、曲柄长度。
 * 改这些参数，模型立刻变形——这正是"设计"该有的反馈。
 */
export function buildRobot(options = {}) {
  const {
    track = 400,          // mm
    wheelRadius = 62.5,   // mm
    wheelWidth = 45,      // mm
    bodyLength = 520,
    bodyWidth = 340,
    bodyHeight = 120,
    wheelbase = 300,
    linkCrank = 40,
    linkCoupler = 120,
    linkRocker = 100,
    linkGround = 110,
  } = options;

  const parts = [];
  const wheelParts = [];

  // 所有高度都以"轮子着地"为基准（地面 z = 0），这样改轮径时整车比例跟着变。
  const deck = wheelRadius;                      // 底盘底面高度
  const bodyMid = deck + bodyHeight / 2;         // 车体中心
  const deckTop = deck + bodyHeight;             // 车体顶面

  // 车体
  parts.push({
    name: 'body', label: '车体', color: '#1e3a8a', opacity: 0.92,
    mesh: boxMesh({ size: [bodyWidth, bodyLength, bodyHeight] }),
    transform: (p) => [p[0], p[1], p[2] + bodyMid],
  });

  // 控制板（标出"电控在哪里"）
  parts.push({
    name: 'mcu', label: '主控板', color: '#065f46', opacity: 0.95,
    mesh: boxMesh({ size: [120, 90, 10] }),
    transform: (p) => [p[0] - 60, p[1] + 60, p[2] + deckTop + 6],
  });
  parts.push({
    name: 'driver', label: '驱动器 ×2', color: '#7c2d12', opacity: 0.95,
    mesh: boxMesh({ size: [70, 70, 14] }),
    transform: (p) => [p[0] + 90, p[1] - 20, p[2] + deckTop + 8],
  });
  parts.push({
    name: 'battery', label: '电池', color: '#374151', opacity: 0.95,
    mesh: boxMesh({ size: [150, 200, 70] }),
    transform: (p) => [p[0], p[1] - 130, p[2] + bodyMid],
  });

  // 四个车轮（差速底盘通常两轮驱动 + 从动轮；这里按 4 轮展示，
  // 左右两侧**独立转动**，才能表现"差速")
  const halfTrack = track / 2;
  const halfBase = wheelbase / 2;
  const wheelPositions = [
    { name: 'fl', label: '左前轮', x: -halfTrack, y: halfBase, side: 'left' },
    { name: 'rl', label: '左后轮', x: -halfTrack, y: -halfBase, side: 'left' },
    { name: 'fr', label: '右前轮', x: halfTrack, y: halfBase, side: 'right' },
    { name: 'rr', label: '右后轮', x: halfTrack, y: -halfBase, side: 'right' },
  ];
  for (const wheel of wheelPositions) {
    wheelParts.push({
      name: `wheel-${wheel.name}`, label: wheel.label, color: '#0f172a', opacity: 0.95,
      side: wheel.side,
      mesh: cylinderMesh({ radius: wheelRadius, height: wheelWidth, segments: 18, axis: 'x' }),
      transform: (p) => [p[0] + wheel.x, p[1] + wheel.y, p[2] + wheelRadius],
    });
    // 轮辐：用来"看得见轮子在转"
    for (let i = 0; i < 4; i += 1) {
      wheelParts.push({
        name: `spoke-${wheel.name}-${i}`, label: '', spoke: true, side: wheel.side,
        color: '#f59e0b', opacity: 0.95,
        mesh: boxMesh({ size: [wheelWidth + 2, 6, wheelRadius * 1.5] }),
        transform: (p) => [p[0] + wheel.x, p[1] + wheel.y, p[2] + wheelRadius],
      });
    }
    // 电机本体
    parts.push({
      name: `motor-${wheel.name}`, label: `${wheel.label}电机`, color: '#475569',
      mesh: cylinderMesh({ radius: 30, height: 62, segments: 12, axis: 'x' }),
      transform: (p) => [p[0] + wheel.x * 0.72, p[1] + wheel.y, p[2] + wheelRadius],
    });
  }

  // 四连杆取球机构（装在车体前端）
  const mechanism = {
    ground: linkGround,
    crank: linkCrank,
    coupler: linkCoupler,
    rocker: linkRocker,
    origin: [0, bodyLength / 2 - 20, deckTop + 30],
  };
  parts.push({
    name: 'mechanism-base', label: '机构机架', color: '#4c1d95', opacity: 0.95,
    mesh: boxMesh({ size: [linkGround + 20, 40, 20] }),
    transform: (p) => [p[0], p[1] + mechanism.origin[1], p[2] + mechanism.origin[2]],
  });
  parts.push({
    name: 'arm-motor', label: '机构电机', color: '#111827',
    mesh: cylinderMesh({ radius: 26, height: 54, segments: 12, axis: 'x' }),
    transform: (p) => [p[0] - 60, p[1] + mechanism.origin[1], p[2] + mechanism.origin[2]],
  });

  return {
    units: 'mm',
    // 机器人要看清"底盘坐在四个轮子上"，俯角必须低一些，
    // 否则车体会把轮子挡住（电机正相反：需要高俯角看端面）。
    camera: { yaw: -2.35, pitch: 0.34 },
    track, wheelRadius, wheelbase,
    bounds: { length: bodyLength, width: bodyWidth, height: deckTop + 120,
              wheelRadius, deck, bodyMid, deckTop },
    parts, wheelParts, mechanism,
    labels: [
      { name: 'body', text: '车体', at: [0, 0, deckTop] },
      { name: 'driver', text: '驱动器：把控制量变成三相电压', at: [90, -20, deckTop + 20] },
      { name: 'battery', text: '电池：峰值电流决定线径与保险', at: [0, -130, deckTop + 50] },
      { name: 'wheel-fl', text: '左右轮独立驱动 → 可以原地转向', at: [-halfTrack, halfBase, wheelRadius + 70] },
    ],
  };
}

/** 四连杆位置解，单位与输入一致。解不出返回 null。 */
export function fourBarSolution(crankAngle, { crank = 40, coupler = 120, rocker = 100, ground = 110 } = {}) {
  const bx = crank * Math.cos(crankAngle);
  const by = crank * Math.sin(crankAngle);
  const intersections = circleIntersections(bx, by, coupler, ground, 0, rocker);
  if (!intersections.length) return null;
  let best = intersections[0];
  for (const candidate of intersections) if (candidate[1] > best[1]) best = candidate;
  return {
    crank: [bx, by],
    coupler: best,
    ground: [ground, 0],
    rockerAngle: Math.atan2(best[1], best[0] - ground),
  };
}

function circleIntersections(x1, y1, r1, x2, y2, r2) {
  const dx = x2 - x1, dy = y2 - y1;
  const d = Math.hypot(dx, dy);
  if (d < 1e-9 || d > r1 + r2 || d < Math.abs(r1 - r2)) return [];
  const a = (r1 * r1 - r2 * r2 + d * d) / (2 * d);
  const hSq = r1 * r1 - a * a;
  const h = hSq > 0 ? Math.sqrt(hSq) : 0;
  const mx = x1 + (a * dx) / d, my = y1 + (a * dy) / d;
  if (h === 0) return [[mx, my]];
  const ox = (-dy / d) * h, oy = (dx / d) * h;
  return [[mx + ox, my + oy], [mx - ox, my - oy]];
}

/* ------------------------------------------------------------------ 仿真数据 → 模型姿态 */

/**
 * 把一行仿真数据翻译成模型要显示的角度。
 *
 * 这里做了单位换算与**可视化缩放**：
 *  - 电机转角为了看得清会减速显示（`spinScale`），但界面会标注"显示已减速"；
 *  - 轮转角按真实比例（轮径固定），不做缩放，因为"走了多远"本身要可信。
 */
export function poseFromRow(row, options = {}) {
  const { wheelRadius = 62.5, spinScale = 0.12, kind = 'pmsm_bldc', polePairs = 4 } = options;
  const rpm = Number.isFinite(row?.rpm) ? row.rpm : 0;
  const omega = (rpm / 60) * TAU;                       // rad/s
  const shaftAngle = (Number.isFinite(row?.shaftTurns) ? row.shaftTurns : 0) * TAU;
  const leftRpm = Number.isFinite(row?.left_rpm) ? row.left_rpm : 0;
  const rightRpm = Number.isFinite(row?.right_rpm) ? row.right_rpm : 0;
  return {
    shaftAngle: shaftAngle || (row?.electricalAngle ?? 0) / Math.max(1, polePairs),
    displayedSpin: shaftAngle * spinScale,
    omega,
    leftAngle: (leftRpm / 60) * TAU,
    rightAngle: (rightRpm / 60) * TAU,
    x: Number.isFinite(row?.x) ? row.x * 1000 : 0,      // m → mm
    y: Number.isFinite(row?.y) ? row.y * 1000 : 0,
    heading: Number.isFinite(row?.theta) ? row.theta : 0,
    torque: Number.isFinite(row?.torque) ? row.torque : 0,
    current: Number.isFinite(row?.current) ? row.current : 0,
    wheelRadius,
  };
}

/** 从轨迹累积真实轴转角与轮转角（仿真只给转速，这里做积分）。 */
export function accumulateAngles(rows, options = {}) {
  const { polePairs = 4, wheelRadius = 62.5 } = options;
  let shaft = 0, left = 0, right = 0;
  let previous = rows.length ? rows[0].t ?? 0 : 0;
  return rows.map((row) => {
    const t = Number.isFinite(row.t) ? row.t : previous;
    const dt = Math.max(0, t - previous);
    previous = t;
    // rpm 是机械转速，所以转角就是 ∫(rpm/60)·2π dt。
    // 极对数只用于电角度（换相、磁场），不属于机械转角——
    // 多乘 p 会让模型看起来比真实转得快 p 倍，这是可视化里最容易骗人的错误。
    const omega = (Number.isFinite(row.rpm) ? row.rpm : 0) / 60 * TAU;
    shaft += omega * dt;
    left += (Number.isFinite(row.left_rpm) ? row.left_rpm : 0) / 60 * TAU * dt;
    right += (Number.isFinite(row.right_rpm) ? row.right_rpm : 0) / 60 * TAU * dt;
    return { ...row, shaftTurns: shaft / TAU, leftTurns: left / TAU, rightTurns: right / TAU,
             leftAngleTotal: left, rightAngleTotal: right, wheelRadius };
  });
}

/* ------------------------------------------------------------------ 统计 */

export function meshStats(mesh) {
  let min = [Infinity, Infinity, Infinity];
  let max = [-Infinity, -Infinity, -Infinity];
  for (const v of mesh.vertices) {
    for (let i = 0; i < 3; i += 1) {
      if (v[i] < min[i]) min[i] = v[i];
      if (v[i] > max[i]) max[i] = v[i];
    }
  }
  const finite = mesh.vertices.every((v) => v.every((n) => Number.isFinite(n)));
  return { vertices: mesh.vertices.length, faces: mesh.faces.length, min, max, finite };
}

/**
 * 计算"用于自动取景"的包围盒：剔掉转轴、编码器这类细长或偏置件。
 *
 * 用全体几何取景会让主体被挤小（细长件把包围球撑大）。
 * 取景只关心"想让学员看清的那部分"，渲染仍然是完整的。
 */
export function fitBounds(model) {
  return flattenModel(model, { forFit: true });
}

export function allParts(model) {
  return [...(model.parts || []), ...(model.rotorParts || []), ...(model.wheelParts || [])];
}

/**
 * 汇总整个模型的规模与包围盒。
 *
 * 注意：这里用的是**部件变换之后**的顶点。部件的 `transform` 会把几何
 * 挪到正确位置，不带上它算出来的包围盒会小得离谱——模型的尺寸标注
 * 与自动取景都依赖这个结果。
 */
export function flattenModel(model, options = {}) {
  const parts = options.forFit
    ? allParts(model).filter((part) => !part.excludeFromFit)
    : allParts(model);
  const stats = { vertices: 0, faces: 0, finite: true,
                  min: [Infinity, Infinity, Infinity],
                  max: [-Infinity, -Infinity, -Infinity] };
  for (const part of parts) {
    if (!part.mesh || !part.mesh.vertices.length) continue;
    stats.vertices += part.mesh.vertices.length;
    stats.faces += part.mesh.faces.length;
    for (const vertex of part.mesh.vertices) {
      const p = part.transform ? part.transform(vertex) : vertex;
      for (let i = 0; i < 3; i += 1) {
        if (!Number.isFinite(p[i])) stats.finite = false;
        if (p[i] < stats.min[i]) stats.min[i] = p[i];
        if (p[i] > stats.max[i]) stats.max[i] = p[i];
      }
    }
  }
  for (let i = 0; i < 3; i += 1) {
    if (!Number.isFinite(stats.min[i])) { stats.min[i] = 0; stats.max[i] = 0; }
  }
  stats.size = [stats.max[0] - stats.min[0], stats.max[1] - stats.min[1], stats.max[2] - stats.min[2]];
  stats.centre = [(stats.min[0] + stats.max[0]) / 2, (stats.min[1] + stats.max[1]) / 2,
                  (stats.min[2] + stats.max[2]) / 2];
  return stats;
}

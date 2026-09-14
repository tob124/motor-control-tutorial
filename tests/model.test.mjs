/* 几何与视图层的 Node 测试。
 *
 * 前端没有构建步骤，也没有浏览器测试框架，所以这里用 Node 直接跑纯函数部分。
 * 覆盖的都是"错了就会让画面静止或错位"的地方：
 *  - 几何是否有限、是否随物理参数变化（不是画死的）；
 *  - 四连杆解是否满足装配约束（和 Python 侧同一套判据）；
 *  - 轨迹角度积分是否保守（转速为零时角度不应漂移）；
 *  - 3D 投影是否把点投到合理位置、相机交互是否单调。
 *
 * 运行：node tests/model.test.mjs
 */

import {
  buildMotor, buildRobot, fourBarSolution, cylinderMesh, boxMesh,
  rotateZ, rotateX, meshStats, flattenModel, accumulateAngles, poseFromRow, TAU,
} from '../dist/model.js';

let passed = 0;
const failures = [];

function check(name, condition, detail = '') {
  if (condition) { passed += 1; return; }
  failures.push(`${name}${detail ? ' — ' + detail : ''}`);
}

function approx(a, b, tolerance = 1e-6) {
  return Math.abs(a - b) <= tolerance;
}

/* ---------------------------------------------------------------- 网格基础 */

const cylinder = cylinderMesh({ radius: 10, height: 20, segments: 12, axis: 'z' });
const cylinderStats = meshStats(cylinder);
check('圆柱顶点数正确', cylinderStats.vertices === 12 * 2 + 2,
  `实际 ${cylinderStats.vertices}`);
check('圆柱面数正确', cylinderStats.faces === 12 * 4, `实际 ${cylinderStats.faces}`);
check('圆柱坐标有限', cylinderStats.finite);
check('圆柱半径正确', approx(Math.max(...cylinder.vertices.map((v) => Math.hypot(v[0], v[1]))), 10, 1e-9));
check('圆柱轴向长度正确',
  approx(Math.max(...cylinder.vertices.map((v) => v[2])) - Math.min(...cylinder.vertices.map((v) => v[2])), 20, 1e-9));

const box = boxMesh({ size: [10, 20, 30] });
check('长方体顶点与面数', box.vertices.length === 8 && box.faces.length === 12);

// 绕 z 轴旋转保持半径
const rotated = rotateZ([3, 4, 5], Math.PI / 2);
check('rotateZ 保半径', approx(Math.hypot(rotated[0], rotated[1]), 5, 1e-12));
check('rotateZ 不改 z', approx(rotated[2], 5, 1e-12));

// 绕 x 轴转 90°：+z 应转到 −y（右手系）
const spun = rotateX([0, 0, 5], Math.PI / 2);
check('rotateX 绕 x 轴', approx(spun[2], 0, 1e-12) && approx(spun[1], -5, 1e-12),
  `得到 [${spun.map((v) => v.toFixed(3))}]`);

/* ---------------------------------------------------------------- 电机几何 */

const bldc = buildMotor({ kind: 'pmsm_bldc', polePairs: 4 });
const bldcStats = flattenModel(bldc);
check('无刷机构建成功', bldc.parts.length > 0 && bldc.rotorParts.length > 0);
check('无刷机坐标有限', bldcStats.finite);
check('无刷机面数在低配预算内', bldcStats.faces < 1500, `实际 ${bldcStats.faces}`);

const magnets = bldc.rotorParts.filter((p) => p.name.startsWith('magnet-'));
check('磁极数 = 2 × 极对数', magnets.length === 8, `实际 ${magnets.length}`);
const norths = magnets.filter((p) => p.color === '#dc2626');
check('N/S 交替且数量相等', norths.length === magnets.length - norths.length);
check('每个部件都有中文标注', bldc.parts.every((p) => typeof p.label === 'string' && p.label.length > 0));
check('有定位图注', Array.isArray(bldc.labels) && bldc.labels.length >= 3);

// 几何必须随参数变化——否则"可交互"是假的
const bigPoles = buildMotor({ kind: 'pmsm_bldc', polePairs: 8 });
check('极对数改变磁极数量', bigPoles.rotorParts.filter((p) => p.name.startsWith('magnet-')).length === 16);
// 机壳是线框（网格为空），它的尺寸不体现在 flattenModel 里，
// 所以用定子外径验证"几何由参数驱动"。
const bigger = buildMotor({ kind: 'pmsm_bldc', polePairs: 4, statorOuter: 60, statorInner: 50,
                            rotorRadius: 48, housingRadius: 70 });
check('定子尺寸改变几何',
  flattenModel(bigger).max[0] > flattenModel(bldc).max[0] + 20,
  `小=${flattenModel(bldc).max[0].toFixed(1)} 大=${flattenModel(bigger).max[0].toFixed(1)}`);
check('机壳线框以轮廓形式存在', bldc.parts.some((p) => p.wireframe && (p.rings || []).length));
check('气隙在正常范围内（不是夸张的大缝隙）', bldc.airGap > 0.5 && bldc.airGap < 3,
  `airGap=${bldc.airGap}`);
check('磁钢数 = 2 × 极对数', bldc.rotorParts.filter((p) => p.name.startsWith('magnet-')).length === 8);
check('模型带默认视角', Number.isFinite(bldc.camera?.pitch) && Number.isFinite(bldc.camera?.yaw));

const induction = buildMotor({ kind: 'induction', polePairs: 2 });
check('异步机有导条', induction.rotorParts.some((p) => p.name.startsWith('bar-')));
check('异步机坐标有限', flattenModel(induction).finite);

const dcMotor = buildMotor({ kind: 'dc' });
check('直流机有磁极与换向器',
  dcMotor.parts.some((p) => p.name.startsWith('pole-'))
  && dcMotor.rotorParts.some((p) => p.name === 'commutator'));

/* ---------------------------------------------------------------- 机器人几何 */

const robot = buildRobot({ track: 400, wheelRadius: 62.5, wheelbase: 300 });
const robotStats = flattenModel(robot);
check('机器人生成成功', robot.parts.length > 0 && robot.wheelParts.length > 0);
check('机器人坐标有限', robotStats.finite);
check('机器人面数在预算内', robotStats.faces < 2600, `实际 ${robotStats.faces}`);
check('四个车轮存在', robot.wheelParts.filter((p) => p.name.startsWith('wheel-')).length === 4);
check('轮距参数生效', approx(robot.track, 400));
check('轮径改变整车高度',
  flattenModel(buildRobot({ wheelRadius: 120 })).max[2] > robotStats.max[2] + 40);
check('底盘坐落在轮子上（不是悬空）', robot.bounds.deck === 62.5 && robot.bounds.deckTop > 62.5);
const bigWheel = buildRobot({ wheelRadius: 120 });
check('改轮径后底盘跟着抬高', bigWheel.bounds.deck === 120);

const dxRobot = buildRobot({ track: 600 });
check('轮距改变宽度',
  flattenModel(dxRobot).max[0] > flattenModel(robot).max[0] + 90);

/* ---------------------------------------------------------------- 四连杆 */

let solved = 0;
for (let i = 0; i < 72; i += 1) {
  const angle = (i / 72) * TAU;
  const solution = fourBarSolution(angle, { crank: 40, coupler: 120, rocker: 100, ground: 110 });
  if (!solution) continue;
  solved += 1;
  const { crank, coupler, ground } = solution;
  const bc = Math.hypot(coupler[0] - crank[0], coupler[1] - crank[1]);
  const cd = Math.hypot(coupler[0] - ground[0], coupler[1] - ground[1]);
  const ab = Math.hypot(crank[0], crank[1]);
  check(`连杆约束 θ=${angle.toFixed(2)}`, approx(bc, 120, 1e-9), `|BC|=${bc}`);
  check(`摇杆约束 θ=${angle.toFixed(2)}`, approx(cd, 100, 1e-9), `|CD|=${cd}`);
  check(`曲柄约束 θ=${angle.toFixed(2)}`, approx(ab, 40, 1e-9), `|AB|=${ab}`);
}
check('至少有一半角度可装配', solved >= 36, `实际 ${solved}`);

const impossible = fourBarSolution(0, { crank: 40, coupler: 10, rocker: 10, ground: 500 });
check('不可装配时返回 null', impossible === null);

/* ---------------------------------------------------------------- 轨迹积分 */

const rows = [
  { t: 0.0, rpm: 600, left_rpm: 100, right_rpm: 100 },
  { t: 0.1, rpm: 600, left_rpm: 100, right_rpm: 100 },
  { t: 0.2, rpm: 600, left_rpm: 100, right_rpm: 100 },
];
const accumulated = accumulateAngles(rows);
const expectedTurns = 600 / 60 * 0.2;      // rpm → 转/秒 → 0.2 s 内的圈数
check('轴转角积分正确', approx(accumulated[2].shaftTurns, expectedTurns, 1e-9),
  `实际 ${accumulated[2].shaftTurns}，期望 ${expectedTurns}`);
check('轮转角积分正确', approx(accumulated[2].leftTurns, (100 / 60) * 0.2, 1e-9));

const idle = accumulateAngles([{ t: 0, rpm: 0 }, { t: 1, rpm: 0 }, { t: 2, rpm: 0 }]);
check('零转速不漂移', approx(idle[2].shaftTurns, 0, 1e-12));

const missing = accumulateAngles([{ t: 0 }, { t: 1 }, { t: 2 }]);
check('缺失转速字段不产生 NaN', missing.every((r) => Number.isFinite(r.shaftTurns)));

/* ---------------------------------------------------------------- 姿态换算 */

const pose = poseFromRow({ rpm: 600, torque: 0.12, current: 2.4, x: 0.5, y: -0.25, theta: 0.3 }, {});
check('位置由 m 换算为 mm', approx(pose.x, 500) && approx(pose.y, -250));
check('转矩电流透传', approx(pose.torque, 0.12) && approx(pose.current, 2.4));
check('朝向透传', approx(pose.heading, 0.3));
check('缺失字段安全', Number.isFinite(poseFromRow({}, {}).omega));

/* ---------------------------------------------------------------- 相机 */

const { OrbitCamera, fitCamera } = await import('../dist/render3d.js');
const camera = new OrbitCamera({ distance: 300, quality: 1 });
const centre = camera.project([0, 0, 0], 600, 400);
check('原点投影到画面中心', approx(centre.x, 300, 1e-9) && approx(centre.y, 200, 1e-9));
check('相机前方有正深度', centre.depth > 0);

const far = camera.project([0, 0, -500], 600, 400);
check('远处点更靠近画面中心', Math.abs(far.x - 300) < Math.abs(centre.x - 300) + 1e-6);

const beforeYaw = camera.yaw;
camera.orbit(40, 0);
check('拖拽改变偏航', camera.yaw !== beforeYaw);

const beforeZoom = camera.distance;
camera.zoom(0.5);
check('缩放减小距离', camera.distance < beforeZoom);
// 限幅来自 zoomLimits（由 fitCamera 设置），这里显式给一组验证边界行为
camera.zoomLimits = [60, 2400];
camera.zoom(1e-6);
check('缩放有下限', camera.distance >= 60, `距离=${camera.distance}`);
camera.zoom(1e9);
check('缩放有上限', camera.distance <= 2400, `距离=${camera.distance}`);

const beforePitch = camera.pitch;
camera.orbit(0, 10000);
check('俯仰被限幅', Math.abs(camera.pitch) <= 1.35 + 1e-9, `pitch=${camera.pitch}`);

/* ---------------------------------------------------------------- 相机构造防御 */

const guarded = new OrbitCamera({ yaw: undefined, pitch: undefined, distance: undefined });
check('undefined 不覆盖默认偏航', Number.isFinite(guarded.yaw), `yaw=${guarded.yaw}`);
check('undefined 不覆盖默认俯仰', Number.isFinite(guarded.pitch), `pitch=${guarded.pitch}`);
check('undefined 不覆盖默认距离', Number.isFinite(guarded.distance), `distance=${guarded.distance}`);
const nanGuard = new OrbitCamera({ yaw: NaN, pitch: NaN });
check('NaN 不覆盖默认值', Number.isFinite(nanGuard.yaw) && Number.isFinite(nanGuard.pitch));
const projection = guarded.project([0, 0, 0], 600, 400);
check('受防护的相机投影有限', Number.isFinite(projection.x) && Number.isFinite(projection.y));

/* ---------------------------------------------------------------- 手性（坐标方向） */

// 这一组是用户反馈"三维视图里 Z 坐标是反的"之后加的。
// 早先的 toView 有两处符号错误（Rᵀ 写成 R、距离项写成 +d），
// 合起来是水平镜像 + z 轴视觉反向。数值全都有限、其他断言也全能过，
// 所以**只有手性断言能抓住它**。
//
// 约定（世界为 z 向上的右手系）：
//   yaw = −90° 时相机在 −y 侧朝 +y 看；
//   屏幕右方 = 世界 −x，屏幕上方 = 世界 +z，世界 +y 朝屏幕内。
{
  const W = 600, H = 400;
  const at = (camera, point) => camera.project(point, W, H);
  const front = new OrbitCamera({ yaw: -Math.PI / 2, pitch: 0, distance: 300, quality: 1 });
  const origin = at(front, [0, 0, 0]);

  const plusX = at(front, [60, 0, 0]);
  const minusX = at(front, [-60, 0, 0]);
  check('标准前视下世界 −x 落在屏幕右方', minusX.x > origin.x + 20,
    `−x 屏幕 x=${minusX.x.toFixed(0)}`);
  check('标准前视下世界 +x 落在屏幕左方', plusX.x < origin.x - 20,
    `+x 屏幕 x=${plusX.x.toFixed(0)}`);
  check('水平方向不镜像（±x 分居两侧）', plusX.x < origin.x && minusX.x > origin.x);

  const plusY = at(front, [0, 60, 0]);
  check('标准前视下世界 +y 朝屏幕内（更远）', plusY.depth > origin.depth + 5,
    `+y 深度=${plusY.depth.toFixed(0)}，原点=${origin.depth.toFixed(0)}`);
  check('标准前视下 +y 不改变屏幕水平位置', Math.abs(plusY.x - origin.x) < 2);

  // 俯视：世界 +z 在屏幕上方，且更靠近相机
  const tilted = new OrbitCamera({ yaw: -Math.PI / 2, pitch: Math.PI / 3, distance: 300, quality: 1 });
  const base = at(tilted, [0, 0, 0]);
  const plusZ = at(tilted, [0, 0, 60]);
  const minusZ = at(tilted, [0, 0, -60]);
  check('俯视时世界 +z 落在屏幕上方', plusZ.y < base.y - 10,
    `+z 屏幕 y=${plusZ.y.toFixed(0)}，原点 y=${base.y.toFixed(0)}`);
  check('俯视时世界 +z 更靠近相机', plusZ.depth < base.depth - 5,
    `+z 深度=${plusZ.depth.toFixed(0)}`);
  check('俯视时世界 −z 更远且更靠下', minusZ.depth > base.depth && minusZ.y > plusZ.y);

  // 说明：这里**不**断言"三个轴向的屏幕投影等长/正交"。
  // 透视投影下深度随位移变化，会带来二阶项，那种断言在接近竖直时本就不成立；
  // 用不成立的判据去测正确实现，只会得到假失败。
  // 真正要保证的是"方向对不对"，上面那组方向断言已经覆盖。

  // 深度必须随"沿视线更远"而单调增大
  const far = at(front, [0, 200, 0]);
  const near = at(front, [0, -200, 0]);
  check('深度随远离相机而增大', far.depth > near.depth,
    `远=${far.depth.toFixed(0)} 近=${near.depth.toFixed(0)}`);
}

/* ---------------------------------------------------------------- 自动取景 */

const framed = new OrbitCamera({ quality: 1 });
const framingModel = buildMotor({ kind: 'pmsm_bldc', polePairs: 4 });
const { fitBounds } = await import('../dist/model.js');
const framedResult = fitCamera(framed, fitBounds(framingModel), 760, 420, 1.06);
check('取景返回合理距离', framedResult.distance > 0 && Number.isFinite(framedResult.distance));
// 取景后主体八个角点应落在画面内
const stats = fitBounds(framingModel);
let inside = 0;
for (const x of [stats.min[0], stats.max[0]]) {
  for (const y of [stats.min[1], stats.max[1]]) {
    for (const z of [stats.min[2], stats.max[2]]) {
      const p = framed.project([x, y, z], 760, 420);
      if (p.x >= 0 && p.x <= 760 && p.y >= 0 && p.y <= 420) inside += 1;
    }
  }
}
check('取景后主体完整落在画面内', inside === 8, `落在画面内 ${inside}/8`);
// 主体应占据画面的可观比例：量八个角点投影后的包围范围
let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
for (const x of [stats.min[0], stats.max[0]]) {
  for (const y of [stats.min[1], stats.max[1]]) {
    for (const z of [stats.min[2], stats.max[2]]) {
      const p = framed.project([x, y, z], 760, 420);
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
  }
}
const fill = Math.max((maxX - minX) / 760, (maxY - minY) / 420);
// 画布是横向的，而电机主体是细长的，占比由长轴决定；
// 50% 以上就说明没有"缩成一小块"（早先版本只有 15% 左右）。
check('主体在画面里有足够占比', fill > 0.5, `占画面 ${(fill * 100).toFixed(0)}%`);
check('主体水平居中', Math.abs((minX + maxX) / 2 - 380) < 40,
  `中心 x=${((minX + maxX) / 2).toFixed(0)}`);

/* ---------------------------------------------------------------- 结果 */

if (failures.length) {
  console.error(`模型测试失败 ${failures.length} 项：`);
  for (const failure of failures.slice(0, 30)) console.error('  ✗ ' + failure);
  process.exit(1);
}
console.log(`模型测试通过：${passed} 项断言`);

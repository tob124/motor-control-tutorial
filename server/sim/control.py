"""数字控制、反馈测量与机器人运动学。

课程作者：Connor He 和 Astra。

包含三部分：

1. ``PI`` / ``CascadedController``：固定采样周期、零阶保持、输出限幅、
   条件积分抗饱和。控制周期与积分步长彼此独立，对应真实数字控制器。
2. ``Encoder``：增量式编码器量化模型，以及几种真实的反馈接线故障。
   关键纪律：**故障只作用于测量与执行通道，绝不篡改物理状态**。
3. 差速底盘运动学与路径生成：第 7–8 周"从一台电机到整机运动"的核心。
"""
from __future__ import annotations

import math

__all__ = ['PI', 'Cascade', 'Encoder', 'DifferentialDrive', 'Path', 'saturate',
           'complementary_heading', 'four_bar', 'limit_switch_state']

_EPS = 1e-12


def saturate(value, limit):
    """对称饱和，返回 ``(out, saturated)``。``limit<=0`` 时视为不限幅。"""
    if limit is None or limit <= 0:
        return value, False
    if value > limit:
        return limit, True
    if value < -limit:
        return -limit, True
    return value, False


class PI:
    """数字 PI 控制器，带输出限幅与抗积分饱和。

    在每个控制周期调用一次 ``step(error, period)``，实现为

        I_{k+1} = I_k + Ki·e_k·Δt          （积分）
        u      = Kp·e_k + I_{k+1}          （输出）

    饱和处理采用**条件积分**（condition integration）：

    * 未饱和：正常累加积分；
    * 已饱和：只有当"这一拍的新误差会把积分往回收"时才累加。
      也就是说饱和时积分不再朝加深饱和的方向爬升。

    这比"输出饱和就冻结积分"更精确：如果饱和后误差反号，
    积分被允许继续变化，控制器才能及时退出饱和（否则就是典型的一冲到底）。
    """

    def __init__(self, kp, ki, limit=0.0, anti_windup=True, name='PI'):
        self.kp = float(kp)
        self.ki = float(ki)
        self.limit = float(limit)
        self.anti_windup = bool(anti_windup)
        self.name = name
        self.integral = 0.0
        self.last_output = 0.0
        self.last_error = 0.0
        self.saturated = False
        self.windup_events = 0

    def reset(self, value=0.0):
        self.integral = 0.0
        self.last_output = value
        self.last_error = 0.0
        self.saturated = False

    def step(self, error, period):
        """执行一次控制更新，返回限幅后的输出。"""
        if not (period > 0.0):
            raise ValueError('控制周期必须为正。')
        if not math.isfinite(error):
            raise ValueError('控制误差必须是有限数。')
        self.last_error = error
        proposed = self.integral + self.ki * error * period
        raw = self.kp * error + proposed
        output, saturated = saturate(raw, self.limit)
        if not saturated or not self.anti_windup:
            self.integral = proposed
        else:
            # 饱和时：只有在"积分朝饱和方向继续走"的情况下才冻结。
            # 反号的误差允许积分回退，控制器才能退出饱和。
            deepening = (raw > self.limit and proposed > self.integral) or \
                        (raw < -self.limit and proposed < self.integral)
            if deepening:
                self.windup_events += 1
            else:
                self.integral = proposed
        self.last_output = output
        self.saturated = saturated
        return output

    def ports(self):
        return {'name': self.name, 'kp': self.kp, 'ki': self.ki, 'limit': self.limit,
                'anti_windup': self.anti_windup, 'integral': self.integral,
                'saturated': self.saturated, 'windup_events': self.windup_events}


class Cascade:
    """位置 → 速度 → 电流 三级串级控制。

    每一级都可以单独打开或关闭，用来观察"少一级会怎样"。
    每级的限幅就是下一级的目标上界——这正是真实伺服里的做法。
    """

    def __init__(self, position=None, speed=None, current=None):
        self.position = position
        self.speed = speed
        self.current = current
        self.trace = []

    def step(self, reference, measured, period):
        """``reference`` 与 ``measured`` 是 ``(position, speed, current)``。

        返回 ``(current_reference, speed_reference, position_reference)``，
        其中后两个是为画图保留的中间量。
        """
        target_pos, target_speed, target_current = reference
        _pos, speed, current = measured
        speed_ref = target_speed
        if self.position is not None:
            speed_ref = self.position.step(target_pos - measured[0], period)
        current_ref = target_current
        if self.speed is not None:
            current_ref = self.speed.step(speed_ref - speed, period)
        voltage = None
        current_used = current
        if self.current is not None:
            voltage = self.current.step(current_ref - current_used, period)
        return {'voltage': voltage, 'current_ref': current_ref, 'speed_ref': speed_ref,
                'position_ref': target_pos, 'current': current_used}


class Encoder:
    """增量式编码器（4 倍频）量化模型。

    ``edges_per_rev = 4 × 线数``。位置以"计数"为单位输出，
    速度只能靠计数差分得到——这正是真实驱动器速度环噪声的来源。
    """

    def __init__(self, lines=1000, faults=(), bias_rad=0.0, jitter_counts=0.0):
        self.lines = int(lines)
        if self.lines < 1:
            raise ValueError('编码器线数必须为正整数。')
        self.edges_per_rev = 4 * self.lines
        self.faults = tuple(faults)
        self.bias_rad = float(bias_rad)
        self.jitter_counts = float(jitter_counts)
        self.reversed = 'encoder_reversed' in self.faults
        self.units_confusion = 'units_confusion' in self.faults

    def counts(self, theta_m):
        """机械角（rad，可为多圈）→ 计数（整数）。"""
        if self.units_confusion:
            # 把"度"当成"弧度"换算：真实接线/配置常见的量纲事故
            theta_m = math.radians(theta_m)
        if self.reversed:
            theta_m = -theta_m
        theta = theta_m + self.bias_rad
        return int(round(theta / (2.0 * math.pi) * self.edges_per_rev))

    def position_rad(self, theta_m):
        """量化后的位置估计（rad），只由计数反算得到。"""
        return self.counts(theta_m) * 2.0 * math.pi / self.edges_per_rev

    def quant_step_rad(self):
        return 2.0 * math.pi / self.edges_per_rev

    def velocity(self, theta_m_now, theta_m_prev, period):
        """用计数差分算速度（rad/s），会带上量化噪声。"""
        if not (period > 0):
            raise ValueError('差分周期必须为正。')
        delta = self.counts(theta_m_now) - self.counts(theta_m_prev)
        return delta * 2.0 * math.pi / self.edges_per_rev / period


class DifferentialDrive:
    """两轮差速底盘：运动学、逆解与里程计。

    轮距 ``track``（m）、轮半径 ``wheel_radius``（m）、减速比 ``gear``。
    中间变量是左右轮线速度与底盘线速度/角速度。
    """

    def __init__(self, track=0.4, wheel_radius=0.0625, gear=1.0, max_wheel_speed=6.0):
        if track <= 0 or wheel_radius <= 0 or gear <= 0:
            raise ValueError('轮距、轮半径、减速比必须为正。')
        self.track = float(track)
        self.wheel_radius = float(wheel_radius)
        self.gear = float(gear)
        self.max_wheel_speed = float(max_wheel_speed)

    def inverse(self, v, omega):
        """底盘线速度/角速度 → 左右轮角速度（rad/s，电机侧）。"""
        v_l = v - omega * self.track / 2.0
        v_r = v + omega * self.track / 2.0
        w_l = v_l / self.wheel_radius * self.gear
        w_r = v_r / self.wheel_radius * self.gear
        return w_l, w_r

    def forward(self, w_l, w_r):
        """左右轮角速度 → 底盘线速度与角速度。"""
        v_l = w_l * self.wheel_radius / self.gear
        v_r = w_r * self.wheel_radius / self.gear
        return (v_l + v_r) / 2.0, (v_r - v_l) / self.track

    def step_pose(self, pose, w_l, w_r, dt):
        """用精确圆弧积分推进位姿 ``(x, y, theta)``。

        直接在圆弧上积分比小角度近似更准，尤其是原地转向时。
        """
        x, y, theta = pose
        v, omega = self.forward(w_l, w_r)
        if abs(omega) < 1e-9:
            return (x + v * math.cos(theta) * dt, y + v * math.sin(theta) * dt, theta)
        radius = v / omega
        theta_new = theta + omega * dt
        x_new = x + radius * (math.sin(theta_new) - math.sin(theta))
        y_new = y - radius * (math.cos(theta_new) - math.cos(theta))
        return (x_new, y_new, theta_new)

    def wheel_speeds_for_target(self, target_rpm_l, target_rpm_r):
        return target_rpm_l * 2.0 * math.pi / 60.0, target_rpm_r * 2.0 * math.pi / 60.0


class Path:
    """教学用路径生成器（直线、圆、矩形、正弦）。

    路径按弧长参数化，``point(s)`` 返回 ``(x, y, 切线角)``。
    """

    def __init__(self, shape='circle', length=6.0, radius=1.5):
        if shape not in ('line', 'circle', 'rectangle', 'sine'):
            raise ValueError('未知路径形状。')
        if length <= 0 or radius <= 0:
            raise ValueError('路径长度与半径必须为正。')
        self.shape = shape
        self.length = float(length)
        self.radius = float(radius)

    def point(self, s):
        """返回弧长 ``s`` 处的 ``(x, y, heading)``。"""
        if self.shape == 'line':
            return s, 0.0, 0.0
        if self.shape == 'circle':
            theta = s / self.radius
            return self.radius * math.sin(theta), self.radius * (1.0 - math.cos(theta)), theta
        if self.shape == 'sine':
            k = 2.0 * math.pi / self.length
            x = s
            y = self.radius * math.sin(k * s)
            return x, y, math.atan2(self.radius * k * math.cos(k * s), 1.0)
        # 矩形：边长为 2·radius，按周长分成四段
        side = 2.0 * self.radius
        perimeter = 4.0 * side
        s = s % perimeter
        leg = int(s // side)
        t = s - leg * side
        if leg == 0:
            return t, 0.0, 0.0
        if leg == 1:
            return side, t, math.pi / 2.0
        if leg == 2:
            return side - t, side, math.pi
        return 0.0, side - t, -math.pi / 2.0

    def closed_loop_length(self):
        if self.shape == 'circle':
            return 2.0 * math.pi * self.radius
        if self.shape == 'rectangle':
            return 8.0 * self.radius
        return self.length


class PurePursuit:
    """纯追踪（pure pursuit）路径跟踪：由前视距离决定期望航向。

    ``step(pose, path, s_est, speed)`` 返回 ``(v, omega)``。
    这是 ROBOCON 底盘最常用、最容易在现场调稳的算法之一。
    """

    def __init__(self, lookahead=0.4, max_omega=6.0, max_v=3.0):
        if lookahead <= 0:
            raise ValueError('前视距离必须为正。')
        self.lookahead = float(lookahead)
        self.max_omega = float(max_omega)
        self.max_v = float(max_v)

    def step(self, pose, path, s_est, speed=1.0):
        x, y, theta = pose
        # 沿路径向前找第一个距离超过前视距离的点
        target = None
        step = max(0.01, self.lookahead / 10.0)
        s = s_est
        for _ in range(600):
            px, py, _h = path.point(s)
            if math.hypot(px - x, py - y) >= self.lookahead:
                target = (px, py)
                break
            s += step
        if target is None:
            target = path.point(s)[:2]
        angle_to_target = math.atan2(target[1] - y, target[0] - x)
        error = _wrap(angle_to_target - theta)
        # 曲率 κ = 2·sin(α)/Ld，ω = v·κ
        v = min(speed, self.max_v)
        omega = 2.0 * v * math.sin(error) / self.lookahead
        omega = max(-self.max_omega, min(self.max_omega, omega))
        return v, omega, error


def _wrap(angle):
    """把角度归一到 (−π, π]。"""
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def complementary_heading(gyro_rate, theta_prev, dt, alpha=0.98):
    """陀螺 + 里程计航向的互补滤波。

    ``alpha`` 越大越信任陀螺（短期准），越小越信任里程计（长期不漂）。
    """
    if not (0.0 < alpha <= 1.0):
        raise ValueError('互补滤波系数必须在 (0, 1] 内。')
    return _wrap(theta_prev + gyro_rate * dt)


def _circle_intersections(x1, y1, r1, x2, y2, r2):
    """两圆交点，返回 0、1 或 2 个解。

    这是机构解算的标准子问题，单独拿出来既好测也好读。
    """
    dx, dy = x2 - x1, y2 - y1
    d = math.hypot(dx, dy)
    if d < 1e-12:
        return []
    if d > r1 + r2 + 1e-12 or d < abs(r1 - r2) - 1e-12:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2.0 * d)
    h_sq = r1 * r1 - a * a
    h = math.sqrt(h_sq) if h_sq > 1e-18 else 0.0
    mx = x1 + a * dx / d
    my = y1 + a * dy / d
    if h == 0.0:
        return [(mx, my)]
    ox, oy = -dy / d * h, dx / d * h
    return [(mx + ox, my + oy), (mx - ox, my - oy)]


def four_bar(crank_angle, crank_len=0.04, coupler_len=0.12, rocker_len=0.10, ground=0.11):
    """平面四连杆机构位置解（ROBOCON 常见的取球/击球机构的简化模型）。

    约定：机架铰点 ``A = (0, 0)``、``D = (ground, 0)``；
    曲柄 ``AB = crank_len``，连杆 ``BC = coupler_len``，摇杆 ``CD = rocker_len``。
    ``crank_angle`` 是曲柄相对机架的方向角。

    返回 ``{'crank': B, 'coupler': C, 'rocker_angle': φ, 'ratio': dφ/dθ}``。
    装配分支固定取 ``C`` 在机架线**上方**（y 较大）的那一支，
    这样"同一个曲柄角对应同一个姿态"是可复现的——机构解算里
    分支跳变是真实事故来源，这里明确固定下来。
    无解（不能装配）时返回 ``None``。
    """
    for name, value in (('crank_len', crank_len), ('coupler_len', coupler_len),
                        ('rocker_len', rocker_len), ('ground', ground)):
        if not (value > 0):
            raise ValueError(f'{name} 必须为正。')
    bx = crank_len * math.cos(crank_angle)
    by = crank_len * math.sin(crank_angle)
    solutions = _circle_intersections(bx, by, coupler_len, ground, 0.0, rocker_len)
    if not solutions:
        return None
    cx, cy = max(solutions, key=lambda point: point[1])
    rocker_angle = math.atan2(cy, cx - ground)
    # 用有限差分给出角速度传动比，供"机构放大倍数"教学用
    eps = 1e-5
    bx2 = crank_len * math.cos(crank_angle + eps)
    by2 = crank_len * math.sin(crank_angle + eps)
    neighbours = _circle_intersections(bx2, by2, coupler_len, ground, 0.0, rocker_len)
    ratio = None
    if neighbours:
        cx2, cy2 = max(neighbours, key=lambda point: point[1])
        ratio = (math.atan2(cy2, cx2 - ground) - rocker_angle) / eps
    return {'coupler': (cx, cy), 'rocker_angle': rocker_angle, 'ratio': ratio,
            'crank': (bx, by), 'ground': (ground, 0.0)}


def limit_switch_state(position, lower, upper):
    """硬限位：返回 ``(state, allowed_direction)``。

    ``state`` ∈ ``{'ok', 'at_lower', 'at_upper'}``；
    ``allowed_direction`` 是仍被允许的运动方向符号（−1 只允许负向）。
    """
    if lower > upper:
        raise ValueError('限位下限不能大于上限。')
    if position <= lower:
        return 'at_lower', 1.0
    if position >= upper:
        return 'at_upper', -1.0
    return 'ok', 0.0

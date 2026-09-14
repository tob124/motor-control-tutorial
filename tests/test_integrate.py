"""积分器与通用工具的测试。

课程作者：Connor He 和 Astra。

数值正确性是整门课的地基：如果 RK4 写错了，后面所有"曲线"都不可信。
这里用有解析解的常微分方程把积分器钉死。
"""
import math
import unittest

from server.sim import integrate, metrics


class RK4Test(unittest.TestCase):
    def test_exponential_decay_matches_exact(self):
        """y' = −y，y(0)=1，积分 1 秒后应等于 e^{-1}。"""
        f = lambda t, s: (-s[0],)
        state, t = (1.0,), 0.0
        for _ in range(10_000):
            state = integrate.rk4_step(f, t, state, 1e-4)
            t += 1e-4
        self.assertAlmostEqual(state[0], math.exp(-1.0), places=12)

    def test_second_order_oscillator_conserves_amplitude(self):
        """简谐振子 x'' = −x：一个周期后振幅应基本不变。"""
        f = lambda t, s: (s[1], -s[0])
        state, t, step = (1.0, 0.0), 0.0, 1e-4
        for _ in range(int(round(2 * math.pi / step))):
            state = integrate.rk4_step(f, t, state, step)
            t += step
        self.assertAlmostEqual(state[0], 1.0, places=5)
        # 速度分量残留约 1.5e-5，属于 RK4 截断误差量级
        self.assertAlmostEqual(state[1], 0.0, places=4)

    def test_fourth_order_accuracy_beats_second_order(self):
        """RK4 在相同步长下的误差应显著小于欧拉法。"""
        f = lambda t, s: (-s[0],)
        exact = math.exp(-1.0)
        euler, rk4, t, step = 1.0, (1.0,), 0.0, 0.01
        for _ in range(100):
            euler -= euler * step
            rk4 = integrate.rk4_step(f, t, rk4, step)
            t += step
        self.assertLess(abs(rk4[0] - exact), abs(euler - exact) / 100.0)

    def test_rejects_non_positive_step(self):
        f = lambda t, s: (-s[0],)
        for bad in (0.0, -1e-3, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                integrate.rk4_step(f, 0.0, (1.0,), bad)

    def test_vector_helpers(self):
        self.assertEqual(integrate.add((1.0, 2.0), (3.0, 4.0)), (4.0, 6.0))
        self.assertEqual(integrate.scale((1.0, -2.0), 2.0), (2.0, -4.0))
        self.assertEqual(integrate.combine((1.0, (1.0,)), (2.0, (2.0,))), (5.0,))


class SubstepPlanTest(unittest.TestCase):
    def test_grid_is_exact_multiple(self):
        n_control, n_sub, dt_ctrl, dt_sub = integrate.substep_plan(5.0, 1e-3, 1e-4)
        self.assertEqual(n_control, 5000)
        self.assertEqual(n_sub, 10)
        self.assertAlmostEqual(dt_ctrl * n_control, 5.0, places=12)
        self.assertAlmostEqual(dt_sub * n_sub, dt_ctrl, places=12)

    def test_rejects_control_slower_than_substep(self):
        with self.assertRaises(ValueError):
            integrate.substep_plan(1.0, 1e-4, 1e-3)

    def test_rejects_too_many_substeps(self):
        with self.assertRaises(ValueError):
            integrate.substep_plan(10.0, 1e-5, 1e-8)

    def test_rejects_bad_inputs(self):
        for duration, period, substep in ((0.0, 1e-3, 1e-4), (-1, 1e-3, 1e-4),
                                          (1.0, 0.0, 1e-4), (1.0, 1e-3, float('nan'))):
            with self.assertRaises(ValueError):
                integrate.substep_plan(duration, period, substep)


class MetricsTest(unittest.TestCase):
    def test_percent_overshoot(self):
        self.assertAlmostEqual(metrics.percent_overshoot([0.0, 1.2, 1.0, 1.0], 1.0), 20.0)
        self.assertEqual(metrics.percent_overshoot([0.0, 0.5, 0.9], 1.0), 0.0)
        self.assertIsNone(metrics.percent_overshoot([1.0, 2.0], 0.0))

    def test_settling_time(self):
        """最后一次越出 ±5% 带之后就算进入稳态。"""
        times = [i * 0.1 for i in range(12)]
        values = [0.0, 2.0, 1.5, 1.2, 1.05, 1.02, 1.01, 1.0, 1.0, 1.0, 1.0, 1.0]
        # 1.05 恰好不越界（判据是严格大于），最后一次越界在 t=0.3
        self.assertAlmostEqual(metrics.settling_time(times, values, 1.0, 0.05), 0.4)
        # 把容差收紧到 2%，1.02 也变成越界，最后一次在 t=0.5
        self.assertAlmostEqual(metrics.settling_time(times, values, 1.0, 0.02), 0.5)

    def test_settling_time_never_settles(self):
        times = [i * 0.1 for i in range(5)]
        values = [0.0, 1.0, 2.0, 3.0, 4.0]
        self.assertIsNone(metrics.settling_time(times, values, 1.0, 0.02))

    def test_steady_state_error_uses_tail_average(self):
        measured = [0.0, 0.5, 1.0, 1.0, 0.98, 1.0]
        self.assertAlmostEqual(metrics.steady_state_error(1.0, measured), 0.0, places=9)

    def test_tracking_error_reports_worst_point(self):
        times = [0.0, 1.0, 2.0]
        reference = [1.0, 1.0, 1.0]
        measured = [0.9, 0.5, 1.0]
        result = metrics.tracking_error(times, reference, measured)
        self.assertAlmostEqual(result['max_abs'], 0.5)
        self.assertAlmostEqual(result['max_at'], 1.0)

    def test_rms_and_peak_ignore_non_finite(self):
        self.assertAlmostEqual(metrics.rms([3.0, 4.0, float('nan')]), math.sqrt(12.5))
        self.assertAlmostEqual(metrics.peak([-7.0, 2.0]), 7.0)
        self.assertIsNone(metrics.rms([float('nan'), float('inf')]))

    def test_first_order_response_matches_analytic(self):
        tau, gain = 0.5, 2.0
        self.assertAlmostEqual(metrics.first_order_response(gain, tau, tau),
                               2.0 * (1.0 - math.exp(-1.0)), places=12)

    def test_bode_of_first_order_lag(self):
        gain, tau = 10.0, 0.1
        points = metrics.bode_points(gain, tau, [0.0, 1.0 / tau])
        self.assertAlmostEqual(points[0][1], gain, places=9)
        self.assertAlmostEqual(points[0][2], 0.0, places=9)
        # 转折频率处幅值降到 1/√2，相位 −45°
        self.assertAlmostEqual(points[1][1], gain / math.sqrt(2.0), places=9)
        self.assertAlmostEqual(points[1][2], -45.0, places=9)

    def test_sine_response_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            metrics.sine_response(1.0, -0.1, 1.0)


if __name__ == '__main__':
    unittest.main()

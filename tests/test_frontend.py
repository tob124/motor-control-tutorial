"""前端几何与模型查看器的测试入口。

课程作者：Connor He 和 Astra。

前端没有构建步骤，也没有浏览器测试框架，但**几何与动画逻辑是纯函数**，
可以在 Node 里直接测。这里把它接进统一入口，让
``python3 -m unittest discover -s tests`` 一条命令覆盖全栈。

覆盖的三件事（都是"错了就看不出来"的地方）：

* ``model.test.mjs``：网格规模、包围盒、四连杆装配约束、
  转角积分是否保守、相机投影与交互限幅；
* ``viewer.test.mjs``：用 canvas 桩件驱动 ModelViewer，
  验证 2D/3D 切换、时间轴夹取、播放推进与自动停止、空数据不崩。

没有安装 Node 时会跳过，而不是失败——课程本身不依赖 Node。
"""
import shutil
import subprocess
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
NODE = shutil.which('node')

#: Node 需要这个解析钩子才能理解浏览器风格的 '/model.js' 绝对导入
LOADER = './tests/browser-resolve.mjs'


@unittest.skipIf(NODE is None, '本机没有 Node，跳过前端几何测试')
class FrontendModelTest(unittest.TestCase):
    def run_node(self, script):
        result = subprocess.run(
            [NODE, '--experimental-loader', LOADER, script],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        return result

    def assert_node_passes(self, script):
        result = self.run_node(script)
        output = (result.stdout or '') + (result.stderr or '')
        self.assertEqual(
            result.returncode, 0,
            f'{script} 失败：\n{output[-3000:]}')
        self.assertIn('通过', result.stdout,
                      f'{script} 没有报告通过：\n{output[-1500:]}')
        return result.stdout

    def test_geometry_and_camera(self):
        stdout = self.assert_node_passes('tests/model.test.mjs')
        # 断言数量要有下限，避免测试被误删成空壳
        self.assertRegex(stdout, r'通过：\d+ 项断言')
        count = int(stdout.strip().split('：')[1].split(' ')[0])
        self.assertGreaterEqual(count, 200, f'几何断言数偏少：{count}')

    def test_model_viewer_animation(self):
        stdout = self.assert_node_passes('tests/viewer.test.mjs')
        count = int(stdout.strip().split('：')[1].split(' ')[0])
        self.assertGreaterEqual(count, 12, f'查看器断言数偏少：{count}')


@unittest.skipIf(NODE is None, '本机没有 Node，跳过前端语法检查')
class FrontendSyntaxTest(unittest.TestCase):
    """前端模块必须能被解析：语法错误在浏览器里表现为整页空白。"""

    MODULES = ['app.js', 'plot.js', 'model.js', 'render3d.js', 'view.js', 'modelview.js']

    def test_modules_parse(self):
        for name in self.MODULES:
            with self.subTest(module=name):
                result = subprocess.run(
                    [NODE, '--check', str(ROOT / 'dist' / name)],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0,
                                 f'{name} 语法错误：{result.stderr[-800:]}')

    def test_index_loads_expected_assets(self):
        html = (ROOT / 'dist' / 'index.html').read_text(encoding='utf-8')
        for asset in ('/app.js', '/styles.css', '/vendor/xterm.js'):
            self.assertIn(asset, html, f'index.html 没有引用 {asset}')
        self.assertIn('id="mode-button"', html, 'index.html 缺少显示模式开关')
        self.assertIn('id="view-root"', html)

    def test_model_modules_are_served_by_the_app(self):
        """静态目录里必须真的有这些文件，否则页面会 404。"""
        for name in self.MODULES:
            self.assertTrue((ROOT / 'dist' / name).is_file(), f'dist/{name} 缺失')


if __name__ == '__main__':
    unittest.main()

"""课程内容一致性测试。

课程作者：Connor He 和 Astra。

内容也是代码：单元编号、周号、实验引用、理解题答案下标、软件条目引用，
任何一处不一致都会让学员在页面上撞到空白或错误的实验。
这些断言就是内容的"编译检查"。
"""
import unittest
from pathlib import Path

from server.app import ROOT

COURSE = ROOT / 'course'


class ContentPresenceTest(unittest.TestCase):
    def setUp(self):
        if not (COURSE / 'curriculum.json').is_file():
            self.skipTest('课程内容尚未生成，先运行 python3 scripts/build_content.py')

    def load(self):
        import json
        return json.loads((COURSE / 'curriculum.json').read_text(encoding='utf-8')), \
               json.loads((COURSE / 'software.json').read_text(encoding='utf-8'))


class StructureTest(ContentPresenceTest):
    def test_eleven_weeks_of_four_lessons(self):
        curriculum, _ = self.load()
        self.assertEqual(len(curriculum['weeks']), 11)
        self.assertEqual(len(curriculum['lessons']), 44)
        counts = {}
        for lesson in curriculum['lessons']:
            counts[lesson['week']] = counts.get(lesson['week'], 0) + 1
        for week in range(1, 12):
            self.assertEqual(counts.get(week), 4, f'第 {week} 周不是 4 个单元')

    def test_lesson_ids_unique_and_ordered(self):
        curriculum, _ = self.load()
        ids = [lesson['id'] for lesson in curriculum['lessons']]
        self.assertEqual(len(ids), len(set(ids)))
        for lesson in curriculum['lessons']:
            self.assertTrue(lesson['id'].startswith(f"w{lesson['week']}-"),
                            f"{lesson['id']} 与周号 {lesson['week']} 不一致")
            self.assertIn(lesson['order'], (1, 2, 3, 4))

    def test_required_fields_present(self):
        curriculum, _ = self.load()
        for lesson in curriculum['lessons']:
            for key in ('title', 'summary', 'objectives', 'sections', 'equations',
                        'tasks', 'quiz', 'deliverable', 'glossary', 'softwareIds'):
                self.assertIn(key, lesson, f"{lesson['id']} 缺少 {key}")
            self.assertTrue(lesson['title'].strip())
            self.assertTrue(lesson['summary'].strip())
            self.assertTrue(lesson['deliverable'].strip())

    def test_teaching_depth(self):
        """每节课至少 3 段正文、2 条关系式、2 道理解题。"""
        curriculum, _ = self.load()
        for lesson in curriculum['lessons']:
            self.assertGreaterEqual(len(lesson['sections']), 3, f"{lesson['id']} 正文段数不足")
            self.assertGreaterEqual(len(lesson['equations']), 2, f"{lesson['id']} 关系式不足")
            self.assertGreaterEqual(len(lesson['quiz']), 2, f"{lesson['id']} 理解题不足")
            self.assertGreaterEqual(len(lesson['objectives']), 2, f"{lesson['id']} 目标不足")
            for section in lesson['sections']:
                self.assertEqual(set(section) - {'title', 'body'}, set(),
                                 f"{lesson['id']} 段落含多余字段")
                self.assertGreaterEqual(len(section['body']), 120,
                                        f"{lesson['id']} 段落 {section['title']} 过短")


class ReferenceTest(ContentPresenceTest):
    def test_quiz_answers_are_valid_indices(self):
        curriculum, _ = self.load()
        for lesson in curriculum['lessons']:
            for index, item in enumerate(lesson['quiz']):
                options = item['options']
                self.assertGreaterEqual(len(options), 3, f"{lesson['id']} 第 {index + 1} 题选项太少")
                self.assertIsInstance(item['answer'], int)
                self.assertTrue(0 <= item['answer'] < len(options),
                                f"{lesson['id']} 第 {index + 1} 题答案下标越界")
                self.assertTrue(item['explanation'].strip())
                # 选项不能重复，否则答案会歧义
                self.assertEqual(len(options), len(set(options)),
                                 f"{lesson['id']} 第 {index + 1} 题选项重复")

    def test_software_references_resolve(self):
        curriculum, software = self.load()
        known = {item['id'] for item in software}
        for lesson in curriculum['lessons']:
            for software_id in lesson.get('softwareIds') or []:
                self.assertIn(software_id, known, f"{lesson['id']} 引用了未知工具 {software_id}")
        for track in curriculum.get('tracks', []):
            for software_id in track.get('softwareIds') or []:
                self.assertIn(software_id, known, f"路线 {track['id']} 引用了未知工具")

    def test_lab_presets_exist(self):
        from server.app import _PRESETS
        curriculum, _ = self.load()
        known = {preset['id'] for preset in _PRESETS}
        for lesson in curriculum['lessons']:
            for lab in lesson.get('labs') or []:
                self.assertIn(lab['id'], known, f"{lesson['id']} 引用了未知实验 {lab['id']}")

    def test_lab_specs_are_actually_runnable(self):
        """每个预设的 spec 必须能通过校验——否则学员点进去就是错误页。"""
        from server.sim import spec as simspec
        from server.app import _PRESETS
        for preset in _PRESETS:
            with self.subTest(preset=preset['id']):
                simspec.validate(preset['spec'])

    def test_curve_presets_reference_real_curves(self):
        from server.sim import scenarios
        from server.app import _PRESETS
        for preset in _PRESETS:
            curve = preset.get('curve')
            if curve:
                with self.subTest(preset=preset['id']):
                    self.assertIn(curve, scenarios.CURVES)

    def test_tasks_have_hints_and_solution(self):
        curriculum, _ = self.load()
        for lesson in curriculum['lessons']:
            for task in lesson['tasks']:
                self.assertTrue(task.get('title'))
                self.assertTrue(task.get('description'))
                self.assertGreaterEqual(len(task.get('hints') or []), 2,
                                        f"{lesson['id']} 任务提示不足")
                self.assertTrue(task.get('solution'))

    def test_no_placeholders_left_in_prose(self):
        """正文里不该留下占位符或半截标记。"""
        curriculum, _ = self.load()
        markers = ('TODO', 'FIXME', 'XXX', '待补充', '此处省略', '（略）')
        for lesson in curriculum['lessons']:
            for section in lesson['sections']:
                for marker in markers:
                    self.assertNotIn(marker, section['body'],
                                     f"{lesson['id']} 的段落 {section['title']} 含占位符 {marker}")
                self.assertFalse(section['body'].rstrip().endswith('\n'),
                                 f"{lesson['id']} 的段落 {section['title']} 以空行结尾")


class SoftwareTest(ContentPresenceTest):
    def test_software_entries_are_complete(self):
        _, software = self.load()
        self.assertTrue(software)
        for item in software:
            for key in ('id', 'name', 'category', 'stage', 'summary', 'scope',
                        'resource', 'license', 'source', 'steps'):
                self.assertIn(key, item, f"{item.get('id')} 缺少 {key}")
            self.assertTrue(item['steps'], f"{item['id']} 没有步骤")
            for step in item['steps']:
                for key in ('title', 'explanation', 'expected', 'troubleshooting'):
                    self.assertIn(key, step, f"{item['id']} 的步骤缺少 {key}")

    def test_ids_unique(self):
        _, software = self.load()
        ids = [item['id'] for item in software]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == '__main__':
    unittest.main()

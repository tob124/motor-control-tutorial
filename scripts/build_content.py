#!/usr/bin/env python3
"""生成 course/curriculum.json 与 course/software.json。

课程作者：Connor He 和 Astra。

内容写在 ``scripts/content_part*.py`` 里，运行时只读生成出来的 JSON。
这样做的理由：内容修改不需要重启服务（服务按 mtime 缓存），
而且生成过程可以做一致性校验——单元编号、周号、实验引用、
理解题答案下标、软件条目引用，全部在生成时就检查一遍。

运行：

    python3 scripts/build_content.py            # 只生成不校验
    python3 scripts/build_content.py --check    # 生成并做一致性校验（有错则退出码 1）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from server.sim import spec as simspec          # noqa: E402

import content_part1                            # noqa: E402
import content_part2                            # noqa: E402
import content_part3                            # noqa: E402
import content_part4                            # noqa: E402

AUTHORS = ['Connor He', 'Astra']
VERSION = '2.0'


def lessons_for_week(week_id):
    module = {1: content_part1, 2: content_part1, 3: content_part1,
              4: content_part2, 5: content_part2,
              6: content_part3, 7: content_part3, 8: content_part3,
              9: content_part4, 10: content_part4, 11: content_part4}[week_id]
    function = {1: 'week1', 2: 'week2', 3: 'week3', 4: 'week4', 5: 'week5',
                6: 'week6', 7: 'week7', 8: 'week8',
                9: 'week9', 10: 'week10', 11: 'week11'}[week_id]
    return list(getattr(module, function)())


def build_curriculum():
    weeks = []
    lessons = []
    for week_id, title, subtitle, project, outcome in content_part1.WEEKS:
        weeks.append({'id': week_id, 'title': title, 'subtitle': subtitle,
                      'project': project, 'outcome': outcome})
        for lesson in lessons_for_week(week_id):
            lesson = dict(lesson)
            lesson['week'] = week_id
            lesson.setdefault('softwareIds', [])
            lesson.setdefault('equations', [])
            lesson.setdefault('commands', [])
            lesson.setdefault('labs', [])
            lesson.setdefault('glossary', [])
            lessons.append(lesson)
    return {
        'version': VERSION,
        'authors': AUTHORS,
        'weeks': weeks,
        'lessons': lessons,
        'tracks': TRACKS,
        'sources': SOURCES,
        'simulation': SIMULATION_NOTES,
    }


TRACKS = [
    {'id': 'robocon', 'title': 'RoboCON 电控主线', 'weeks': '01–11',
     'summary': '本课程的主线：从单台电机到一整台比赛机器人的电控系统设计。',
     'softwareIds': ['python-basics', 'can-tools', 'lab-toolchain'],
     'skills': ['电机拖动', 'FOC', 'CAN 通信', '运动学', '系统设计', '排障'],
     'projects': [
         {'weeks': '01–05', 'title': '单机闭环',
          'deliverable': '把一台电机的电流、速度、位置控制做稳，并用带宽与量化误差解释性能边界。'},
         {'weeks': '06–08', 'title': '通信与整机运动',
          'deliverable': '设计报文表、算出总线负载，让底盘沿规定路径走并给出跟踪误差。'},
         {'weeks': '09–11', 'title': '系统设计与交付',
          'deliverable': '完成电源预算、安全链、状态机与整机调试记录，形成可评审的设计包。'},
     ]},
    {'id': 'robomaster', 'title': 'RoboMaster 对照阅读', 'weeks': '按需',
     'summary': '对比赛用同样思路：云台/底盘的电调协议、CAN 报文风格与整定流程。',
     'softwareIds': ['can-tools', 'python-basics'],
     'skills': ['CAN 报文解析', '电调参数对照', '整定流程'],
     'projects': [
         {'weeks': '第 4 周后', 'title': '电调 FOC 参数对照',
          'deliverable': '把课程里的电流环整定结论与主流电调的可调参数一一对应起来。'},
         {'weeks': '第 6 周后', 'title': 'CAN 协议对照',
          'deliverable': '比较 RoboMaster 电调报文与自研协议在优先级、周期与负载率上的取舍。'},
     ]},
    {'id': 'industrial', 'title': '工业电机与拖动（进阶）', 'weeks': '按需',
     'summary': '把异步机的 V/f 与矢量控制扩展到工业变频器与伺服系统。',
     'softwareIds': ['lab-toolchain'],
     'skills': ['变频器参数', '矢量控制', 'PLC 通信'],
     'projects': [
         {'weeks': '扩展', 'title': '变频器参数整定',
          'deliverable': '用课程实验台复现变频器的加减速时间、载波频率与保护参数。'},
     ]},
]

SOURCES = [
    {'title': '《电机拖动》课程教材（电机与拖动基础）',
     'url': 'https://www.moe.gov.cn/',
     'note': '机械特性、拖动系统运动方程、异步机转矩—转差曲线的标准讲法。'},
    {'title': '《机器人技术》课程教材（机器人学导论方向）',
     'url': 'https://www.moe.gov.cn/',
     'note': '差速运动学、路径跟踪与坐标系变换的标准讲法。'},
    {'title': 'TI / ST 电机控制应用笔记（FOC 与 SVPWM 实现）',
     'url': 'https://www.ti.com/motor-drivers/overview.html',
     'note': '工程实现口径：电流环、调制比与死区补偿的实际做法。'},
    {'title': 'CiA 301 / CAN 标准帧格式说明',
     'url': 'https://www.can-cia.org/',
     'note': '报文位填充与帧开销的定量依据。'},
]

SIMULATION_NOTES = {
    'engine': 'server/sim/engine.py',
    'verification': 'tests/test_machines.py',
    'statement': (
        '所有曲线由本机实时求解电气—机械耦合微分方程得到，不含预存数据。'
        '每个模型都有与解析解或独立公式对照的自动测试；'
        '发散会如实停止并报告，不会给出被截断的"好看"结果。'),
    'boundaries': [
        '电机为平均值模型，不含齿槽转矩、磁路饱和与铁损。',
        '逆变器按"参考电压—母线限幅—占空比"处理，不做开关级仿真，不含死区补偿。',
        '电流没有内置保护模型：超限只给告警，保护逻辑是第 9–10 周的设计内容。',
        '热模型是一阶集总参数，仅用于建立量级直觉，不能用于热设计。',
    ],
}


def lesson_errors(lesson, software_ids):
    problems = []
    lid = lesson.get('id', '?')
    if not str(lid).startswith(f"w{lesson.get('week')}-"):
        problems.append(f'{lid}: 编号与周号不一致')
    for key in ('title', 'summary', 'deliverable'):
        if not lesson.get(key):
            problems.append(f'{lid}: 缺少 {key}')
    if len(lesson.get('objectives') or []) < 2:
        problems.append(f'{lid}: 目标少于 2 条')
    sections = lesson.get('sections') or []
    if len(sections) < 3:
        problems.append(f'{lid}: 正文段落少于 3 段（实际 {len(sections)}）')
    for index, section in enumerate(sections):
        extra = set(section) - {'title', 'body'}
        if extra:
            problems.append(f'{lid}: 第 {index + 1} 段有多余字段 {sorted(extra)}')
        if len(section.get('body', '')) < 120:
            problems.append(f'{lid}: 第 {index + 1} 段正文过短（{len(section.get("body", ""))} 字）')
    if len(lesson.get('equations') or []) < 2:
        problems.append(f'{lid}: 关系式少于 2 条')
    quiz = lesson.get('quiz') or []
    if len(quiz) < 2:
        problems.append(f'{lid}: 理解题少于 2 道')
    for index, item in enumerate(quiz):
        options = item.get('options') or []
        answer = item.get('answer')
        if len(options) < 3:
            problems.append(f'{lid}: 第 {index + 1} 题选项少于 3 个')
        if not isinstance(answer, int) or isinstance(answer, bool) or not 0 <= answer < len(options):
            problems.append(f'{lid}: 第 {index + 1} 题答案下标非法')
        if not item.get('explanation'):
            problems.append(f'{lid}: 第 {index + 1} 题缺少解析')
    for key in ('tasks', 'glossary'):
        if not lesson.get(key):
            problems.append(f'{lid}: 缺少 {key}')
    for software in lesson.get('softwareIds') or []:
        if software not in software_ids:
            problems.append(f'{lid}: 引用了不存在的工具 {software}')
    for lab in lesson.get('labs') or []:
        extra = set(lab) - {'id', 'title'}
        if extra:
            problems.append(f'{lid}: 实验条目有多余字段 {sorted(extra)}')
    return problems


def validate(curriculum, software):
    problems = []
    ids = [lesson['id'] for lesson in curriculum['lessons']]
    if len(ids) != len(set(ids)):
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        problems.append(f'单元编号重复：{duplicates}')
    week_ids = {week['id'] for week in curriculum['weeks']}
    if len(curriculum['weeks']) != 11:
        problems.append(f'周数应为 11，实际 {len(curriculum["weeks"])}')
    by_week = {}
    for lesson in curriculum['lessons']:
        by_week.setdefault(lesson.get('week'), []).append(lesson)
        if lesson.get('week') not in week_ids:
            problems.append(f"{lesson['id']}: 周号 {lesson.get('week')} 不在周表里")
    for week_id in week_ids:
        items = by_week.get(week_id, [])
        if len(items) != 4:
            problems.append(f'第 {week_id} 周有 {len(items)} 个单元（应为 4）')
    for lesson in curriculum['lessons']:
        problems.extend(lesson_errors(lesson, {item['id'] for item in software}))
    # 实验引用的预设必须真的存在（和 server/app.py 的 _PRESETS 对齐）
    from server.app import _PRESETS
    preset_ids = {preset['id'] for preset in _PRESETS}
    for lesson in curriculum['lessons']:
        for lab in lesson.get('labs') or []:
            if lab.get('id') not in preset_ids:
                problems.append(f"{lesson['id']}: 实验预设 {lab.get('id')} 不在服务端预设表里")
    known_software = {item['id'] for item in software}
    for track in curriculum['tracks']:
        for software_id in track.get('softwareIds') or []:
            if software_id not in known_software:
                problems.append(f"路线 {track['id']}: 引用了不存在的工具 {software_id}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成课程内容 JSON')
    parser.add_argument('--check', action='store_true', help='生成后做一致性校验')
    parser.add_argument('--out', default=str(ROOT / 'course'))
    args = parser.parse_args(argv)

    import build_software

    course_dir = Path(args.out)
    course_dir.mkdir(parents=True, exist_ok=True)

    software = build_software.build()
    curriculum = build_curriculum()

    problems = validate(curriculum, software)
    if problems:
        print(f'内容校验发现 {len(problems)} 个问题：', file=sys.stderr)
        for problem in problems[:60]:
            print(f'  - {problem}', file=sys.stderr)
        if len(problems) > 60:
            print(f'  …… 还有 {len(problems) - 60} 条', file=sys.stderr)
        if args.check:
            return 1

    (course_dir / 'curriculum.json').write_text(
        json.dumps(curriculum, ensure_ascii=False, indent=1), encoding='utf-8')
    (course_dir / 'software.json').write_text(
        json.dumps(software, ensure_ascii=False, indent=1), encoding='utf-8')

    total_quiz = sum(len(lesson.get('quiz') or []) for lesson in curriculum['lessons'])
    print(f'已生成 {course_dir}/curriculum.json：{len(curriculum["weeks"])} 周 / '
          f'{len(curriculum["lessons"])} 单元 / {total_quiz} 道理解题')
    print(f'已生成 {course_dir}/software.json：{len(software)} 个工具条目')
    if problems:
        print('（上面列出了内容问题，请修正后重跑 --check）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

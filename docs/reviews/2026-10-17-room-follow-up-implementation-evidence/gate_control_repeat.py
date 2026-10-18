"""门禁对照：指定场景各重复 N 次，只输出判定与正文前缀；虚构资料，不写库不发消息。

用法：在 API 容器的临时目录内、候选源码在 PYTHONPATH 时，
python gate_control_repeat.py '场景1,场景2' 6
"""

import asyncio
import json
import sys
from pathlib import Path

from homestay_bot.tools import reply_regression as rr

fixture = json.loads(Path("fixture.json").read_text(encoding="utf-8"))
ids, n = sys.argv[1].split(","), int(sys.argv[2])


async def main() -> None:
    """每个场景并发跑 N 次，打印通过次数与各次路由。"""
    runner = rr._Runner(fixture, await rr._load_runtime_snapshot())
    for sid in ids:
        sc = next(s for s in fixture["scenarios"] if s["id"] == sid)
        recs = await asyncio.gather(*[runner.run(sc) for _ in range(n)])
        oks = [rr.judge(sc, r, fixture.get("global_must_not_include", []))["ok"] for r in recs]
        print("RESULT", sid, sum(oks), "/", n, [r.get("route") for r in recs])


asyncio.run(main())

"""门禁对照：指定场景各重复 N 次，只输出判定与正文前缀；虚构资料，不写库不发消息。"""
import asyncio, json, sys
from homestay_bot.tools import reply_regression as rr
fixture = json.load(open("fixture.json", encoding="utf-8"))
ids, n = sys.argv[1].split(","), int(sys.argv[2])
async def main():
    runner = rr._Runner(fixture, await rr._load_runtime_snapshot())
    for sid in ids:
        sc = next(s for s in fixture["scenarios"] if s["id"] == sid)
        recs = await asyncio.gather(*[runner.run(sc) for _ in range(n)])
        oks = [rr.judge(sc, r, fixture.get("global_must_not_include", []))["ok"] for r in recs]
        print("RESULT", sid, sum(oks), "/", n, [r.get("route") for r in recs])
asyncio.run(main())

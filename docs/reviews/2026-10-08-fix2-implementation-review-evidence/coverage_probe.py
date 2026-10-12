"""离线复现 fix2 覆盖缺陷；退出 0 表示当前反例成立，不表示实现通过。"""
import asyncio
import json
from datetime import date

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import Language
from homestay_bot.domain.models import Base, KnowledgeEntry
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.services.item_coverage import clause_rooms
from homestay_bot.services.knowledge_service import KnowledgeService
from tests.integration.test_knowledge_referrals import _Assistant, _jobs, _run_final, _seed
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _availability_row,
    _decision,
    _Knowledge,
    _respond,
    _snippet,
    _tool_message,
    _Tools,
)

ARGS = {'check_in_date': '2026-10-10', 'check_out_date': '2026-10-11'}


async def show(label, text, decision):
    """输出最终正文、分项与缺口，而非中间模型输出。"""
    engine = create_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        await _seed(factory, msgid=label, content=text)
        from tests.integration.test_knowledge_referrals import _guest
        await _run_final(factory, _Assistant(decision), _guest(label, text))
        replies = await _jobs(factory, 'wecom_send_text')
        assert len(replies) == 1
        client_body = replies[0].payload['content']
        assert ('7:30' in client_body) == ('7:30' in decision.reply_text)
    finally:
        await engine.dispose()
    print(json.dumps({
        'client_body': client_body, 'actual_route': 'wecom_send_text/final',
        'case': label, 'question': text, 'rooms': clause_rooms(text),
        'reply': decision.reply_text,
        'parts': [{'status': p.status, 'question': p.question,
                   'evidence': [{'id': e.source_id, 'date': e.target_date}
                                for e in p.evidence]} for p in decision.reply_parts],
        'gaps': [{'reason': g.reason, 'text': g.text} for g in decision.gap_units],
    }, ensure_ascii=False, default=str))


async def answer(text, tool_name, rows, knowledge, *, room=None, fallback=False):
    """使用本地核验过的粗粒度计划和脚本响应运行真实适配器。"""
    fields = dict(ARGS)
    if room:
        fields['target_room'] = room
    plan = plan_for(text, ('stay_query', text, fields))
    assert plan.status == 'ok'
    assistant, _ = _assistant(
        [_tool_message(tool_name, ARGS), '不是JSON' if fallback else _decision()],
        knowledge, _Tools({tool_name: rows}),
    )
    return await _respond(assistant, text, plan)


async def main():
    """三组正反对照；日期反例用真实 SQLite、知识仓储和检索。"""
    availability = [_availability_row(1401, '2026-10-10', '2026-10-11')]
    plain = '10月10日401房间住一晚有房吗'
    iso = '2026-10-10 401房间住一晚有房吗'
    for label, text in [('iso_control', plain), ('iso_bug', iso)]:
        d = await answer(text, 'search_availability', availability,
                         _Knowledge([], {'401': 1401}), room='401')
        await show(label, text, d)
        assert bool(d.gap_units) == (label == 'iso_bug')
    assert clause_rooms(iso) == ('2026', '401')

    breakfast = _snippet(6001, '早餐几点开始？', '早餐7:30开始。', category='早餐')
    prices = [{'property_id': 1401, 'property_title': '401', **ARGS,
               'nightly_reference_prices': [{'date': '2026-10-10', 'price': 300}]}]
    for label, joiner in [('residue_control', '，'), ('residue_bug', '和')]:
        text = f'10月10日401房间住一晚多少钱{joiner}早餐几点开始'
        d = await answer(text, 'search_reference_price', prices,
                         _Knowledge([breakfast], {'401': 1401}), room='401')
        await show(label, text, d)
        assert ('7:30' in d.reply_text) == (label == 'residue_control')
        assert bool(d.gap_units) == (label == 'residue_bug')

    engine = create_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        async with factory() as session:
            session.add(KnowledgeEntry(
                id=6001, category='早餐', scope='global', is_enabled=True,
                question_zh='早餐几点开始？', answer_zh='早餐7:30开始。',
                question_en='When is breakfast?', answer_en='Breakfast starts at 7:30.',
                keywords=['早餐'], valid_from=date(2026, 10, 10),
                valid_until=date(2026, 10, 10),
            ))
            await session.commit()
            knowledge = KnowledgeService(SQLAlchemyKnowledgeRepository(session))
            for label, static_day, fallback in [
                ('date_control', '10月10日', False),
                ('date_bug', '10月11日', False),
                ('date_fallback_bug', '10月11日', True),
            ]:
                text = f'10月10日有房吗，{static_day}早餐几点开始'
                d = await answer(text, 'search_availability', availability,
                                 knowledge, fallback=fallback)
                await show(label, text, d)
                assert '7:30' in d.reply_text and not d.gap_units
                assert any(e.target_date == date(2026, 10, 10)
                           for p in d.reply_parts for e in p.evidence
                           if e.source_kind == 'knowledge')
            found = await knowledge.retrieve(
                Language.ZH, '10月11日早餐几点开始', target_date=date(2026, 10, 11)
            )
            assert found == []
            print(json.dumps({'direct_oct11_retrieval': len(found)}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

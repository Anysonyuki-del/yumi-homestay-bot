"""复现部署后可用性审查中的本地缺陷；仅使用测试替身，不访问生产。"""

import json
import re
from html import unescape
from types import MethodType
from urllib.parse import quote

from test_task_routes import BusinessTaskStatus, EmployeeRole, build_client, login

from homestay_bot.services.room_readiness_service import ReadinessRuleError
from homestay_bot.services.runtime_config_service import UpdateRuntimeConfig


def csrf(html):
    """从测试页面读取一次性令牌，不读取真实用户会话。"""
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def back_link(html):
    """提取详情页返回目标，用于比对提交前后的筛选上下文。"""
    return unescape(re.search(r'href="([^"]+)">返回任务列表', html).group(1))


def main():
    """输出三个当前缺陷的最小证据；断言用于提示修复后更新报告。"""
    with build_client(EmployeeRole.ADMIN)[0] as client:
        login(client)
        source = '/employee/tasks?property_id=101&status_filter=pending_confirmation&page=2'
        before = client.get('/employee/tasks/1?return_to=' + quote(source, safe=''))
        result = client.post('/employee/tasks/1/assign', data={
            'csrf_token': csrf(before.text), 'assigned_employee_id': 2,
            'property_id': 101, 'service_date': '2026-09-08',
        }, follow_redirects=False)
        after = client.get(result.headers['location'])
        assert back_link(before.text) == source
        assert back_link(after.text) == '/employee/tasks'
        print(json.dumps({'id': 'UX-02', 'before': back_link(before.text),
                          'redirect': result.headers['location'],
                          'after': back_link(after.text)}, ensure_ascii=False))

    client, stub = build_client(EmployeeRole.ADMIN)
    with client:
        login(client)
        stub.item.status = BusinessTaskStatus.PENDING_INSPECTION
        stub.item.assigned_employee_id = 1

        async def missing_photo(self, task_id, employee):
            """模拟实际领域错误，避免触发可入住后的凭证评估或外联。"""
            raise ReadinessRuleError('至少需要一张有效现场照片')

        stub.mark_ready = MethodType(missing_photo, stub)
        detail = client.get('/employee/tasks/1')
        result = client.post('/employee/tasks/1/ready',
                             data={'csrf_token': csrf(detail.text)},
                             headers={'accept': 'text/html'}, follow_redirects=False)
        assert result.status_code == 409
        assert result.json() == {'detail': '任务操作未完成'}
        print(json.dumps({'id': 'UX-01', 'status': result.status_code,
                          'content_type': result.headers['content-type'],
                          'body': result.json()}, ensure_ascii=False))

    updates = UpdateRuntimeConfig(hostex_webhook_secret_token='').normalized_updates()
    assert 'hostex_webhook_secret_token' not in updates
    print(json.dumps({'id': 'UX-07', 'blank_secret_updates': updates}, ensure_ascii=False))


if __name__ == '__main__':
    main()

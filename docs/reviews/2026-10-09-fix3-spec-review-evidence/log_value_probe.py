"""只在进程内模拟 fix3 的可选引号键修订，核对实际 handler 的值边界。"""
import io
import json
import logging
import re

from homestay_bot import logging as policy

# 原值规则原样保留，只按 Spec 2.2 给键添加可选引号；这不是已实施版本。
policy._SECRET_ASSIGNMENT_PATTERN = re.compile(
    r'''(?i)(["']?[\w.-]*(?:token|secret|password|api[_-]?key|aes[_-]?key|authorization)["']?)'''
    r'(\s*[=:]\s*)'
    r'(?!\[REDACTED\])'
    r'(?:(?:Bearer|Basic|Token)\s+)?'
    r'''(?:"[^"]*"|'[^']*'|[^\s,;)\]}&]+)'''
)

stream = io.StringIO()
handler = logging.StreamHandler(stream)
handler.addFilter(policy.SensitiveDataFilter())
logger = logging.getLogger('synthetic.fix3.review')
logger.handlers = [handler]
logger.propagate = False
logger.setLevel(logging.INFO)
logger.info('%s', json.dumps({'Authorization': 'Bearer SYNTHETIC_CONTROL_TOKEN'}))
control = stream.getvalue()
assert 'SYNTHETIC_CONTROL_TOKEN' not in control
assert policy.redact_text(control) == control
stream.seek(0)
stream.truncate(0)
secret = 'A1!"SYNTHETIC_JSON_SUFFIX'
logger.info('%s', json.dumps({'password': secret}))
logger.warning('fields=%s status=%s', {'password': "A1!'\"SYNTHETIC_REPR_SUFFIX"}, 401)
actual = stream.getvalue()
assert 'SYNTHETIC_JSON_SUFFIX' in actual
assert 'SYNTHETIC_REPR_SUFFIX' in actual
print('CONTROL quoted-key Bearer removed, repeated filtering idempotent')
print('PROXY_LOG_OUTPUT', actual.strip())
print('Spec prefix-only proxy still leaks escaped quoted value suffixes; '
      'not an implemented fix3 test.')

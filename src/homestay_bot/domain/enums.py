from enum import StrEnum


class ConversationMode(StrEnum):
    """表示当前会话由机器人还是人工处理。"""

    BOT_ACTIVE = "bot_active"
    HUMAN_ACTIVE = "human_active"


class ApprovalStatus(StrEnum):
    """表示预订审批从收集到落单的确定状态。"""

    PENDING = "pending"
    CREATING = "creating"
    BOOKED = "booked"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    NEEDS_REVIEW = "needs_review"


class EmployeeRole(StrEnum):
    """定义内部员工在审批与知识管理中的权限。"""

    CUSTOMER_SERVICE = "customer_service"
    BOOKING_APPROVER = "booking_approver"
    ADMIN = "admin"


class Language(StrEnum):
    """定义机器人第一期支持的语言。"""

    ZH = "zh"
    EN = "en"


class MessageOrigin(StrEnum):
    """区分消息来自客人、人工客服还是机器人。"""

    GUEST = "guest"
    SERVICER = "servicer"
    BOT = "bot"


class JobStatus(StrEnum):
    """表示持久化后台任务的执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class KnowledgeCandidateStatus(StrEnum):
    """表示高频 FAQ 候选是否继续统计或已结束处理。"""

    OPEN = "open"
    SNOOZED = "snoozed"
    CONVERTED = "converted"


class KnowledgeCandidateDraftStatus(StrEnum):
    """表示 FAQ 参考草稿的生成状态。"""

    NONE = "none"
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"

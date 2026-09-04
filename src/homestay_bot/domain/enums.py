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
    """定义一期内部员工的两级权限。"""

    STAFF = "staff"
    ADMIN = "admin"


class CustomerIdentityProvider(StrEnum):
    """定义客户身份来自哪个经过验证的外部渠道。"""

    WECOM_KF = "wecom_kf"
    WECOM_CONTACT = "wecom_contact"
    WECHAT_UNIONID = "wechat_unionid"
    HOSTEX = "hostex"


class CustomerMergeStatus(StrEnum):
    """表示客户合并建议是否已经过管理员判断。"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CustomerMemoryCategory(StrEnum):
    """定义允许长期保存的客户记忆类别。"""

    PREFERENCE = "preference"
    CONFIRMED_FACT = "confirmed_fact"
    UNRESOLVED = "unresolved"
    SERVICE_HISTORY = "service_history"


class CustomerMemoryStatus(StrEnum):
    """定义结构化客户记忆从候选到失效的治理状态。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISPUTED = "disputed"
    STALE = "stale"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class CustomerMemoryEvidenceType(StrEnum):
    """区分记忆来自客户明示、员工确认还是模型推断。"""

    USER_EXPLICIT = "user_explicit"
    EMPLOYEE_CONFIRMED = "employee_confirmed"
    MODEL_INFERENCE = "model_inference"


class BusinessTaskType(StrEnum):
    """定义一期允许进入任务中心的业务事项。"""

    CLEANING = "cleaning"
    MAINTENANCE = "maintenance"
    SUPPLIES = "supplies"
    SPECIAL_SERVICE = "special_service"
    EARLY_CHECK_IN = "early_check_in"
    LATE_CHECK_OUT = "late_check_out"
    MANUAL_CONTACT = "manual_contact"


class BusinessTaskStatus(StrEnum):
    """定义业务任务从建议到完成的受控状态。"""

    PENDING_CONFIRMATION = "pending_confirmation"
    PENDING_ASSIGNMENT = "pending_assignment"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    PENDING_INSPECTION = "pending_inspection"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ARCHIVABLE_TASK_STATUSES = (
    BusinessTaskStatus.COMPLETED,
    BusinessTaskStatus.CANCELLED,
    BusinessTaskStatus.EXPIRED,
)
"""可归档的任务终态。

归档只影响列表可见性、不改变任务状态，因此只有已经走完流程的任务才允许归档；
开放中的任务一旦可归档，就等于提供了「把没做的活藏起来」的入口。
仓储用它做强制校验，页面用它决定是否给出勾选，两处必须同源。
"""


class BusinessTaskOrigin(StrEnum):
    """标记任务来自订单周转、提醒失败、模型建议或历史未知来源。"""

    TURNOVER = "turnover"
    LIFECYCLE_REMINDER = "lifecycle_reminder"
    AI_SUGGESTION = "ai_suggestion"
    UNKNOWN = "unknown"


class TaskClosureReason(StrEnum):
    """记录任务失去业务价值的确定性原因。"""

    ORDER_CANCELLED = "order_cancelled"
    WINDOW_EXPIRED = "window_expired"
    SUPERSEDED = "superseded"


class TaskClosureSource(StrEnum):
    """区分任务由系统、员工还是历史治理批次关闭。"""

    SYSTEM = "system"
    EMPLOYEE = "employee"
    MIGRATION = "migration"


class RoomOperationalStatus(StrEnum):
    """定义房间保洁、检查、入住和维修状态。"""

    NOT_STARTED = "not_started"
    CLEANING = "cleaning"
    PENDING_INSPECTION = "pending_inspection"
    READY = "ready"
    OCCUPIED = "occupied"
    MAINTENANCE = "maintenance"


class RoomOccupancyStatus(StrEnum):
    """表示由百居易订单事实推导出的房间近期入住状态。"""

    UNKNOWN = "unknown"
    VACANT = "vacant"
    ARRIVING_TODAY = "arriving_today"
    OCCUPIED = "occupied"
    DEPARTING_TODAY = "departing_today"
    TURNOVER_TODAY = "turnover_today"


class CredentialDeliveryStatus(StrEnum):
    """定义入住凭证整体或单个部件的投递状态。"""

    PENDING = "pending"
    SENT = "sent"
    NEEDS_REVIEW = "needs_review"
    MANUAL_FOLLOWUP = "manual_followup"
    CANCELLED = "cancelled"


class ReminderType(StrEnum):
    """定义一期入住生命周期的四类主动提醒。"""

    PRE_ARRIVAL = "pre_arrival"
    ARRIVAL_DAY = "arrival_day"
    CHECKOUT = "checkout"
    THANK_YOU = "thank_you"


class ReminderStatus(StrEnum):
    """区分计划、平台受理和需要人工跟进，绝不虚构已送达。"""

    SCHEDULED = "scheduled"
    PLATFORM_ACCEPTED = "platform_accepted"
    MANUAL_FOLLOWUP = "manual_followup"
    CANCELLED = "cancelled"


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


class RuntimeConfigVersionStatus(StrEnum):
    """描述配置候选从待测试到测试或激活冲突的生命周期。"""

    CANDIDATE = "candidate"
    TEST_FAILED = "test_failed"
    TEST_PASSED = "test_passed"
    ACTIVATION_CONFLICT = "activation_conflict"
    ACTIVATION_FAILED = "activation_failed"


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


class ComplaintReviewStatus(StrEnum):
    """定义客诉分析、人工编辑和实际投递状态。"""

    PENDING_ANALYSIS = "pending_analysis"
    READY_FOR_REVIEW = "ready_for_review"
    EDITING = "editing"
    SEND_QUEUED = "send_queued"
    DELIVERY_FAILED = "delivery_failed"
    SENT = "sent"
    RETURNED = "returned"
    ANALYSIS_FAILED = "analysis_failed"
    CANCELLED = "cancelled"

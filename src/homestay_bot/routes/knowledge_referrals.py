"""资料核实后台视图（修复 Spec 2026-10-08 fix2 §2.6(3)(5)），仅管理员，沿用知识库登录与 CSRF。

列表显示待处理数量与「管家已回复 N 条」；详情并列展示净化后的原问、房源、管家原文、草稿与
依据片段。操作：整理草稿、入库（必须点选范围并勾选「已核对」）、关闭不入库（30 天抑制）。
"""

from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from homestay_bot.domain.enums import KnowledgeReferralStatus
from homestay_bot.routes.knowledge import (
    KnowledgeFormError,
    _consume_csrf,
    _fields,
    _form_error_response,
    _get_service,
    _issue_csrf,
    _require_admin,
    _word_list,
)
from homestay_bot.web import pop_page_notice, set_page_notice, templates

router = APIRouter(prefix="/employee/knowledge-referrals")

# 页面状态说明：如实区分「模型已整理」与「管理员已确认」（Spec §2.6(4)）。
STATUS_LABELS = {
    KnowledgeReferralStatus.PENDING: ("待处理", "warning"),
    KnowledgeReferralStatus.DRAFTING: ("整理中", "info"),
    KnowledgeReferralStatus.DRAFTED: ("模型已整理，待管理员确认", "info"),
    KnowledgeReferralStatus.NO_ANSWER: ("没找到答案", "neutral"),
    KnowledgeReferralStatus.DRAFT_FAILED: ("草稿失败", "danger"),
    KnowledgeReferralStatus.CONVERTED: ("已入库", "success"),
    KnowledgeReferralStatus.CLOSED: ("已关闭（不入库）", "neutral"),
    KnowledgeReferralStatus.CLEARED: ("已清空", "neutral"),
}


def _detail_url(referral_id: int) -> str:
    """详情页地址。"""
    return f"/employee/knowledge-referrals/{referral_id}"


@router.get("", response_class=HTMLResponse)
async def referral_index(
    request: Request, page: int = Query(default=1, ge=1, le=10000),
) -> Response:
    """开放记录优先，按每页 50 条读取；额外一条只判断是否还有下一页。"""
    await _require_admin(request)
    service = _get_service(request)
    items = await service.list_referrals(limit=51, offset=(page - 1) * 50)
    return templates.TemplateResponse(
        request=request,
        name="knowledge/referrals.html",
        context={
            "items": items[:50],
            "page": page,
            "previous_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if len(items) > 50 and page < 10000 else None,
            "open_count": await service.count_open_referrals(),
            "status_labels": STATUS_LABELS,
            "notice": pop_page_notice(request) or None,
            "page_title": "资料核实",
            "active_nav": "knowledge",
        },
    )


@router.get("/{referral_id}", response_class=HTMLResponse)
async def referral_detail(request: Request, referral_id: int) -> Response:
    """资料核实详情。"""
    return await _render_detail(request, referral_id)


async def _render_detail(
    request: Request,
    referral_id: int,
    *,
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
    submitted: SimpleNamespace | None = None,
) -> Response:
    """渲染详情；入库失败时回填刚提交的内容（失败保留输入）。"""
    await _require_admin(request)
    service = _get_service(request)
    try:
        detail = await service.referral_detail(referral_id)
    except LookupError as error_:
        raise HTTPException(status_code=404, detail="资料核实不存在") from error_
    return templates.TemplateResponse(
        request=request,
        name="knowledge/referral_detail.html",
        status_code=status_code,
        context={
            "detail": detail,
            "referral": detail.referral,
            "draft": detail.referral.draft or {},
            "submitted": submitted,
            "properties": await service.list_properties(),
            "status_labels": STATUS_LABELS,
            "statuses": KnowledgeReferralStatus,
            "csrf_token": await _issue_csrf(request),
            "error": error,
            "notice": pop_page_notice(request) or None,
            "page_title": f"资料核实 #{referral_id}",
            "active_nav": "knowledge",
        },
    )


def _redirect_detail(referral_id: int) -> RedirectResponse:
    """写操作后回到详情页。"""
    return RedirectResponse(_detail_url(referral_id), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{referral_id}/draft")
async def start_draft(
    request: Request,
    referral_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> Response:
    """管理员点「整理草稿」。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    try:
        await _get_service(request).start_referral_draft(referral_id, employee_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="资料核实不存在") from error
    except KnowledgeFormError as error:
        _form_error_response(request, error)
        return await _render_detail(
            request, referral_id, status_code=error.status_code, error=str(error)
        )
    set_page_notice(request, "已开始整理草稿，稍后刷新查看结果。")
    return _redirect_detail(referral_id)


@router.post("/{referral_id}/convert")
async def convert(
    request: Request,
    referral_id: int,
    generation: int = Form(ge=0),
    scope_choice: str = Form("", max_length=16),
    property_id: str = Form("", max_length=20),
    category: str = Form(min_length=1, max_length=64),
    question_zh: str = Form(min_length=1, max_length=500),
    answer_zh: str = Form(min_length=1, max_length=10_000),
    question_en: str = Form(min_length=1, max_length=500),
    answer_en: str = Form(min_length=1, max_length=10_000),
    keywords: str = Form("", max_length=1000),
    valid_from: str = Form("", max_length=10),
    valid_until: str = Form("", max_length=10),
    reviewed: str = Form("", max_length=8),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> Response:
    """核对后入库：范围没有默认值，「已核对」由服务端校验，直接 POST 未勾选也被拒。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    raw: dict[str, Any] = {
        "category": category, "question_zh": question_zh, "answer_zh": answer_zh,
        "question_en": question_en, "answer_en": answer_en, "keywords": keywords,
        "valid_from": valid_from, "valid_until": valid_until,
    }
    submitted = SimpleNamespace(
        **raw,
        keywords_list=_word_list(keywords) or [],
        scope_choice=scope_choice,
        property_id=int(property_id) if property_id.strip().isdigit() else None,
        reviewed=reviewed == "1",
    )
    try:
        if scope_choice not in {"property", "global"}:
            raise KnowledgeFormError("请选择「仅此房间」或「全店」")
        if scope_choice == "property" and not property_id.strip():
            raise KnowledgeFormError("选择「仅此房间」时请选定房间")
        fields = _fields(
            **raw,
            scope=scope_choice,
            property_id=property_id if scope_choice == "property" else "",
        )
        await _get_service(request).convert_referral(
            referral_id,
            employee_id,
            generation=generation,
            reviewed=reviewed == "1",
            **fields,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="资料核实不存在") from error
    except KnowledgeFormError as error:
        _form_error_response(request, error)
        return await _render_detail(
            request,
            referral_id,
            status_code=error.status_code,
            error=str(error),
            submitted=submitted,
        )
    set_page_notice(request, "已入库并启用。")
    return _redirect_detail(referral_id)


@router.post("/{referral_id}/close")
async def close(
    request: Request,
    referral_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> Response:
    """关闭不入库：同一问题与房源 30 天内不再转交。"""
    employee_id = await _require_admin(request)
    await _consume_csrf(request, csrf_token)
    try:
        await _get_service(request).close_referral(referral_id, employee_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="资料核实不存在") from error
    except KnowledgeFormError as error:
        _form_error_response(request, error)
        return await _render_detail(
            request, referral_id, status_code=error.status_code, error=str(error)
        )
    set_page_notice(request, "已关闭，30 天内同一问题不再转交管家。")
    return _redirect_detail(referral_id)

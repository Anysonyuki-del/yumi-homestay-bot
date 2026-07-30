from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from homestay_bot.domain.models import Employee
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.services.private_file_storage import StoredPrivateFile

router = APIRouter(prefix="/employee/private-files")


class PrivateFilePageServicePort(Protocol):
    """定义私有附件下载所需的授权服务。"""

    async def file_for(
        self,
        file_id: str,
        employee: Employee,
    ) -> StoredPrivateFile:
        """授权后返回服务器私有文件。"""


def _get_service(request: Request) -> PrivateFilePageServicePort:
    """从应用状态读取私有附件授权服务。"""
    service = getattr(request.app.state, "task_page_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="任务服务尚未配置")
    return cast(PrivateFilePageServicePort, service)


async def _current_employee(request: Request) -> Employee:
    """把持续复核后的签名会话转换为最小员工对象。"""
    employee_id, role = await require_employee_session(request)
    return Employee(
        id=employee_id,
        wecom_userid="",
        name="",
        role=role,
        is_active=True,
    )


@router.get("/{file_id}")
async def download_private_file(request: Request, file_id: str) -> Response:
    """仅向有权查看关联任务的员工返回私有照片。"""
    employee = await _current_employee(request)
    try:
        stored = await _get_service(request).file_for(file_id, employee)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    response = FileResponse(
        stored.path,
        media_type=stored.content_type,
        filename=None,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

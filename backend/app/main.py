"""Single-process API, Bot intake and durable queue runtime."""
import asyncio
import secrets
import shutil
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from filelock import FileLock, Timeout
from pydantic import ValidationError
from sqlalchemy import delete, select
from starlette.exceptions import HTTPException

from app.bot.client import TelegramClient
from app.core.config import Config
from app.core.errors import AppError
from app.core.security import Secrets, digest, new_token, password_hasher, private_file, verify_password
from app.db.models import Admin, MediaRequest, Session
from app.db.session import Database
from app.schemas.contracts import (
    Credentials,
    LocalApiRetry,
    PasswordChange,
    Retry,
    SettingsPatch,
    Setup,
    TaskDelete,
    UserSettings,
)
from app.schemas.responses import (
    CurrentUser,
    DiagnosticLogStatus,
    ErrorResponse,
    LocalApiStatus,
    RequestSafetyStatus,
    SettingsResponse,
    SetupStatus,
    SystemStatus,
    TaskDeleteResponse,
    TaskDetail,
    TaskPage,
    ValidationResponse,
)
from app.services.settings import SettingsService
from app.services.tasks import ACTIVE, TaskService
from app.storage.files import StorageManager
from app.workers.runtime import Runtime


def migrate(db: Database) -> None:
    config = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with db.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or Config()
    db: Database
    settings: SettingsService
    tasks: TaskService
    storage: StorageManager
    runtime: Runtime
    crypto: Secrets
    attempts: dict[str, deque[float]] = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal db, settings, tasks, storage, runtime, crypto
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        lock = FileLock(cfg.data_dir / "instance.lock")
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise RuntimeError("Only one TGD instance may use this data directory") from exc
        try:
            db = Database(cfg.database_url)
            await asyncio.to_thread(migrate, db)
            crypto = Secrets(cfg.data_dir)
            settings = SettingsService(db, cfg, crypto)
            tasks = TaskService(db, settings)
            storage = StorageManager(cfg.storage_roots, cfg.data_dir, cfg.minimum_free_bytes)
            runtime = Runtime(db, cfg, settings, tasks, storage)
            with db.sessions() as session:
                initialized = session.get(Admin, 1) is not None
            bootstrap = cfg.data_dir / "bootstrap.key"
            if not initialized and not bootstrap.exists():
                private_file(bootstrap, new_token().encode())
            application.state.db, application.state.settings = db, settings
            application.state.tasks, application.state.runtime = tasks, runtime
            application.state.storage = storage
            application.state.ready = True
            await asyncio.to_thread(runtime.maintain_diagnostics)
            runtime.diagnostics.emit("service_started", level="info")
            if cfg.run_background:
                await runtime.start()
            yield
        finally:
            application.state.ready = False
            if "runtime" in application.state._state and cfg.run_background:
                await runtime.close()
            if "runtime" in application.state._state:
                runtime.diagnostics.flush()
                runtime.diagnostics.emit("service_stopped", level="info")
            if "db" in application.state._state:
                db.engine.dispose()
            lock.release()

    application = FastAPI(title="Telegram 媒体下载助手", version="0.1.0", lifespan=lifespan,
                          responses={code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 422, 429, 500)})

    @application.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        request.state.request_id = secrets.token_hex(8)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                origin_parts = urlsplit(origin)
                if (origin_parts.scheme, origin_parts.netloc) != (request.url.scheme, request.url.netloc):
                    return error_response(request, AppError("INVALID_ORIGIN", "请求来源不受信任", 403))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def error_response(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": {
            "code": exc.code, "message": exc.message, "request_id": getattr(request.state, "request_id", ""),
            "details": exc.details}})

    @application.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request, exc)

    @application.exception_handler(RequestValidationError)
    @application.exception_handler(ValidationError)
    async def validation_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, AppError("VALIDATION_ERROR", "参数格式或范围不正确", 422))

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, AppError("INTERNAL_ERROR", "操作失败，请检查系统状态后重试", 500))

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(request, AppError("HTTP_ERROR", "请求的接口或方法不存在", exc.status_code))

    def auth(request: Request) -> dict[str, str]:
        token = request.cookies.get("session", "")
        with db.sessions() as session:
            row = session.get(Session, digest(token)) if token else None
            admin = session.get(Admin, 1)
            if not row or not admin or row.expires_at <= time.time():
                raise AppError("UNAUTHORIZED", "请登录后继续", 401)
            if request.method not in {"GET", "HEAD"}:
                if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), row.csrf_token):
                    raise AppError("CSRF_FAILED", "安全校验失败，请刷新页面", 403)
            return {"username": admin.username, "csrf_token": row.csrf_token}

    @application.get("/api/v1/health/live")
    @application.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/v1/health/ready")
    @application.get("/health/ready")
    def ready() -> dict[str, str]:
        if not getattr(application.state, "ready", False):
            raise AppError("NOT_READY", "服务初始化中", 503)
        return {"status": "ok"}

    @application.get("/api/v1/setup/status", response_model=SetupStatus)
    def setup_status() -> dict[str, bool]:
        with db.sessions() as session:
            return {"initialized": session.get(Admin, 1) is not None}

    @application.post("/api/v1/setup", response_model=SetupStatus)
    def setup(payload: Setup) -> dict[str, bool]:
        bootstrap = cfg.data_dir / "bootstrap.key"
        with db.transaction() as session:
            if session.get(Admin, 1):
                raise AppError("ALREADY_INITIALIZED", "管理员已初始化", 409)
            if not bootstrap.exists() or not secrets.compare_digest(bootstrap.read_text().strip(), payload.bootstrap_secret):
                raise AppError("INVALID_BOOTSTRAP", "初始化密钥不正确", 403)
            session.add(Admin(id=1, username=payload.username, password_hash=password_hasher.hash(payload.password)))
        bootstrap.unlink(missing_ok=True)
        return {"initialized": True}

    @application.post("/api/v1/auth/login", response_model=CurrentUser)
    def login(payload: Credentials, request: Request, response: Response) -> dict[str, str]:
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        recent = attempts[key]
        while recent and recent[0] < now - 300:
            recent.popleft()
        if len(recent) >= 10:
            raise AppError("RATE_LIMITED", "登录尝试过多，请 5 分钟后重试", 429)
        recent.append(now)
        with db.sessions() as session:
            admin = session.get(Admin, 1)
            if not admin or admin.username != payload.username or not verify_password(admin.password_hash, payload.password):
                raise AppError("INVALID_CREDENTIALS", "用户名或密码不正确", 401)
        token, csrf = new_token(), new_token()
        with db.transaction() as session:
            session.execute(delete(Session).where(Session.expires_at < time.time()))
            session.add(Session(token_hash=digest(token), csrf_token=csrf, expires_at=time.time() + 86400))
        recent.clear()
        response.set_cookie("session", token, httponly=True, secure=cfg.cookie_secure, samesite="strict", max_age=86400, path="/")
        return {"username": payload.username, "csrf_token": csrf}

    @application.get("/api/v1/auth/me", response_model=CurrentUser)
    def me(user: Annotated[dict[str, str], Depends(auth)]) -> dict[str, str]:
        return user

    @application.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response, user: Annotated[dict[str, str], Depends(auth)]) -> dict[str, bool]:
        with db.transaction() as session:
            session.execute(delete(Session).where(Session.token_hash == digest(request.cookies.get("session", ""))))
        response.delete_cookie("session", path="/")
        return {"ok": True}

    @application.post("/api/v1/auth/password")
    def change_password(payload: PasswordChange, response: Response,
                        user: Annotated[dict[str, str], Depends(auth)]) -> dict[str, bool]:
        with db.transaction() as session:
            admin = session.get(Admin, 1)
            assert admin
            if not verify_password(admin.password_hash, payload.current_password):
                raise AppError("INVALID_CREDENTIALS", "当前密码不正确", 403)
            admin.password_hash = password_hasher.hash(payload.new_password)
            session.execute(delete(Session))
        response.delete_cookie("session", path="/")
        return {"ok": True}

    @application.get("/api/v1/tasks", dependencies=[Depends(auth)], response_model=TaskPage)
    def task_list(status: str | None = None, q: str | None = Query(default=None, max_length=200),
                  page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return tasks.list_tasks(status, q, page, page_size)

    @application.post("/api/v1/tasks/delete", dependencies=[Depends(auth)], response_model=TaskDeleteResponse)
    def delete_tasks(payload: TaskDelete) -> dict[str, list[str]]:
        return tasks.delete(payload.ids)

    @application.delete("/api/v1/tasks/{request_id}", dependencies=[Depends(auth)], response_model=TaskDeleteResponse)
    def delete_task(request_id: str) -> dict[str, list[str]]:
        return tasks.delete([request_id])

    @application.get("/api/v1/tasks/{request_id}", dependencies=[Depends(auth)], response_model=TaskDetail)
    def task_detail(request_id: str) -> dict[str, Any]:
        return tasks.detail(request_id)

    @application.post("/api/v1/tasks/{request_id}/cancel", dependencies=[Depends(auth)], response_model=TaskDetail)
    def cancel(request_id: str) -> dict[str, Any]:
        return tasks.cancel(request_id)

    @application.post("/api/v1/tasks/{request_id}/retry", dependencies=[Depends(auth)], response_model=TaskDetail)
    def retry(request_id: str, payload: Retry | None = None) -> dict[str, Any]:
        return tasks.retry(request_id, payload.item_ids if payload else None)

    @application.get("/api/v1/settings", dependencies=[Depends(auth)], response_model=SettingsResponse)
    def get_settings() -> dict[str, Any]:
        result = settings.public()
        if runtime.network_write_error and result["values"]["bot_api_mode"] == "local":
            result["proxy_status"]["state"] = "failed"
            result["proxy_status"]["message"] = "配置已保存，但写入网络组件失败，请检查共享卷权限"
        return result

    async def validate_bot(candidate: UserSettings) -> dict[str, Any] | None:
        if not candidate.bot_token:
            return None
        try:
            client = TelegramClient(candidate.bot_token, cfg, candidate)
            client.safety = runtime.safety
            try:
                identity = await client.identity()
            finally:
                await client.close()
        except AppError:
            raise
        except Exception as exc:
            raise AppError("BOT_VALIDATION_FAILED", "无法验证 Bot，请检查 Token、所选 API 或代理连接", 422) from exc
        def check_identity() -> None:
            with db.sessions() as session:
                active = session.scalar(select(MediaRequest.id).where(MediaRequest.status.in_(ACTIVE | {"collecting", "awaiting_confirmation"}),
                                                                       MediaRequest.bot_id != identity["id"]).limit(1))
                if active:
                    raise AppError("BOT_BUSY", "存在原 Bot 的活动或待确认请求，不能更换 Bot 身份", 409)
        await asyncio.to_thread(check_identity)
        return identity

    @application.post("/api/v1/settings/validate", dependencies=[Depends(auth)], response_model=ValidationResponse)
    async def validate_settings(payload: SettingsPatch) -> dict[str, Any]:
        candidate = await asyncio.to_thread(settings.candidate, payload)
        async with runtime.connection_lock:
            await asyncio.to_thread(runtime.local_api.validate_allowed, candidate)
            identity = await validate_bot(candidate)
        warnings = []
        if candidate.bot_api_mode == "local" and candidate.proxy.enabled and not cfg.proxy_capable:
            warnings.append("本地网络管理组件不可用，请检查标准部署的组件状态和 TUN 支持")
        try:
            await asyncio.to_thread(storage.get(candidate.download_root_id).check)
        except (OSError, AppError):
            warnings.append("下载目录暂不可用，请检查挂载、权限和空间")
        return {"valid": True, "warnings": warnings, "bot": identity}

    @application.patch("/api/v1/settings", dependencies=[Depends(auth)], response_model=SettingsResponse)
    async def patch_settings(payload: SettingsPatch) -> dict[str, Any]:
        candidate = await asyncio.to_thread(settings.candidate, payload)
        # Persist independently of Telegram availability. Runtime verifies identity
        # before activation; explicit /validate probes never save this candidate.
        async with runtime.connection_lock:
            result = await asyncio.to_thread(settings.save, payload, candidate)
        await asyncio.to_thread(runtime.maintain_diagnostics)
        return result

    @application.post("/api/v1/system/local-api/restart", dependencies=[Depends(auth)], response_model=LocalApiStatus, status_code=202)
    async def restart_local_api() -> dict[str, Any]:
        async with runtime.connection_lock:
            return await asyncio.to_thread(runtime.local_api.action, True)

    @application.post("/api/v1/system/local-api/retry", dependencies=[Depends(auth)], response_model=LocalApiStatus, status_code=202)
    async def retry_local_api(payload: LocalApiRetry | None = None) -> dict[str, Any]:
        async with runtime.connection_lock:
            return await asyncio.to_thread(runtime.local_api.action, False, bool(payload and payload.acknowledge_logout_uncertain))

    @application.get("/api/v1/system/logs", dependencies=[Depends(auth)])
    def export_logs() -> dict[str, Any]:
        return runtime.diagnostics.export()

    @application.delete("/api/v1/system/logs", dependencies=[Depends(auth)], response_model=DiagnosticLogStatus)
    def clear_logs() -> dict[str, Any]:
        return runtime.diagnostics.clear()

    @application.get("/api/v1/system/status", dependencies=[Depends(auth)], response_model=SystemStatus)
    def system_status() -> dict[str, Any]:
        revision, values, _ = settings.read()
        roots = []
        for root_id, root in storage.roots.items():
            try:
                roots.append(root.check())
            except (OSError, AppError, ValueError):
                roots.append({"id": root_id, "label": root_id, "available": False, "free_bytes": None,
                              "total_bytes": None, "message": "下载目录不可用，请检查挂载、权限和空间"})
        cache: dict[str, Any] = {"free_bytes": None, "total_bytes": None}
        try:
            usage = shutil.disk_usage(cfg.bot_api_root)
            cache = {"free_bytes": usage.free, "total_bytes": usage.total}
        except OSError:
            pass
        active, blocked, pending = runtime.counts()
        safety = runtime.safety.status(values.bot_token)
        bot = dict(runtime.bot_status) | {"request_safety": safety}
        if safety["state"] != "ready":
            bot.update(state="error", message=safety["message"])
        return {"version": "0.1.0", "diagnostic_log": runtime.diagnostics.status(), "bot": bot, "local_api": runtime.local_api.status(), "storage": {"roots": roots, "cache": cache},
                "proxy": settings.proxy_status(revision, values),
                "queue": {"active": active, "pending": pending, "concurrency": values.download_concurrency},
                "recovery": {"blocked_jobs": blocked, "message": "请重启本地 Bot API 以恢复不确定的获取槽" if blocked else ""}}

    @application.post("/api/v1/system/bot/retry", dependencies=[Depends(auth)], response_model=RequestSafetyStatus)
    async def retry_bot() -> dict[str, Any]:
        async with runtime.connection_lock:
            _, candidate, _ = await asyncio.to_thread(settings.read)
            candidate = await asyncio.to_thread(runtime.local_api.recovery_candidate, candidate)
            status = await asyncio.to_thread(runtime.safety.status, candidate.bot_token)
            if not status["can_retry"]:
                raise AppError("BOT_RETRY_UNAVAILABLE", status["message"] or "当前无需恢复 Bot 凭据", 409)
            client = TelegramClient(candidate.bot_token, cfg, candidate)
            client.safety = runtime.safety
            try:
                identity = await client.identity(probe=True)
                await asyncio.to_thread(runtime.remember_identity, identity)
            except AppError:
                raise
            except Exception as exc:
                raise AppError("BOT_VALIDATION_FAILED", "Bot 验证未成功，请检查凭据和连接后重试", 422) from exc
            finally:
                await client.close()
        return await asyncio.to_thread(runtime.safety.status, candidate.bot_token)

    @application.get("/{path:path}", include_in_schema=False)
    def static(path: str) -> Response:
        if path.startswith(("api/", "health/")):
            raise AppError("NOT_FOUND", "接口不存在", 404)
        root = cfg.static_dir.resolve()
        requested = (root / path).resolve()
        if requested.is_relative_to(root) and requested.is_file():
            return FileResponse(requested)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"name": "Telegram 媒体下载助手", "message": "WebUI 静态文件尚未构建"}, status_code=503)

    return application


app = create_app()

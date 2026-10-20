from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.core.templates import DEFAULT_CONFIRMATION_TEMPLATE, DEFAULT_FILENAME_TEMPLATE, validate_template


class ProxySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    scheme: Literal["http", "socks5"] = "socks5"
    host: str = ""
    port: int = Field(default=1080, ge=1, le=65535)
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=1000)

    @field_validator("host")
    @classmethod
    def safe_host(cls, value: str) -> str:
        import re
        if value and not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", value):
            raise ValueError("代理地址只接受主机名或 IPv4，不包含协议、路径或端口")
        if value and any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                         for label in value.split(".")):
            raise ValueError("代理主机名格式不正确")
        return value

    @field_validator("username", "password")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("代理认证不可包含控制字符")
        return value


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_api_mode: Literal["cloud", "local"] = "local"
    confirmation_template: str = DEFAULT_CONFIRMATION_TEMPLATE
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    confirmation_timeout_seconds: int = Field(default=1800, ge=30, le=86400)
    download_concurrency: int = Field(default=3, ge=1, le=16)
    max_auto_retries: int = Field(default=3, ge=0, le=10)
    allowed_user_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=100)
    download_root_id: str = Field(default="default", min_length=1, max_length=80)
    timezone: str = "Asia/Shanghai"
    bot_token: str = Field(default="", max_length=300)
    telegram_api_id: str = Field(default="", max_length=10)
    telegram_api_hash: str = Field(default="", max_length=32)
    proxy: ProxySettings = Field(default_factory=ProxySettings)

    @field_validator("confirmation_template", "filename_template")
    @classmethod
    def valid_template(cls, value: str, info: ValidationInfo) -> str:
        return validate_template(value, filename=info.field_name == "filename_template")

    @field_validator("bot_token")
    @classmethod
    def token_format(cls, value: str) -> str:
        import re
        if value and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", value):
            raise ValueError("Bot Token 格式不正确")
        return value

    @field_validator("telegram_api_id", "telegram_api_hash")
    @classmethod
    def application_credentials(cls, value: str, info: ValidationInfo) -> str:
        import re
        if value and info.field_name == "telegram_api_id" and (
                not re.fullmatch(r"[1-9][0-9]{0,9}", value) or int(value) > 2147483647):
            raise ValueError("API ID 必须为正整数")
        if value and info.field_name == "telegram_api_hash" and not re.fullmatch(r"[a-fA-F0-9]{32}", value):
            raise ValueError("API Hash 必须为 32 位十六进制字符")
        return value

    @field_validator("timezone")
    @classmethod
    def known_zone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("无效时区") from exc
        return value


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_api_mode: Literal["cloud", "local"] | None = None
    revision: int | None = None
    confirmation_template: str | None = None
    filename_template: str | None = None
    confirmation_timeout_seconds: int | None = None
    download_concurrency: int | None = None
    max_auto_retries: int | None = None
    allowed_user_ids: list[int] | None = None
    download_root_id: str | None = None
    timezone: str | None = None
    bot_token: str | None = None
    proxy: ProxySettings | None = None
    clear_bot_token: bool = False
    clear_proxy_password: bool = False
    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    clear_telegram_api_hash: bool = False

    @field_validator("confirmation_template", "filename_template")
    @classmethod
    def valid_template(cls, value: str | None, info: ValidationInfo) -> str | None:
        return validate_template(value, filename=info.field_name == "filename_template") if value is not None else value


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class Setup(Credentials):
    bootstrap_secret: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=6, max_length=20)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=20)


class Retry(BaseModel):
    item_ids: list[str] | None = None


class LocalApiRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_logout_uncertain: bool = False


class TaskDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("ids")
    @classmethod
    def valid_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not item.strip() or len(item) > 100 for item in value):
            raise ValueError("任务 ID 不可重复或为空")
        return value

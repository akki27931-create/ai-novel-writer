"""全局配置。

原则：
1. 敏感信息（DeepSeek API Key）优先从环境变量 / .env 读取，绝不硬编码在代码里。
2. 其余可调参数（模型名、温度、max_tokens 等）保存在本地数据库，用户可在“设置页”修改。
3. 环境变量的优先级高于数据库设置（方便 Docker / CI 注入）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/ -> 项目根目录
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parents[2]


class EnvSettings(BaseSettings):
    """来自环境变量 / .env 的配置。"""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # DeepSeek / OpenAI 兼容接口
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # 数据目录（SQLite、ChromaDB、上传文件都放这里）
    data_dir: str = ""

    # 服务监听
    host: str = "0.0.0.0"
    port: int = 8000

    # 允许的前端来源，逗号分隔；* 表示全部允许（本地开发默认）
    cors_origins: str = "*"

    # 番茄小说 MCP server 启动命令（留空表示未配置，走手动上传）
    # 例如：npx -y mcp-server-fanqie
    fanqie_mcp_command: str = ""

    # 设置页模型下拉框的候选值（逗号分隔）
    candidate_models: str = (
        "deepseek-v4.1-flash,deepseek-v4.1-pro,deepseek-chat,deepseek-reasoner"
    )

    # 番茄 MCP 依赖的第三方数据接口地址（该接口若变更/下线，可在此指向新的地址）
    fanqie_api_base: str = ""

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir) if self.data_dir else BACKEND_DIR / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def upload_path(self) -> Path:
        path = self.data_path / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def chroma_path(self) -> Path:
        path = self.data_path / "chroma"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sqlite_url(self) -> str:
        db_file = self.data_path / "app.db"
        return f"sqlite:///{db_file.as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def candidate_model_list(self) -> list[str]:
        return [m.strip() for m in self.candidate_models.split(",") if m.strip()]


@lru_cache(maxsize=1)
def get_env() -> EnvSettings:
    return EnvSettings()


env = get_env()

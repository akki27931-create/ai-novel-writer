"""番茄小说导入适配器（可插拔 MCP）。

真实依赖
--------
本适配器对接的 MCP server 是 **`@fysh925/mcp-server-fanqie`**（npm 包，14 个工具）。
它的数据来自第三方接口 `FANQIE_API_BASE`（默认 `http://101.35.133.34:5000`）。
**该第三方接口当前已下线（TCP 不可达）**，所以即使 MCP 正常启动，搜书/下载也会失败。
可用环境变量 `FANQIE_API_BASE` 指向其它可用接口；接口恢复后无需改代码即可继续使用。

设计要点
--------
- 不硬编码工具名：启动后用 tools/list 自动发现，再按候选名优先级匹配。
- 参数名严格按官方 README 的工具签名构造（例如搜索用 `key`，不是 `keyword`）。
- 整个下载过程**只启动一次 MCP 进程**并复用同一会话，避免几百次 npx 启动开销。
- 调用失败时抛出 `FanqieUnavailable`，消息里带上游真实报错，前端原样展示给用户。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..settings_store import get_settings
from .text_utils import RawChapter, clean_text, count_words

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 180.0
DEFAULT_API_BASE = "http://101.35.133.34:5000"

# 每种能力对应的候选工具名（按优先级排列）
TOOL_CANDIDATES: dict[str, list[str]] = {
    "search": ["search_books", "search_book", "search_novel", "search"],
    "detail": ["get_book_detail", "book_detail", "get_detail", "detail"],
    "catalog": ["get_simple_directory", "get_book_directory", "get_catalog", "get_toc", "directory"],
    "content": ["get_chapter_content", "get_content", "get_raw_content", "chapter_content"],
}

# 从各种命名风格里识别章节 ID / 标题
ID_KEYS = ("item_id", "chapter_id", "itemId", "chapterId", "itemID", "id")
TITLE_KEYS = ("chapter_title", "title", "chapterTitle", "item_title", "name")
VOLUME_HINT_KEYS = ("volume_title", "volumeTitle", "volume_name")

ProgressFn = Callable[[int, int, str], None]


class FanqieUnavailable(RuntimeError):
    """番茄 MCP 不可用（未配置 / 启动失败 / 上游接口异常）。"""


def get_command(db: Session) -> str:
    settings = get_settings(db, include_secrets=True)
    return str(settings.get("fanqie_mcp_command") or "").strip()


def get_api_base(db: Session) -> str:
    settings = get_settings(db, include_secrets=True)
    return str(settings.get("fanqie_api_base") or "").strip() or DEFAULT_API_BASE


# ======================================================================
# MCP stdio 客户端（JSON-RPC 2.0，换行分隔）
# ======================================================================
class MCPStdioSession:
    """最小可用的 MCP stdio 客户端。

    注意：`request()` 是按 id 匹配响应的，非匹配消息会被丢弃，
    因此**同一个会话上不能并发发请求**，必须串行使用。
    """

    def __init__(self, command: str, *, env: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.command = command
        self.extra_env = env or {}
        self.timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._id = 0
        self._stderr_lines: list[str] = []

    async def __aenter__(self) -> "MCPStdioSession":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        import os

        env = {**os.environ, **self.extra_env}
        try:
            self._process = await asyncio.create_subprocess_shell(
                self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            raise FanqieUnavailable(f"无法启动 MCP server（{self.command}）：{exc}") from exc

        asyncio.create_task(self._drain_stderr())
        try:
            await self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "local-novel-ai", "version": "0.1.0"},
                },
            )
            await self._notify("notifications/initialized", {})
        except FanqieUnavailable:
            await self.close()
            raise

    async def _drain_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            self._stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
            if len(self._stderr_lines) > 60:
                self._stderr_lines.pop(0)

    async def close(self) -> None:
        """关闭进程并释放管道，避免 Windows 上的 unclosed transport 告警。"""
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:  # noqa: BLE001
            try:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=5)
            except Exception:  # noqa: BLE001
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None and not stream.is_closing():
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(0)  # 让传输层有机会回收

    # ------------------------------------------------------------------
    async def _write(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise FanqieUnavailable("MCP 进程未启动")
        data = json.dumps(payload, ensure_ascii=False) + "\n"
        self._process.stdin.write(data.encode("utf-8"))
        await self._process.stdin.drain()

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        request_id = self._id
        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise FanqieUnavailable(
                    f"MCP 调用超时（{method}）。stderr: {' | '.join(self._stderr_lines[-3:])}"
                )
            message = await self._read_message(remaining)
            if message.get("id") != request_id:
                continue  # 忽略通知 / 其它响应
            if "error" in message:
                raise FanqieUnavailable(f"MCP 返回错误：{message['error']}")
            return message.get("result") or {}

    async def _read_message(self, timeout: float) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise FanqieUnavailable("MCP 进程未启动")
        try:
            line = await asyncio.wait_for(self._process.stdout.readline(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise FanqieUnavailable("等待 MCP 响应超时") from exc
        if not line:
            raise FanqieUnavailable(f"MCP 进程已退出。stderr: {' | '.join(self._stderr_lines[-3:])}")
        try:
            return json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}  # 非 JSON 行（日志）直接跳过


# ======================================================================
# 高层适配器
# ======================================================================
class FanqieAdapter:
    def __init__(self, command: str, api_base: str = "") -> None:
        self.command = command
        self.api_base = api_base or DEFAULT_API_BASE
        self._tools: list[dict[str, Any]] = []

    @classmethod
    def from_db(cls, db: Session) -> "FanqieAdapter":
        return cls(get_command(db), get_api_base(db))

    @property
    def configured(self) -> bool:
        return bool(self.command)

    @property
    def env(self) -> dict[str, str]:
        return {"FANQIE_API_BASE": self.api_base}

    # ---------------- 连接与工具发现 ----------------
    async def _refresh_tools(self, session: MCPStdioSession) -> list[dict[str, Any]]:
        result = await session.request("tools/list", {})
        self._tools = list(result.get("tools") or [])
        return self._tools

    async def list_tools(self) -> list[dict[str, Any]]:
        if not self.configured:
            raise FanqieUnavailable(
                "未配置番茄 MCP server。请在设置页填写命令"
                "（推荐：npx -y @fysh925/mcp-server-fanqie），或改用 TXT / EPUB 上传。"
            )
        async with MCPStdioSession(self.command, env=self.env) as session:
            return await self._refresh_tools(session)

    async def status(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "configured": False,
                "available": False,
                "command": "",
                "tools": [],
                "message": "未配置：可在设置页填写 MCP 命令，或直接上传 TXT/EPUB。",
            }
        try:
            tools = await self.list_tools()
            return {
                "configured": True,
                "available": True,
                "command": self.command,
                "tools": [t.get("name", "") for t in tools],
                "message": f"MCP 已连接，发现 {len(tools)} 个工具。（注意：能否真的搜到书取决于上游接口）",
            }
        except FanqieUnavailable as exc:
            return {
                "configured": True,
                "available": False,
                "command": self.command,
                "tools": [],
                "message": str(exc),
            }

    async def selftest(self) -> dict[str, Any]:
        """逐步自检：进程 → 工具列表 → 真实搜一次书。用于让用户看清卡在哪一步。"""
        steps: list[dict[str, Any]] = []

        def add(step: str, ok: bool, detail: str = "") -> None:
            steps.append({"step": step, "ok": ok, "detail": detail})

        if not self.configured:
            add("配置命令", False, "未填写 MCP 启动命令")
            return {"ok": False, "steps": steps, "api_base": self.api_base}

        add("配置命令", True, self.command)
        try:
            tools = await self.list_tools()
            add("启动 MCP 进程 & 获取工具列表", True, f"发现 {len(tools)} 个工具：{', '.join(t.get('name','') for t in tools)}")
        except FanqieUnavailable as exc:
            add("启动 MCP 进程 & 获取工具列表", False, str(exc))
            return {"ok": False, "steps": steps, "api_base": self.api_base}

        try:
            data = await self.search("修仙")
            if isinstance(data, dict) and data.get("error"):
                raise FanqieUnavailable(str(data.get("message") or data))
            results = data.get("results") if isinstance(data, dict) else None
            add(
                "调用上游搜书接口",
                True,
                f"搜索成功，返回 {len(results) if isinstance(results, list) else '?'} 条结果",
            )
            return {"ok": True, "steps": steps, "api_base": self.api_base}
        except FanqieUnavailable as exc:
            add(
                "调用上游搜书接口",
                False,
                f"{exc}\n（这一步失败说明 MCP 本身没问题，是它依赖的第三方接口 {self.api_base} 不可用）",
            )
            return {"ok": False, "steps": steps, "api_base": self.api_base}

    # ---------------- 工具定位 ----------------
    def _find_tools(self, kind: str) -> list[dict[str, Any]]:
        names = {str(t.get("name") or "") for t in self._tools}
        found: list[dict[str, Any]] = []
        for candidate in TOOL_CANDIDATES.get(kind, []):
            if candidate in names:
                found.append(next(t for t in self._tools if str(t.get("name")) == candidate))
        if found:
            return found
        # 兜底：按关键词模糊匹配
        keywords = {
            "search": ["search"],
            "detail": ["detail"],
            "catalog": ["directory", "catalog", "toc"],
            "content": ["content"],
        }[kind]
        for tool in self._tools:
            name = str(tool.get("name") or "").lower()
            if any(k in name for k in keywords):
                found.append(tool)
        if not found:
            raise FanqieUnavailable(
                f"未找到「{kind}」对应的 MCP 工具，实际可用工具：{[t.get('name') for t in self._tools]}"
            )
        return found

    async def _call(
        self, kind: str, session: MCPStdioSession, arguments: dict[str, Any]
    ) -> Any:
        """在已有会话上调用某个能力的工具，按候选顺序逐个尝试。"""
        errors: list[str] = []
        for tool in self._find_tools(kind):
            name = str(tool.get("name"))
            try:
                result = await session.request(
                    "tools/call", {"name": name, "arguments": arguments}
                )
            except FanqieUnavailable as exc:
                errors.append(f"{name}: {exc}")
                continue
            if result.get("isError"):
                errors.append(f"{name}: {_content_text(result)[:300]}")
                continue
            return _coerce(_content_text(result))
        raise FanqieUnavailable("；".join(errors) or "工具调用失败")

    # ---------------- 业务能力 ----------------
    async def search(self, keyword: str, *, tab_type: int = 3, offset: int = 0) -> Any:
        async with MCPStdioSession(self.command, env=self.env) as session:
            if not self._tools:
                await self._refresh_tools(session)
            return await self._call(
                "search", session, {"key": keyword, "tab_type": tab_type, "offset": offset}
            )

    async def book_detail(self, book_id: str) -> Any:
        async with MCPStdioSession(self.command, env=self.env) as session:
            if not self._tools:
                await self._refresh_tools(session)
            return await self._call("detail", session, {"book_id": book_id})

    async def catalog(self, book_id: str) -> Any:
        async with MCPStdioSession(self.command, env=self.env) as session:
            if not self._tools:
                await self._refresh_tools(session)
            return await self._call("catalog", session, {"book_id": book_id})

    async def chapter_content(self, book_id: str, chapter_id: Any) -> Any:
        async with MCPStdioSession(self.command, env=self.env) as session:
            if not self._tools:
                await self._refresh_tools(session)
            return await self._call("content", session, {"item_id": str(chapter_id)})

    # ---------------- 整本下载 ----------------
    async def download(
        self,
        book_id: str,
        *,
        start_chapter: int = 1,
        max_chapters: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        """下载整本书。全程复用同一个 MCP 进程，避免每章都重启 npx。"""
        if not self.configured:
            raise FanqieUnavailable(
                "未配置番茄 MCP server。请在设置页填写命令"
                "（推荐：npx -y @fysh925/mcp-server-fanqie）。"
            )

        async with MCPStdioSession(self.command, env=self.env) as session:
            await self._refresh_tools(session)
            logger.info("番茄 MCP 已连接，工具：%s", [t.get("name") for t in self._tools])

            # 1) 书籍详情（失败不致命，用书籍 ID 兜底）
            meta: dict[str, Any] = {}
            try:
                detail = await self._call("detail", session, {"book_id": book_id})
                meta = _first_dict(_first_dict(detail) or {}) or {}
            except FanqieUnavailable as exc:
                logger.warning("获取书籍详情失败（忽略）：%s", exc)

            # 2) 目录
            catalog = await self._call("catalog", session, {"book_id": book_id})
            items = _extract_catalog(catalog)
            if not items:
                raise FanqieUnavailable(
                    "未能从目录接口解析出章节列表。原始返回片段："
                    f"{json.dumps(catalog, ensure_ascii=False)[:400] if not isinstance(catalog, str) else catalog[:400]}"
                )

            items = items[max(0, start_chapter - 1) :]
            if max_chapters:
                items = items[:max_chapters]
            total = len(items)
            if on_progress:
                on_progress(0, total, f"目录共 {total} 章，开始下载")

            # 3) 逐章取正文（串行，复用同一会话）
            chapters: list[RawChapter] = []
            failures: list[str] = []
            for index, item in enumerate(items, start=1):
                chapter_id = item.get("id")
                title = str(item.get("title") or f"第{index}章")
                try:
                    payload = await self._call("content", session, {"item_id": str(chapter_id)})
                    content = clean_text(_extract_content(payload))
                except FanqieUnavailable as exc:
                    failures.append(f"{title}: {exc}")
                    content = ""
                if count_words(content) >= 20:
                    chapters.append(
                        RawChapter(number=len(chapters) + 1, title=title, content=content)
                    )
                if on_progress:
                    on_progress(index, total, f"已下载 {index}/{total} 章")
                await asyncio.sleep(0.05)  # 轻微限速，避免把上游打挂

            if not chapters:
                hint = failures[:2] if failures else ["上游未返回任何正文"]
                raise FanqieUnavailable("一章正文都没拿到。" + "；".join(hint))

            return {
                "title": str(meta.get("book_name") or meta.get("bookName") or meta.get("title") or f"番茄小说-{book_id}"),
                "author": str(meta.get("author") or meta.get("authorName") or ""),
                "intro": str(meta.get("abstract") or meta.get("intro") or meta.get("description") or ""),
                "cover_url": str(meta.get("thumb_url") or meta.get("thumbUrl") or meta.get("cover") or ""),
                "chapters": chapters,
                "failed": len(failures),
                "total": total,
            }


# ======================================================================
# 解析辅助
# ======================================================================
def _content_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif item.get("type") == "resource":
                parts.append(str((item.get("resource") or {}).get("text") or ""))
        elif isinstance(item, str):
            parts.append(item)
    if not parts and result.get("structuredContent"):
        return json.dumps(result["structuredContent"], ensure_ascii=False)
    return "\n".join(parts)


def _coerce(text: str) -> Any:
    """工具返回的 text 可能是 JSON 字符串，也可能夹着说明文字，尽量解析出来。"""
    text = (text or "").strip()
    if not text:
        return ""
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return text


def _first_dict(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        for key in ("data", "detail", "book", "info", "result"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return None


def _pick_id(item: dict[str, Any]) -> str:
    for key in ID_KEYS:
        value = item.get(key)
        if value not in (None, "", 0):
            return str(value)
    return ""


def _pick_title(item: dict[str, Any], fallback: str) -> str:
    for key in TITLE_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _extract_catalog(payload: Any, depth: int = 0) -> list[dict[str, str]]:
    """从任意层级的目录结构里抽出 [{id, title}]。

    兼容：扁平列表、{chapters:[...]}、{directory:{volume_1:{chapters:[...]}}}、
    以及「一列纯数字 ID」这几种形态。
    """
    if depth > 8:
        return []

    if isinstance(payload, list):
        dicts = [x for x in payload if isinstance(x, dict)]
        if dicts and all(_pick_id(x) for x in dicts):
            return [
                {"id": _pick_id(x), "title": _pick_title(x, f"第{i + 1}章")}
                for i, x in enumerate(dicts)
            ]
        if payload and all(isinstance(x, str) and x.strip().isdigit() for x in payload):
            return [{"id": x.strip(), "title": f"第{i + 1}章"} for i, x in enumerate(payload)]
        for item in payload:
            found = _extract_catalog(item, depth + 1)
            if found:
                return found
        return []

    if isinstance(payload, dict):
        preferred = (
            "chapters",
            "chapter_list",
            "chapterList",
            "item_list",
            "items",
            "list",
            "data",
            "results",
        )
        for key in preferred:
            if key in payload:
                found = _extract_catalog(payload[key], depth + 1)
                if found:
                    return found
        # 任意 list 值
        for value in payload.values():
            if isinstance(value, list):
                found = _extract_catalog(value, depth + 1)
                if found:
                    return found
        # 卷结构：{volume_1: {...}}
        for value in payload.values():
            if isinstance(value, dict) and value:
                found = _extract_catalog(value, depth + 1)
                if found:
                    return found
        return []

    return []


def _extract_content(payload: Any) -> str:
    """从章节内容返回值里取出正文文本。"""
    if isinstance(payload, str):
        return _strip_tags(payload)
    if isinstance(payload, dict):
        # 有些实现会返回 {"content": {"content": "..."}} 这种嵌套
        for key in ("content", "text", "chapter_content", "chapterContent", "body"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _strip_tags(value)
            if isinstance(value, (dict, list)):
                nested = _extract_content(value)
                if nested.strip():
                    return nested
        for key in ("data", "result", "chapter"):
            value = payload.get(key)
            if value is not None:
                nested = _extract_content(value)
                if nested.strip():
                    return nested
    if isinstance(payload, list):
        parts = [_extract_content(x) for x in payload]
        return "\n".join(p for p in parts if p.strip())
    return ""


def _strip_tags(text: str) -> str:
    if "<" not in text:
        return text
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(entity, char)
    return text


def parse_command(command: str) -> list[str]:
    """把命令字符串拆成 argv（用于调试）。"""
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()

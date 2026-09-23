from __future__ import annotations

import base64
from contextlib import ExitStack
import json
import os
import shutil
import signal
import subprocess
import re
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep
from typing import Callable

from .config import settings
from .crawler_browser import account_browser_env, account_profile_owns_auth, scheduler_env

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
SUPPORTED_PLATFORMS = ("xhs", "dy", "ks")
PLATFORM_DATA_DIRS = {
    "xhs": "xhs",
    "dy": "douyin",
    "ks": "kuaishou",
}
ACCOUNT_AUTH_STATE_ENV = "MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64"
ACCOUNT_AUTH_INVALID_MARKER = "ACCOUNT_AUTH_INVALID"


class CrawlerAuthenticationError(RuntimeError):
    pass


class CrawlerVerificationError(RuntimeError):
    pass


class CrawlerCollectionIncompleteError(RuntimeError):
    """A required content stage failed; retain outputs without rotating accounts."""



@dataclass
class CrawlOutput:
    platform: str
    contents: list[dict]
    comments: list[dict]
    output_dir: Path
    creators: list[dict] = field(default_factory=list)
    command: list[str] = field(default_factory=list)


ProgressCallback = Callable[[int, int], None]
ContentCallback = Callable[[list[dict], list[dict]], None]
StopChecker = Callable[[], bool]
StartedCallback = Callable[[], None]
CheckpointCallback = Callable[[str, int], None]

CHECKPOINT_PATTERNS = {
    "dy": r"search douyin keyword: (.*?), page: (\d+)",
}


def latest_search_checkpoint(log_text: str, platform: str) -> tuple[str, int] | None:
    pattern = CHECKPOINT_PATTERNS.get(platform)
    if not pattern:
        return None
    matches = re.findall(pattern, log_text)
    if not matches:
        return None
    keyword, page = matches[-1]
    return keyword.strip(), int(page)


class CrawlerRateLimitError(RuntimeError):
    """Shared platform cooldown; do not mark an account expired or rotate it."""


class MediaCrawlerAdapter:
    def __init__(self, media_crawler_dir: Path | None = None):
        self.media_crawler_dir = media_crawler_dir or settings.media_crawler_dir

    def run_search(
        self,
        platform: str,
        keyword: str,
        start_page: int,
        max_notes: int,
        max_comments: int,
        max_concurrency: int,
        max_items_per_minute: int,
        get_sub_comment: bool,
        save_root: Path,
        search_sort: str = "general",
        progress_callback: ProgressCallback | None = None,
        content_callback: ContentCallback | None = None,
        stream_items: bool = False,
        stop_checker: StopChecker | None = None,
        auth_state: dict | None = None,
        started_callback: StartedCallback | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
        skip_content_ids_file: Path | None = None,
        reusable_content_db: Path | None = None,
        current_task_id: str = "",
        resume_keyword: str = "",
        resume_page: int | None = None,
        account_id: str = "",
        collect_comments: bool = True,
        collect_media: bool = True,
        max_total_notes: int | None = None,
        query_correct_type: int | None = None,
        fetch_author_profile: bool = False,
    ) -> CrawlOutput:
        self._validate_platform(platform)
        effective_max_comments = max_comments if collect_comments else 0
        terms = list(
            dict.fromkeys(term.strip() for term in keyword.split(",") if term.strip())
        )
        if not terms:
            raise ValueError("at least one search keyword is required")
        total_limit = min(
            max_notes * len(terms),
            max_total_notes if max_total_notes is not None else max_notes * len(terms),
        )
        existing = self._load_platform_output(save_root, platform)
        known_ids = {
            identity
            for item in existing.contents
            if (identity := self._content_identity(item, platform))
        }
        streamed_ids = set(known_ids)
        collected_by_term: dict[str, int] = {}
        for item in existing.contents:
            identity = self._content_identity(item, platform)
            source_term = str(item.get("source_keyword") or "").strip()
            if identity and source_term:
                collected_by_term[source_term] = collected_by_term.get(source_term, 0) + 1

        if resume_keyword in terms:
            terms = terms[terms.index(resume_keyword) :]

        last_command: list[str] = []
        output = existing
        crawler_started = False
        for term in terms:
            remaining_total = total_limit - len(known_ids)
            existing_for_term = collected_by_term.get(term, 0)
            remaining_for_term = max_notes - existing_for_term
            new_capacity = min(remaining_total, remaining_for_term)
            if new_capacity <= 0:
                continue
            if stop_checker and stop_checker():
                break

            # Douyin initializes its per-keyword result list with completed
            # rows from the reusable task database, so its CLI value is an
            # absolute target. Other enabled platforms do not restore that
            # count and must receive only the remaining new-item capacity.
            term_target = existing_for_term + new_capacity
            crawler_restores_term_count = bool(
                platform == "dy" and reusable_content_db and current_task_id
            )
            command_limit = (
                term_target if crawler_restores_term_count else new_capacity
            )

            command = [
                *self._base_command(platform),
                "--platform",
                platform,
                "--lt",
                "cookie" if auth_state else "qrcode",
                "--type",
                "search",
                "--keywords",
                term,
                "--start",
                str(start_page),
                "--crawler_max_notes_count",
                str(command_limit),
                "--max_comments_count_singlenotes",
                str(effective_max_comments),
                "--max_concurrency_num",
                str(max_concurrency),
                "--crawler_max_items_per_minute",
                str(max_items_per_minute),
                "--crawler_sleep_sec",
                str(settings.crawler_sleep_seconds),
                "--get_comment",
                "true" if collect_comments else "false",
                "--get_sub_comment",
                "true" if collect_comments and get_sub_comment else "false",
                "--get_media",
                "true" if collect_media else "false",
                "--stream_items",
                "true" if stream_items else "false",
                "--save_data_option",
                "jsonl",
                "--save_data_path",
                str(save_root),
            ]
            if platform == "dy":
                command.extend(["--dy_search_sort", search_sort])
            if fetch_author_profile:
                # 搜索结果里的 author 粉丝数恒为 0 且没有签名，只有初筛候选需要这一轮补充请求
                command.extend(["--dy_fetch_author_profile", "true"])
            if skip_content_ids_file:
                command.extend(["--skip_aweme_ids_file", str(skip_content_ids_file)])
            if reusable_content_db:
                command.extend(["--reusable_content_db", str(reusable_content_db)])
            if current_task_id:
                command.extend(["--current_task_id", current_task_id])
            if query_correct_type is not None:
                command.extend(["--dy_query_correct_type", str(int(query_correct_type))])
            if term == resume_keyword and resume_page is not None:
                command.extend(
                    ["--resume_keyword", resume_keyword, "--resume_page", str(resume_page)]
                )

            def relay_content(contents: list[dict], comments: list[dict]) -> None:
                if content_callback is None:
                    return
                new_contents = []
                for item in contents:
                    identity = self._content_identity(item, platform)
                    if not identity or identity in streamed_ids:
                        continue
                    streamed_ids.add(identity)
                    new_contents.append(item)
                if new_contents:
                    content_callback(new_contents, comments)

            def relay_progress(_current: int, _target: int) -> None:
                if progress_callback is None:
                    return
                current_total = self._latest_content_count(save_root, platform)
                progress_callback(min(current_total, total_limit), total_limit)

            output = self._run_command(
                command=command,
                save_root=save_root,
                platform=platform,
                max_notes=command_limit,
                progress_callback=relay_progress if progress_callback else None,
                content_callback=relay_content if content_callback else None,
                stop_checker=stop_checker,
                auth_state=auth_state,
                started_callback=(
                    started_callback if not crawler_started else None
                ),
                checkpoint_callback=checkpoint_callback,
                account_id=account_id,
            )
            crawler_started = True
            last_command = command
            output = self._load_platform_output(save_root, platform)
            known_ids = {
                identity
                for item in output.contents
                if (identity := self._content_identity(item, platform))
            }
            collected_by_term[term] = sum(
                1
                for item in output.contents
                if str(item.get("source_keyword") or "").strip() == term
                and self._content_identity(item, platform)
            )
            if progress_callback:
                progress_callback(min(len(known_ids), total_limit), total_limit)
            if len(known_ids) >= total_limit:
                break

        output.command = last_command
        return output

    def run_creator(
        self,
        platform: str,
        creator_id: str,
        max_notes: int,
        max_comments: int,
        max_concurrency: int,
        max_items_per_minute: int,
        get_sub_comment: bool,
        save_root: Path,
        progress_callback: ProgressCallback | None = None,
        content_callback: ContentCallback | None = None,
        stream_items: bool = False,
        stop_checker: StopChecker | None = None,
        auth_state: dict | None = None,
        started_callback: StartedCallback | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
        account_id: str = "",
        collect_comments: bool = True,
        collect_media: bool = True,
    ) -> CrawlOutput:
        self._validate_platform(platform)
        effective_max_comments = max_comments if collect_comments else 0
        command = [
            *self._base_command(platform),
            "--platform",
            platform,
            "--lt",
            "cookie" if auth_state else "qrcode",
            "--type",
            "creator",
            "--creator_id",
            creator_id,
            "--crawler_max_notes_count",
            str(max_notes),
            "--max_comments_count_singlenotes",
            str(effective_max_comments),
            "--max_concurrency_num",
            str(max_concurrency),
            "--crawler_max_items_per_minute",
            str(max_items_per_minute),
            "--crawler_sleep_sec",
            str(settings.crawler_sleep_seconds),
            "--get_comment",
            "true" if collect_comments else "false",
            "--get_sub_comment",
            "true" if collect_comments and get_sub_comment else "false",
            "--get_media",
            "true" if collect_media else "false",
            "--stream_items",
            "true" if stream_items else "false",
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(save_root),
        ]
        return self._run_command(
            command=command,
            save_root=save_root,
            platform=platform,
            max_notes=max_notes,
            progress_callback=progress_callback,
            content_callback=content_callback,
            stop_checker=stop_checker,
            auth_state=auth_state,
            started_callback=started_callback,
            checkpoint_callback=checkpoint_callback,
            account_id=account_id,
        )

    def run_detail(
        self,
        platform: str,
        content_id: str,
        *,
        source_keyword: str,
        max_comments: int,
        max_concurrency: int,
        max_items_per_minute: int,
        get_sub_comment: bool,
        save_root: Path,
        progress_callback: ProgressCallback | None = None,
        content_callback: ContentCallback | None = None,
        stream_items: bool = False,
        stop_checker: StopChecker | None = None,
        auth_state: dict | None = None,
        started_callback: StartedCallback | None = None,
        account_id: str = "",
        collect_comments: bool = True,
        collect_media: bool = True,
    ) -> CrawlOutput:
        """Collect one post by ID with media and full comments; tag rows with the originating keyword."""
        self._validate_platform(platform)
        content_id = str(content_id or "").strip()
        if not content_id:
            raise ValueError("run_detail requires a content id")
        effective_max_comments = max_comments if collect_comments else 0
        command = [
            *self._base_command(platform),
            "--platform", platform,
            "--lt", "cookie" if auth_state else "qrcode",
            "--type", "detail",
            "--specified_id", content_id,
            "--crawler_max_notes_count", "1",
            "--max_comments_count_singlenotes", str(effective_max_comments),
            "--max_concurrency_num", str(max_concurrency),
            "--crawler_max_items_per_minute", str(max_items_per_minute),
            "--crawler_sleep_sec", str(settings.crawler_sleep_seconds),
            "--get_comment", "true" if collect_comments else "false",
            "--get_sub_comment", "true" if collect_comments and get_sub_comment else "false",
            "--get_media", "true" if collect_media else "false",
            "--stream_items", "true" if stream_items else "false",
            "--save_data_option", "jsonl",
            "--save_data_path", str(save_root),
        ]

        def only_target(contents: list[dict]) -> list[dict]:
            selected = []
            for item in contents:
                if self._content_identity(item, platform) == content_id:
                    tagged = dict(item)
                    tagged["source_keyword"] = source_keyword
                    selected.append(tagged)
            return selected

        def relay(contents: list[dict], comments: list[dict]) -> None:
            if content_callback is None:
                return
            targeted = only_target(contents)
            if targeted:
                content_callback(targeted, comments)

        output = self._run_command(
            command=command, save_root=save_root, platform=platform, max_notes=1,
            progress_callback=progress_callback, content_callback=relay if content_callback else None,
            stop_checker=stop_checker, auth_state=auth_state, started_callback=started_callback,
            checkpoint_callback=None, account_id=account_id,
        )
        output.contents = only_target(output.contents)
        output.command = command
        return output

    def _run_command(
        self,
        command: list[str],
        save_root: Path,
        platform: str,
        max_notes: int,
        progress_callback: ProgressCallback | None,
        content_callback: ContentCallback | None,
        stop_checker: StopChecker | None = None,
        auth_state: dict | None = None,
        started_callback: StartedCallback | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
        account_id: str = "",
    ) -> CrawlOutput:
        from backend.task_admission.resources import account_lease, file_lease
        while True:
            if stop_checker and stop_checker():
                raise RuntimeError("采集任务在等待账号资源时已停止")
            with ExitStack() as leases:
                legacy_acquired = settings.app_auth_mode == "required" or leases.enter_context(
                    file_lease(self.media_crawler_dir / ".xhs-audit-crawler.lock")
                )
                acquired = legacy_acquired and leases.enter_context(account_lease(platform, account_id))
                if acquired:
                    try:
                        return self._run_command_locked(
                            command, save_root, platform, max_notes, progress_callback,
                            content_callback, stop_checker, auth_state, started_callback,
                            checkpoint_callback, account_id,
                        )
                    except OSError:
                        # Preparation I/O can fail before spawn (e.g. log directory).
                        # This never retracts a prior launch barrier.
                        from backend.task_admission.execution import current_execution
                        execution = current_execution()
                        if execution:
                            store, task_id, token = execution
                            store.system_fault(task_id, token, "crawler_io_failed")
                        raise
            sleep(0.1)

    def _run_command_locked(
        self, command: list[str], save_root: Path, platform: str, max_notes: int,
        progress_callback: ProgressCallback | None, content_callback: ContentCallback | None,
        stop_checker: StopChecker | None = None, auth_state: dict | None = None,
        started_callback: StartedCallback | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
        account_id: str = "",
    ) -> CrawlOutput:
        env = self._subprocess_env(auth_state, platform=platform, account_id=account_id)
        if platform == "dy":
            command = [*command,
                "--request_scheduler_db", str(settings.request_scheduler_db),
                "--request_min_interval", str(settings.request_min_interval),
                "--requests_per_minute", str(settings.requests_per_minute),
                "--request_concurrency", str(settings.request_concurrency),
                "--media_request_interval", str(settings.media_request_interval),
                "--request_cooldown_seconds", str(settings.request_cooldown_seconds),
                "--headless", "true"]
        save_root.mkdir(parents=True, exist_ok=True)

        stdout_path = save_root / "mediacrawler_stdout.log"
        stderr_path = save_root / "mediacrawler_stderr.log"
        with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as stderr_file:
            from backend.task_admission.execution import crawler_fds, launch_crawler
            completed = launch_crawler(
                command,
                pass_fds=crawler_fds(),
                cwd=self.media_crawler_dir,
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=os.name != "nt",
            )
            last_count = -1
            seen_content_ids: set[str] = set()
            stop_reason = ""
            terminate_sent = False
            kill_sent = False
            stop_error = ""
            cleanup_failed = False
            operation_error: BaseException | None = None

            def append_stop_error(message: str) -> None:
                nonlocal stop_error
                stop_error = f"{stop_error}; {message}" if stop_error else message

            def stop_process(reason: str) -> None:
                nonlocal terminate_sent
                nonlocal kill_sent
                nonlocal stop_reason
                nonlocal cleanup_failed
                if not stop_reason:
                    stop_reason = reason
                if completed.returncode is not None:
                    return

                terminate_just_sent = False
                if not terminate_sent:
                    terminate_sent = True
                    try:
                        if os.name != 'nt' and isinstance(getattr(completed, 'pid', None), int):
                            os.killpg(completed.pid, signal.SIGTERM)
                        else:
                            completed.terminate()
                        terminate_just_sent = True
                    except Exception as exc:
                        append_stop_error(f"terminate failed: {exc}")
                        completed.poll()

                if completed.returncode is None or terminate_just_sent:
                    try:
                        completed.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        append_stop_error("terminate timed out after 10 seconds")
                    except Exception as exc:
                        append_stop_error(f"wait after terminate failed: {exc}")

                if completed.returncode is None and not kill_sent:
                    kill_sent = True
                    try:
                        if os.name != 'nt' and isinstance(getattr(completed, 'pid', None), int):
                            os.killpg(completed.pid, signal.SIGKILL)
                        else:
                            completed.kill()
                    except Exception as exc:
                        append_stop_error(f"kill failed: {exc}")
                        cleanup_failed = True
                    if completed.returncode is None:
                        try:
                            completed.wait(timeout=5)
                        except Exception as exc:
                            append_stop_error(f"wait after kill failed: {exc}")
                            cleanup_failed = True
                elif completed.returncode is None:
                    cleanup_failed = True

                # Launcher exit alone does not prove its worker exited. This is
                # the private group created by Popen, never another task's group.
                if os.name != 'nt' and isinstance(getattr(completed, 'pid', None), int):
                    try:
                        os.killpg(completed.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError as exc:
                        append_stop_error(f"process group cleanup failed: {exc}")
                        cleanup_failed = True

            def raise_exit_failure(returncode: int | None) -> None:
                stdout_file.flush()
                stderr_file.flush()
                stdout = (
                    stdout_path.read_text(encoding="utf-8", errors="replace")
                    if stdout_path.exists()
                    else ""
                )
                stderr = (
                    stderr_path.read_text(encoding="utf-8", errors="replace")
                    if stderr_path.exists()
                    else ""
                )
                if "PLATFORM_RATE_LIMITED" in stdout or "PLATFORM_RATE_LIMITED" in stderr:
                    raise CrawlerRateLimitError("平台请求被限流，共享冷却已记录；保留已采集内容，冷却结束后再继续")
                if "REQUEST_SCHEDULER_WAIT_EXCEEDED" in stdout or "REQUEST_SCHEDULER_WAIT_EXCEEDED" in stderr:
                    raise CrawlerRateLimitError("等待共享请求额度或冷却超时；未启动后续请求，请稍后继续")
                if ACCOUNT_AUTH_INVALID_MARKER in stdout or ACCOUNT_AUTH_INVALID_MARKER in stderr:
                    raise CrawlerAuthenticationError("所选采集账号登录态已失效，请重新登录")
                if "ACCOUNT_VERIFY" in stdout or "ACCOUNT_VERIFY" in stderr:
                    raise CrawlerVerificationError("采集账号触发平台验证")
                if "COLLECTION_INCOMPLETE" in stdout or "COLLECTION_INCOMPLETE" in stderr:
                    raise CrawlerCollectionIncompleteError(
                        "采集未完成：详情、媒体或分页处理失败；已成功内容保留，失败记录在采集目录 douyin/collection_status 中，可修复后继续采集")
                raise RuntimeError(
                    f"MediaCrawler failed with exit code {returncode}\n"
                    f"STDOUT:\n{stdout[-4000:]}\n"
                    f"STDERR:\n{stderr[-4000:]}"
                )

            try:
                if started_callback:
                    started_callback()
                while completed.poll() is None:
                    if stop_checker and stop_checker():
                        stop_process("external_stop_requested")
                        break
                    current_count = self._latest_content_count(save_root, platform)
                    if current_count != last_count:
                        last_count = current_count
                        if progress_callback:
                            progress_callback(min(current_count, max_notes), max_notes)
                    if content_callback:
                        self._emit_new_content(
                            save_root,
                            platform,
                            seen_content_ids,
                            content_callback,
                        )
                    if checkpoint_callback and stderr_path.exists():
                        try:
                            log_text = stderr_path.read_text(encoding="utf-8", errors="replace")
                            checkpoint = latest_search_checkpoint(log_text, platform)
                            if checkpoint:
                                checkpoint_callback(*checkpoint)
                        except OSError:
                            pass
                    sleep(1)

                final_count = self._latest_content_count(save_root, platform)
                if final_count != last_count and progress_callback:
                    progress_callback(min(final_count, max_notes), max_notes)
                if content_callback:
                    self._emit_new_content(
                        save_root,
                        platform,
                        seen_content_ids,
                        content_callback,
                    )

                returncode = completed.returncode
                if returncode is None:
                    append_stop_error("process did not expose an exit code after stop")
                elif not kill_sent and stop_reason != "external_stop_requested" and returncode != 0:
                    raise_exit_failure(returncode)

                # A caught platform limit must never look like successful completion.
                terminal_log = stderr_path.read_text(encoding='utf-8', errors='replace') + stdout_path.read_text(encoding='utf-8', errors='replace')
                if 'PLATFORM_RATE_LIMITED' in terminal_log or 'REQUEST_SCHEDULER_WAIT_EXCEEDED' in terminal_log:
                    raise CrawlerRateLimitError("平台请求额度或冷却限制已生效；保留已采集内容，请稍后继续")

                # MediaCrawler may catch a platform response and exit cleanly;
                # inspect its log marker even when the process return code is 0.
                if stderr_path.exists() and "ACCOUNT_VERIFY" in stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                ):
                    raise CrawlerVerificationError("采集账号触发平台验证")

                if 'COLLECTION_INCOMPLETE' in terminal_log:
                    raise CrawlerCollectionIncompleteError(
                        "采集未完成：详情、媒体或分页处理失败；已成功内容保留，请查看采集目录 douyin/collection_status 中的失败记录")

                output = self.load_latest_output(save_root, platform)
                output.command = command
                return output
            except BaseException as exc:
                operation_error = exc
                raise
            finally:
                if completed.returncode is None:
                    try:
                        stop_process("exception_cleanup")
                    except BaseException as cleanup_exc:
                        append_stop_error(f"cleanup failed: {cleanup_exc}")
                if completed.returncode is None:
                    append_stop_error("process still running after cleanup")
                    cleanup_failed = True
                if cleanup_failed and operation_error is None:
                    raise RuntimeError(
                        f"MediaCrawler process cleanup failed: {stop_error}"
                    )

    def run_xhs_search(self, **kwargs) -> CrawlOutput:
        return self.run_search(platform="xhs", **kwargs)

    def _base_command(self, platform: str) -> list[str]:
        return self._build_runner()

    def _subprocess_env(self, auth_state: dict | None = None, *, platform: str = "", account_id: str = "") -> dict[str, str]:
        env = os.environ.copy()
        for name in ("MEDIACRAWLER_ACCOUNT_ID", "MEDIACRAWLER_CLOAK_PROFILE_ROOT", "MEDIACRAWLER_DY_BROWSER_ENGINE", *scheduler_env()):
            env.pop(name, None)
        if platform == "dy":
            env.update(account_browser_env(account_id))
            env.update(scheduler_env())
        path_parts = [
            str(settings.root_dir / "tools" / "node" / "bin"),
            env.get("PATH", ""),
        ]
        env["PATH"] = os.pathsep.join(part for part in path_parts if part)
        env.setdefault("EXECJS_RUNTIME", "Node")
        # A Douyin CloakBrowser profile marked ``auth-imported`` owns the live
        # credentials. MediaCrawler intentionally refuses to replay the older
        # database snapshot into that profile, so passing the snapshot is both
        # redundant and unsafe: a storage_state larger than Linux MAX_ARG_STRLEN
        # makes execve fail with E2BIG before Python starts.
        profile_owns_auth = (
            platform == "dy"
            and bool(account_id)
            and account_profile_owns_auth(account_id)
        )
        if auth_state is not None and not profile_owns_auth:
            payload = json.dumps(
                auth_state,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            env[ACCOUNT_AUTH_STATE_ENV] = base64.b64encode(payload).decode("ascii")
        else:
            env.pop(ACCOUNT_AUTH_STATE_ENV, None)
        return env

    def _build_runner(self) -> list[str]:
        configured_python = settings.crawler_login_python
        if configured_python.is_file():
            # Keep the venv launcher path itself. Resolving its ``python``
            # symlink to /usr/bin/python discards pyvenv.cfg and therefore the
            # crawler-only dependencies installed in that environment.
            return [str(configured_python.expanduser().absolute()), "main.py"]

        uv_path = shutil.which("uv")
        if uv_path:
            return [uv_path, "run", "main.py"]

        for venv_python in (
            self.media_crawler_dir / ".venv" / "bin" / "python",
            self.media_crawler_dir / ".venv" / "Scripts" / "python.exe",
        ):
            if venv_python.exists():
                return [str(venv_python), "main.py"]

        return ["python", "main.py"]

    def load_latest_output(self, save_root: Path, platform: str) -> CrawlOutput:
        self._validate_platform(platform)
        platform_dir = save_root / PLATFORM_DATA_DIRS[platform] / "jsonl"
        if not platform_dir.exists():
            return CrawlOutput(platform=platform, contents=[], comments=[], output_dir=save_root)

        content_files = sorted(platform_dir.glob("*_contents_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        comment_files = sorted(platform_dir.glob("*_comments_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        creator_files = sorted(platform_dir.glob("*_creators_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

        contents = self._read_jsonl(content_files[0]) if content_files else []
        comments = self._read_jsonl(comment_files[0]) if comment_files else []
        creators = self._read_jsonl(creator_files[0]) if creator_files else []
        return CrawlOutput(platform=platform, contents=contents, comments=comments, creators=creators, output_dir=save_root)

    def load_latest_xhs_output(self, save_root: Path) -> CrawlOutput:
        return self.load_latest_output(save_root, "xhs")

    def list_existing_outputs(self) -> list[dict]:
        outputs = []
        root = settings.outputs_dir
        if not root.exists():
            return outputs

        for output_root in root.iterdir():
            if not output_root.is_dir():
                continue
            crawl_root = output_root / "crawler"
            if not crawl_root.exists():
                continue

            for platform in SUPPORTED_PLATFORMS:
                loaded = self.load_latest_output(crawl_root, platform)
                if not loaded.contents and not loaded.comments:
                    continue

                platform_dir = PLATFORM_DATA_DIRS[platform]
                image_count = self._count_files(crawl_root / platform_dir / "images", IMAGE_EXTENSIONS)
                video_count = self._count_files(crawl_root / platform_dir / "videos", VIDEO_EXTENSIONS)
                outputs.append({
                    "id": output_root.name,
                    "platform": platform,
                    "path": str(output_root),
                    "contents_count": len(loaded.contents),
                    "comments_count": len(loaded.comments),
                    "image_count": image_count,
                    "video_count": video_count,
                    "modified_at": self._latest_mtime(output_root),
                })

        return sorted(outputs, key=lambda item: item["modified_at"], reverse=True)

    def _count_files(self, root: Path, extensions: set[str]) -> int:
        if not root.exists():
            return 0
        return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions)

    def _latest_content_count(self, save_root: Path, platform: str) -> int:
        platform_dir = save_root / PLATFORM_DATA_DIRS[platform] / "jsonl"
        if not platform_dir.exists():
            return 0
        content_files = sorted(platform_dir.glob("*_contents_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not content_files:
            return 0
        return len(self._read_jsonl(content_files[0]))

    def _emit_new_content(
        self,
        save_root: Path,
        platform: str,
        seen_content_ids: set[str],
        callback: ContentCallback,
    ) -> None:
        output = self._load_platform_output(save_root, platform)
        new_contents = []
        for item in output.contents:
            content_id = self._content_identity(item, platform)
            if not content_id or content_id in seen_content_ids:
                continue
            seen_content_ids.add(content_id)
            new_contents.append(item)
        if new_contents:
            for item in new_contents:
                callback([item], output.comments)
        else:
            callback([], output.comments)

    def _load_platform_output(self, save_root: Path, platform: str) -> CrawlOutput:
        """Load only the selected platform directory while the process is live."""
        platform_dir = save_root / PLATFORM_DATA_DIRS[platform] / "jsonl"
        if not platform_dir.exists():
            return CrawlOutput(
                platform=platform,
                contents=[],
                comments=[],
                output_dir=save_root,
            )
        content_files = sorted(platform_dir.glob("*_contents_*.jsonl"))
        comment_files = sorted(platform_dir.glob("*_comments_*.jsonl"))
        creator_files = sorted(platform_dir.glob("*_creators_*.jsonl"))
        return CrawlOutput(
            platform=platform,
            contents=[item for path in content_files for item in self._read_jsonl(path)],
            comments=[item for path in comment_files for item in self._read_jsonl(path)],
            creators=[item for path in creator_files for item in self._read_jsonl(path)],
            output_dir=save_root,
        )

    def _content_identity(self, item: dict, platform: str) -> str:
        fields = {
            "xhs": ("note_id", "note_url"),
            "dy": ("aweme_id", "note_id", "aweme_url"),
            "ks": ("video_id", "note_id", "video_url"),
        }.get(platform, ("note_id",))
        for field in fields:
            value = item.get(field)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                if value > 0:
                    return str(value)
                continue
            if not isinstance(value, str):
                continue
            cleaned = value.strip()
            if cleaned != value or cleaned.lower() in {"", "none", "null", "undefined"}:
                continue
            return cleaned
        return ""

    def _latest_mtime(self, root: Path) -> float:
        latest = root.stat().st_mtime
        for path in root.rglob("*"):
            if path.is_file():
                latest = max(latest, path.stat().st_mtime)
        return latest

    def _read_jsonl(self, path: Path) -> list[dict]:
        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(value, dict):
                    continue
                rows.append(value)
        return rows

    def _validate_platform(self, platform: str) -> None:
        if platform not in SUPPORTED_PLATFORMS:
            supported = ", ".join(sorted(SUPPORTED_PLATFORMS))
            raise ValueError(f"Unsupported platform: {platform}. Supported: {supported}")

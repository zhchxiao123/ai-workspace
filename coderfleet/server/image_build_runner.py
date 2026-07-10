"""
image_build_runner.py — 驱动一次 `docker build`，与触发它的 HTTP 请求生命周期解耦

背景：早先 build_project_image() 把 `docker build` 子进程直接挂在 SSE 生成器里驱动——
浏览器断开连接时 FastAPI 会 cancel 该生成器，进而终止仍在运行的构建。run_image_build()
被设计为通过 asyncio.create_task() 启动，不属于任何请求：子进程的生命周期只取决于它自己
何时退出，或被显式 cancel（见 ImageBuildRegistry.cancel）。

构建输出实时追加写入 builds/{id}.log；调用方（HTTP 端点、SSE 流）通过轮询这个文件的
字节增量来读日志，不再依赖直接读子进程 stdout，因此“查看构建”和“构建本身”是两件互不
影响的事——关掉查看端不会牵连构建。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Optional

from coderfleet.server.image_builds import ImageBuildRegistry
from coderfleet.server.models import ImageBuild, ImageBuildStatus


async def run_image_build(
    build: ImageBuild,
    builds_dir: Path,
    dockerfile: Path,
    context_dir: Path,
    platform: str,
    registry: ImageBuildRegistry,
    on_success: Optional[Callable[[str], None]] = None,
) -> None:
    log_path = builds_dir / f"{build.id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f">>> 构建 ID：{build.id}\n>>> 构建镜像 {build.image_tag}（平台：{platform}）...\n",
        encoding="utf-8",
    )
    build.save(builds_dir)

    rc: Optional[int] = None
    cancelled = False
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "build",
            "--platform", platform,
            "--tag", build.image_tag,
            "--file", str(dockerfile),
            str(context_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        registry.track(build.id, build.project_name, proc)
        assert proc.stdout is not None
        with log_path.open("a", encoding="utf-8") as f:
            async for line in proc.stdout:
                f.write(line.decode("utf-8", errors="replace"))
        rc = await proc.wait()
    except Exception as e:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n✗ 构建出错：{e}\n")
    finally:
        cancelled = registry.was_cancelled(build.id)
        registry.forget(build.id)

    # rc == 0 优先于 cancelled 判断：docker build 已经跑完并成功退出，
    # 就算 DELETE 请求恰好在这最后一刻落到 registry 上（asyncio 协作式调度下，
    # cancel_requested 和"进程其实已经正常退出"可能在同一轮事件循环里先后发生），
    # 也不该把一次成功的构建误记成"已停止"、连带漏掉 on_success 的 projects.conf 写回。
    if rc == 0:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n✓ 镜像构建完成：{build.image_tag}\n")
        build.update_status(ImageBuildStatus.succeeded, builds_dir, exit_code=rc)
        if on_success is not None:
            on_success(build.image_tag)
    elif cancelled:
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n✗ 构建已停止\n")
        build.update_status(ImageBuildStatus.cancelled, builds_dir, exit_code=rc)
    else:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n✗ 构建失败（exit={rc}）\n")
        build.update_status(ImageBuildStatus.failed, builds_dir, exit_code=rc)

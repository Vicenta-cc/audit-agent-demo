"""Offline Linux headed smoke. Requires explicit executable and preinstalled assets."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from control import atomic_json, preflight, read_json, require, validate


async def run(config, receipt_path):
    preflight(config)
    from camoufox.addons import DefaultAddons
    from camoufox.async_api import AsyncCamoufox

    r = config["runtime"]
    # Explicit binary, no SDK version resolution, no default add-on downloader.
    # Preflight validates cache permissions. GeoIP/model assets must already exist;
    # network-isolated Docker smoke is the final proof of offline operation.
    read_fd, write_fd = os.pipe()
    display = subprocess.Popen(  # noqa: ASYNC220 - Spawn only; process waits use executor deadlines.
        [
            "Xvfb",
            "-displayfd",
            str(write_fd),
            "-screen",
            "0",
            "1280x800x24",
            "-nolisten",
            "tcp",
        ],
        pass_fds=(write_fd,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(write_fd)
    try:
        display_number = await asyncio.wait_for(
            asyncio.to_thread(os.read, read_fd, 32), 10
        )
        require(display_number.strip().isdigit(), "xvfb_start_failed")
        os.environ["DISPLAY"] = ":" + display_number.decode().strip()
        os.environ["HOME"] = r["home"]
        os.environ["XDG_CACHE_HOME"] = r["cache_root"]
        os.environ["TMPDIR"] = r["tmp_root"]
        os.environ["LANG"] = "en_US.UTF-8"
        os.environ["LANGUAGE"] = "en_US:en"
        async with AsyncCamoufox(
            headless=False,
            executable_path=config["artifacts"]["browser_binary"]["path"],
            exclude_addons=list(DefaultAddons),
            geoip=False,
            os="linux",
            locale="en-US",
            persistent_context=True,
            user_data_dir=str(Path(r["tmp_root"]) / "offline-smoke-profile"),
        ) as context:
            page = await context.new_page()
            await page.goto(
                'data:text/html,<title>offline-smoke</title><div style="height:3000px">offline</div>'
            )
            await page.mouse.wheel(0, 500)
            require(await page.title() == "offline-smoke", "browser_page_failed")
            await page.goto("about:support")
            # Firefox diagnostic table reports the effective content sandbox level.
            row = page.locator("tr").filter(has_text="Effective Content Sandbox Level")
            sandbox = (
                await row.locator("td").last.text_content()
                if await row.count()
                else None
            )
            require(
                sandbox is not None
                and sandbox.strip().isdigit()
                and int(sandbox.strip()) > 0,
                "sandbox_not_proven",
            )
        receipt = {
            "schema_version": 1,
            "release_id": config["release_id"],
            "passed": True,
            "headed": True,
            "uid": os.geteuid(),
            "sandbox_level": int(sandbox.strip()),
            "offline_network_required": True,
        }
        atomic_json(receipt_path, receipt)
        return receipt
    finally:
        os.close(read_fd)
        display.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(display.wait), 5)
        except asyncio.TimeoutError:
            display.kill()
            await asyncio.to_thread(display.wait)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                asyncio.run(
                    asyncio.wait_for(
                        run(validate(read_json(args.manifest)), args.receipt), 90
                    )
                )
            )
        )
    except Exception:  # noqa: BLE001 - CLI boundary emits a fixed failure code without secrets.
        print('{"passed":false,"error":"offline_smoke_failed"}', file=sys.stderr)
        raise SystemExit(2)

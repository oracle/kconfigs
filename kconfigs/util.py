# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import hashlib
import io
import posixpath
from asyncio import Semaphore
from asyncio.subprocess import create_subprocess_exec
from asyncio.subprocess import PIPE
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiofiles
from aiofiles.tempfile import TemporaryDirectory
from aiohttp import ClientResponseError
from aiohttp import ClientSession
from multidict import CIMultiDictProxy


HTTPS_HOSTS = {
    "yum.oracle.com",
    "source.android.com",
    "dl.fedoraproject.org",
    "download.copr.fedorainfracloud.org",
}


class DownloadManager:
    RETRIES = 3

    def __init__(self, max_downloads: int = 8):
        self.session = ClientSession(raise_for_status=True)
        self.sem = Semaphore(max_downloads)

    async def head(self, url: str) -> CIMultiDictProxy[str]:
        async with self.sem:
            print(f"HTTP HEAD {url}")
            resp = await self.session.head(url)
            return resp.headers

    async def download_file(
        self,
        url: str,
        file: Path,
        always_download: bool = False,
        checksum: tuple[str, str] | None = None,
    ) -> None:
        if checksum:
            h = hashlib.new(checksum[0], usedforsecurity=True)
        if file.exists() and not always_download:
            # Prevents duplicate work during development
            print(f"Skip download {file}")
            return
        errors = []
        for i in range(self.RETRIES):
            async with self.sem:
                try:
                    print(
                        f"Download {url} to {file} [try {i + 1}/{self.RETRIES}]"
                    )
                    async with self.session.get(url) as resp, aiofiles.open(
                        file, "wb"
                    ) as out:
                        async for chunk in resp.content.iter_chunked(4096):
                            if checksum:
                                h.update(chunk)
                            await out.write(chunk)
                    break
                except ClientResponseError as err:
                    file.unlink(missing_ok=True)
                    if err.status == 404:
                        # retrying won't help, raise
                        raise
                    # otherwise, wait a second and retry
                    errors.append(err)
                except BaseException:
                    file.unlink(missing_ok=True)
                    raise
            await asyncio.sleep(1)
        else:
            # loop terminated after retries,
            raise Exception(
                f"Failed to download {url} after {self.RETRIES} retries: "
                f"{errors}"
            )
        if checksum:
            digest = h.hexdigest()
            if digest != checksum[1]:
                raise Exception(
                    f"Failed to verify {checksum[0]} checksum of {url}:\n"
                    f"Expected: {checksum[1]}\n",
                    f"Actual  : {digest}",
                )
            else:
                print(f"Verified {checksum[0]} of {url}")

    async def download_file_mem(
        self, url: str, checksum: tuple[str, str] | None = None
    ) -> bytes:
        if checksum:
            h = hashlib.new(checksum[0])
        errors = []
        for i in range(self.RETRIES):
            out = io.BytesIO()
            try:
                async with self.sem, self.session.get(url) as resp:
                    print(f"Download {url} to mem [try {i + 1}/{self.RETRIES}]")
                    async for chunk in resp.content.iter_chunked(4096):
                        if checksum:
                            h.update(chunk)
                        out.write(chunk)
                break
            except ClientResponseError as err:
                if err.status == 404:
                    raise
                errors.append(err)
            await asyncio.sleep(1)
        else:
            # loop terminated after retries,
            raise Exception(
                f"Failed to download {url} after {self.RETRIES} retries: "
                f"{errors}"
            )
        if checksum:
            digest = h.hexdigest()
            if digest != checksum[1]:
                raise Exception(
                    f"Failed to verify {checksum[0]} checksum of {url}:\n"
                    f"Expected: {checksum[1]}\n",
                    f"Actual  : {digest}",
                )
            else:
                print(f"Verified {checksum[0]} of {url}")
        return out.getvalue()


@cache
def download_manager() -> DownloadManager:
    return DownloadManager()


async def download_file(
    url: str,
    file: Path,
    always_download: bool = False,
    checksum: tuple[str, str] | None = None,
) -> None:
    return await download_manager().download_file(
        url,
        file,
        always_download=always_download,
        checksum=checksum,
    )


async def download_file_mem(
    url: str, checksum: tuple[str, str] | None = None
) -> bytes:
    return await download_manager().download_file_mem(url, checksum=checksum)


async def head_file(url: str) -> CIMultiDictProxy[str]:
    return await download_manager().head(url)


async def check_call(
    cmd: list[str | Path], capture_output: bool = False, **kwargs: Any
) -> bytes:
    if capture_output:
        kwargs["stdout"] = PIPE
    proc = await create_subprocess_exec(*cmd, **kwargs)
    output = b""
    if capture_output:
        output, _ = await proc.communicate()
    code = await proc.wait()
    assert code == 0
    return output


async def maybe_decompress(file: Path) -> Path:
    compressors = {
        "xz": "unxz",
        "bz2": "bunzip2",
        "gz": "gunzip",
        "zst": "unzstd",
        "zstd": "unzstd",
    }
    ext = None
    split = file.name.rsplit(".", 1)
    if len(split) == 2:
        ext = split[1]
    if not ext or ext not in compressors:
        return file

    decomp = file.parent / file.name[: -1 - len(ext)]
    if not decomp.exists():
        await check_call([compressors[ext], "-kq", file])
    return decomp


async def gpg_verify(file: Path, sig: Path, key: str) -> bool:
    key_path = Path(__file__).parent.parent.resolve() / f"gpg-keys/{key}.gpg"
    proc = await create_subprocess_exec(
        "/usr/bin/gpg",
        "--no-default-keyring",
        "--keyring",
        key_path.absolute(),
        "--verify",
        sig,
        file,
        stderr=PIPE,
    )
    _, stderr = await proc.communicate()
    code = await proc.wait()
    # From gpg(1): "the program returns 0 if there are no severe errors, 1 if at
    # least a signature was bad, and other errors codes for fatal errors."
    # Handle 0 and 1 as our desired output (yes/no) and 2 as some other error
    # which we should report.
    if code not in (0, 1):
        raise Exception(
            f"GPG error: key: {key} file: {file}\n{stderr.decode()}"
        )
    return code == 0


def trusted_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in HTTPS_HOSTS


async def download_file_mem_verified(
    url: str, key: str | None, https_ok: bool = False, suffix: str = ".asc"
) -> bytes:
    async with TemporaryDirectory() as td:
        tdpath = Path(td)
        filename = posixpath.split(url)[-1]
        file_path = tdpath / filename
        sig_path = tdpath / f"{filename}{suffix}"

        sig_exists = True

        # start downloading the larger file, but don't wait for it yet
        file_download = download_file(url, file_path)
        try:
            await download_file(url + suffix, sig_path)
        except ClientResponseError as err:
            if err.status != 404:
                raise
            sig_exists = False

        await file_download

        if sig_exists:
            if not key:
                raise Exception(f"Missing GPG key for {url}")
            elif not await gpg_verify(file_path, sig_path, key):
                raise Exception(f"Bad GPG signature: {url}")
            else:
                print(f"Good GPG signature [{key}]: {url}")
        elif https_ok and trusted_url(url):
            print(
                f"warning: {url} not GPG-signed, but fetched from trusted host via HTTPS"
            )
        else:
            raise Exception(f"Missing GPG signature: {url}")

        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()

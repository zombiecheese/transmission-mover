from __future__ import annotations

import os
import posixpath
import shlex
import stat
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import paramiko

from app.models import AppConfig, Destination
from app.ssh_utils import exec_remote_command, parse_private_key, remote_has_cmd


class InsufficientSpaceError(RuntimeError):
    pass


ProgressCallback = Callable[[int, int], None]


def transfer_to_destination(
    torrent_name: str,
    download_dir: str | None,
    destination: Destination,
    app_config: AppConfig | None,
    transfer_mode_override: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> str:
    transfer_mode = (transfer_mode_override or (app_config.transfer_mode if app_config else "move")).lower()
    if transfer_mode not in {"move", "copy"}:
        raise ValueError(f"Unsupported transfer mode: {transfer_mode}")

    watch_source_kind = (app_config.watch_source_kind if app_config else "local").lower()
    if watch_source_kind == "local":
        source_path = _resolve_local_source_path(download_dir, torrent_name, app_config)
        method_used = _transfer_local_source(source_path, destination, torrent_name, transfer_mode, progress_callback)
        if method_used == "local":
            return "Moved to destination" if transfer_mode == "move" else "Copied to destination"
        return (
            f"Moved to destination via {method_used}"
            if transfer_mode == "move"
            else f"Copied to destination via {method_used}"
        )

    if watch_source_kind == "sftp":
        method_used = _transfer_sftp_source(torrent_name, destination, app_config, transfer_mode, progress_callback)
        return (
            f"Moved from remote watch source via {method_used}"
            if transfer_mode == "move"
            else f"Copied from remote watch source via {method_used}"
        )

    raise ValueError(f"Unsupported watch source kind: {watch_source_kind}")


def _apply_path_remap(download_dir: str, app_config: AppConfig | None) -> str:
    """Translate a Transmission-reported path to the path visible inside this container."""
    if not app_config or not app_config.remap_download_path:
        return download_dir
    source = app_config.remap_source_prefix or ""
    target = app_config.remap_target_prefix or ""
    if source and download_dir.startswith(source):
        return target + download_dir[len(source):]
    return download_dir


def _resolve_local_source_path(download_dir: str | None, torrent_name: str, app_config: AppConfig | None) -> str:
    if app_config and app_config.watch_base_path:
        return str(Path(app_config.watch_base_path) / torrent_name)
    if not download_dir:
        raise FileNotFoundError("Missing downloadDir and no watch_base_path configured")
    remapped = _apply_path_remap(download_dir, app_config)
    return str(Path(remapped) / torrent_name)


def _transfer_local_source(
    source_path: str,
    destination: Destination,
    torrent_name: str,
    transfer_mode: str,
    progress_callback: ProgressCallback | None = None,
) -> str:
    if destination.kind == "local":
        _transfer_local_to_local(source_path, destination.base_path, torrent_name, transfer_mode, progress_callback)
        return "local"

    if destination.kind in {"sftp", "remote"}:
        return _transfer_local_to_remote(source_path, destination, torrent_name, transfer_mode, progress_callback)

    raise ValueError(f"Unsupported destination kind: {destination.kind}")


def _transfer_local_to_remote(
    source_path: str,
    destination: Destination,
    torrent_name: str,
    transfer_mode: str,
    progress_callback: ProgressCallback | None = None,
) -> str:
    source = Path(source_path)
    required_bytes = _get_local_path_size(source)

    candidates = _get_remote_method_candidates(destination)

    for method in candidates:
        if method in {"rsync", "scp"}:
            try:
                _transfer_local_to_remote_via_shell(
                    source_path,
                    destination,
                    torrent_name,
                    transfer_mode,
                    method,
                    required_bytes,
                    progress_callback,
                )
                return method
            except Exception:
                # Fall through to next candidate and eventually sftp fallback.
                continue

        if method == "sftp":
            _transfer_local_to_sftp(source_path, destination, torrent_name, transfer_mode, progress_callback)
            return "sftp"

    _transfer_local_to_sftp(source_path, destination, torrent_name, transfer_mode, progress_callback)
    return "sftp"


def _get_remote_method_candidates(destination: Destination) -> list[str]:
    preferred = (destination.transfer_method_preference or "auto").lower()
    detected = (destination.detected_preferred_method or "").lower()
    if preferred == "auto":
        first = detected if detected in {"rsync", "scp", "sftp"} else "sftp"
        return [first] + [m for m in ["rsync", "scp", "sftp"] if m != first]
    return [preferred] + [m for m in ["rsync", "scp", "sftp"] if m != preferred]


def _transfer_remote_to_remote_direct(
    source_sftp: paramiko.SFTPClient,
    source_transport: paramiko.Transport,
    remote_source: str,
    destination: Destination,
    torrent_name: str,
    transfer_mode: str,
    required_bytes: int,
    progress_callback: ProgressCallback | None = None,
) -> str:
    # Ensure destination target is reachable and has capacity from mover perspective.
    dest_sftp, dest_transport = _connect_sftp(destination)
    try:
        base_remote = destination.base_path.rstrip("/") or "/"
        _ensure_remote_dirs(dest_sftp, base_remote)
        _ensure_remote_free_space(dest_transport, base_remote, required_bytes, "remote destination")
    finally:
        dest_sftp.close()
        dest_transport.close()

    candidates = _get_remote_method_candidates(destination)
    ssh_port = int(destination.detected_scp_port or destination.port or 22)
    target_remote = f"{destination.username}@{destination.host}:{destination.base_path.rstrip('/')}/{torrent_name}"

    for method in candidates:
        if method not in {"rsync", "scp"}:
            continue

        if not remote_has_cmd(source_transport, method):
            continue

        auth_prefix = ""
        if destination.password:
            if not remote_has_cmd(source_transport, "sshpass"):
                continue
            auth_prefix = f"sshpass -p {shlex.quote(destination.password)} "

        if destination.private_key:
            # We cannot rely on this private key existing on source host filesystem.
            continue

        if method == "rsync":
            cmd = (
                f"{auth_prefix}rsync -a --partial --inplace "
                f"-e \"ssh -p {ssh_port} -o BatchMode=yes -o StrictHostKeyChecking=no\" "
                f"{shlex.quote(remote_source)}/ {shlex.quote(target_remote)}/"
            )
        else:
            cmd = (
                f"{auth_prefix}scp -r -P {ssh_port} -o StrictHostKeyChecking=no "
                f"{shlex.quote(remote_source)} {shlex.quote(target_remote)}"
            )

        exit_code, _stdout, stderr = exec_remote_command(source_transport, cmd)
        if exit_code != 0:
            continue

        if transfer_mode == "move":
            _remove_remote_path(source_sftp, remote_source)
        if progress_callback:
            progress_callback(required_bytes, required_bytes)
        return method

    raise RuntimeError("No direct remote-to-remote method succeeded")


def _transfer_local_to_remote_via_shell(
    source_path: str,
    destination: Destination,
    torrent_name: str,
    transfer_mode: str,
    method: str,
    required_bytes: int,
    progress_callback: ProgressCallback | None = None,
) -> None:
    if method not in {"rsync", "scp"}:
        raise ValueError(f"Unsupported shell transfer method: {method}")
    if not destination.host or not destination.username:
        raise ValueError("Remote destination requires host and username")

    tool = shutil.which(method)
    if not tool:
        raise RuntimeError(f"{method} binary not available in container")

    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Source path does not exist: {source}")

    # Ensure remote base path exists and has capacity before external transfer command.
    sftp, transport = _connect_sftp(destination)
    try:
        base_remote = destination.base_path.rstrip("/") or "/"
        _ensure_remote_dirs(sftp, base_remote)
        _ensure_remote_free_space(transport, base_remote, required_bytes, "remote destination")
    finally:
        sftp.close()
        transport.close()

    ssh_port = int(destination.detected_scp_port or destination.port or 22)
    remote_target = f"{destination.username}@{destination.host}:{destination.base_path.rstrip('/')}/{torrent_name}"

    key_temp_path: str | None = None
    try:
        auth_prefix: list[str] = []
        ssh_base_opts = ["-p", str(ssh_port), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]

        if destination.private_key:
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as key_file:
                key_file.write(destination.private_key)
                key_temp_path = key_file.name
            os.chmod(key_temp_path, 0o600)
            ssh_base_opts.extend(["-i", key_temp_path])
        elif destination.password:
            sshpass = shutil.which("sshpass")
            if not sshpass:
                raise RuntimeError("password auth for rsync/scp requires sshpass in container")
            auth_prefix = [sshpass, "-p", destination.password]

        if method == "rsync":
            rsync_cmd = [tool, "-a", "--human-readable", "--partial", "--inplace"]
            rsync_cmd.extend(["-e", "ssh " + " ".join(shlex.quote(opt) for opt in ssh_base_opts)])
            if source.is_dir():
                rsync_cmd.extend([str(source) + "/", remote_target + "/"])
            else:
                rsync_cmd.extend([str(source), remote_target])
            cmd = auth_prefix + rsync_cmd
        else:
            scp_cmd = [tool, "-P", str(ssh_port), "-o", "StrictHostKeyChecking=no"]
            if destination.private_key and key_temp_path:
                scp_cmd.extend(["-i", key_temp_path])
            if source.is_dir():
                scp_cmd.append("-r")
            scp_cmd.extend([str(source), remote_target])
            cmd = auth_prefix + scp_cmd

        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            raise RuntimeError(f"{method} transfer failed: {stderr or completed.returncode}")

        if transfer_mode == "move":
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()

        if progress_callback:
            progress_callback(required_bytes, required_bytes)
    finally:
        if key_temp_path:
            try:
                os.remove(key_temp_path)
            except Exception:
                pass


def _transfer_local_to_local(
    source_path: str,
    destination_base: str,
    torrent_name: str,
    transfer_mode: str,
    progress_callback: ProgressCallback | None = None,
) -> None:
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Source path does not exist: {source}")

    destination_dir = Path(destination_base)
    destination_dir.mkdir(parents=True, exist_ok=True)

    required_bytes = _get_local_path_size(source)
    if _requires_local_capacity_check(source, destination_dir, transfer_mode):
        _ensure_local_free_space(destination_dir, required_bytes, "local destination")

    target = destination_dir / torrent_name
    if transfer_mode == "copy" or _requires_local_capacity_check(source, destination_dir, transfer_mode):
        if source.is_dir():
            _copy_local_dir_with_progress(source, target, required_bytes, progress_callback)
        else:
            _copy_local_file_with_progress(source, target, required_bytes, progress_callback)
        if transfer_mode == "move":
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        return

    shutil.move(str(source), str(target))
    if progress_callback:
        progress_callback(required_bytes, required_bytes)


def _connect_sftp(destination: Destination) -> tuple[paramiko.SFTPClient, paramiko.Transport]:
    if not destination.host:
        raise ValueError("SFTP destination requires host")
    if not destination.username:
        raise ValueError("SFTP destination requires username")

    transport = paramiko.Transport((destination.host, destination.port))

    pkey = None
    if destination.private_key:
        pkey = parse_private_key(destination.private_key, destination.key_passphrase)

    transport.connect(username=destination.username, password=destination.password, pkey=pkey)
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
    except paramiko.SSHException as exc:
        transport.close()
        raise RuntimeError(
            "Unable to open SFTP channel on destination. The SSH service is reachable, but SFTP subsystem may be disabled or not allowed on this port/account."
        ) from exc
    return sftp, transport


def _connect_source_sftp(app_config: AppConfig | None) -> tuple[paramiko.SFTPClient, paramiko.Transport]:
    if not app_config:
        raise ValueError("Remote watch source settings are missing")
    if not app_config.watch_host:
        raise ValueError("SFTP watch source requires host")
    if not app_config.watch_username:
        raise ValueError("SFTP watch source requires username")

    transport = paramiko.Transport((app_config.watch_host, app_config.watch_port))

    pkey = None
    if app_config.watch_private_key:
        pkey = parse_private_key(app_config.watch_private_key, app_config.watch_key_passphrase)

    transport.connect(username=app_config.watch_username, password=app_config.watch_password, pkey=pkey)
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
    except paramiko.SSHException as exc:
        transport.close()
        raise RuntimeError(
            "Unable to open SFTP channel on watch source. The SSH service is reachable, but SFTP subsystem may be disabled or not allowed on this port/account."
        ) from exc
    return sftp, transport


def _ensure_remote_dirs(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = [p for p in remote_dir.split("/") if p]
    current = "/"
    for part in parts:
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def _remove_remote_path(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    try:
        entries = sftp.listdir_attr(remote_path)
    except OSError:
        sftp.remove(remote_path)
        return

    for entry in entries:
        child_path = posixpath.join(remote_path, entry.filename)
        try:
            _remove_remote_path(sftp, child_path)
        except FileNotFoundError:
            continue
    sftp.rmdir(remote_path)


def _upload_dir(
    sftp: paramiko.SFTPClient,
    local_dir: Path,
    remote_dir: str,
    total_bytes: int,
    progress_callback: ProgressCallback | None = None,
) -> None:
    _ensure_remote_dirs(sftp, remote_dir)

    transferred_bytes = 0

    for root, dirs, files in os.walk(local_dir):
        rel_root = Path(root).relative_to(local_dir)
        remote_root = posixpath.join(remote_dir, str(rel_root).replace("\\", "/")) if rel_root != Path(".") else remote_dir

        _ensure_remote_dirs(sftp, remote_root)
        for d in dirs:
            _ensure_remote_dirs(sftp, posixpath.join(remote_root, d))

        for f in files:
            local_file = Path(root) / f
            remote_file = posixpath.join(remote_root, f)
            file_size = int(local_file.stat().st_size)
            file_uploaded = 0

            def _file_cb(current: int, _total: int) -> None:
                nonlocal transferred_bytes, file_uploaded
                delta = int(current) - file_uploaded
                if delta <= 0:
                    return
                file_uploaded = int(current)
                transferred_bytes += delta
                if progress_callback:
                    progress_callback(transferred_bytes, total_bytes)

            sftp.put(str(local_file), remote_file, callback=_file_cb)
            if file_uploaded < file_size:
                transferred_bytes += file_size - file_uploaded
                if progress_callback:
                    progress_callback(transferred_bytes, total_bytes)


def _upload_file(
    sftp: paramiko.SFTPClient,
    local_file: Path,
    remote_file: str,
    total_bytes: int,
    progress_callback: ProgressCallback | None = None,
) -> None:
    remote_parent = posixpath.dirname(remote_file)
    _ensure_remote_dirs(sftp, remote_parent)

    uploaded = 0

    def _file_cb(current: int, _total: int) -> None:
        nonlocal uploaded
        uploaded = int(current)
        if progress_callback:
            progress_callback(uploaded, total_bytes)

    sftp.put(str(local_file), remote_file, callback=_file_cb)
    if progress_callback and uploaded < total_bytes:
        progress_callback(total_bytes, total_bytes)


def _transfer_local_to_sftp(
    source_path: str,
    destination: Destination,
    torrent_name: str,
    transfer_mode: str,
    progress_callback: ProgressCallback | None = None,
) -> None:
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Source path does not exist: {source}")

    base_remote = destination.base_path.rstrip("/") or "/"
    target_remote = posixpath.join(base_remote, torrent_name)
    required_bytes = _get_local_path_size(source)

    sftp, transport = _connect_sftp(destination)
    try:
        _ensure_remote_free_space(transport, base_remote, required_bytes, "remote destination")
        if source.is_dir():
            _upload_dir(sftp, source, target_remote, required_bytes, progress_callback)
            if transfer_mode == "move":
                shutil.rmtree(source)
        else:
            _upload_file(sftp, source, target_remote, required_bytes, progress_callback)
            if transfer_mode == "move":
                source.unlink()
        if progress_callback:
            progress_callback(required_bytes, required_bytes)
    finally:
        sftp.close()
        transport.close()


def _download_remote_dir(
    sftp: paramiko.SFTPClient,
    remote_dir: str,
    local_dir: Path,
    total_bytes: int,
    progress_callback: ProgressCallback | None = None,
    transferred_bytes: list[int] | None = None,
) -> None:
    if transferred_bytes is None:
        transferred_bytes = [0]

    local_dir.mkdir(parents=True, exist_ok=True)
    for entry in sftp.listdir_attr(remote_dir):
        remote_path = posixpath.join(remote_dir, entry.filename)
        local_path = local_dir / entry.filename
        try:
            child_entries = sftp.listdir_attr(remote_path)
        except OSError:
            file_downloaded = 0

            def _file_cb(current: int, _total: int) -> None:
                nonlocal file_downloaded
                delta = int(current) - file_downloaded
                if delta <= 0:
                    return
                file_downloaded = int(current)
                transferred_bytes[0] += delta
                if progress_callback:
                    progress_callback(transferred_bytes[0], total_bytes)

            sftp.get(remote_path, str(local_path), callback=_file_cb)
            continue

        _download_remote_dir(sftp, remote_path, local_path, total_bytes, progress_callback, transferred_bytes)


def _download_remote_source(
    sftp: paramiko.SFTPClient,
    remote_path: str,
    local_path: Path,
    total_bytes: int,
    progress_callback: ProgressCallback | None = None,
) -> None:
    try:
        sftp.listdir_attr(remote_path)
    except OSError:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        downloaded = 0

        def _file_cb(current: int, _total: int) -> None:
            nonlocal downloaded
            downloaded = int(current)
            if progress_callback:
                progress_callback(downloaded, total_bytes)

        sftp.get(remote_path, str(local_path), callback=_file_cb)
        if progress_callback and downloaded < total_bytes:
            progress_callback(total_bytes, total_bytes)
        return

    _download_remote_dir(sftp, remote_path, local_path, total_bytes, progress_callback)


def _transfer_sftp_source(
    torrent_name: str,
    destination: Destination,
    app_config: AppConfig | None,
    transfer_mode: str,
    progress_callback: ProgressCallback | None = None,
) -> str:
    if not app_config or not app_config.watch_base_path:
        raise ValueError("watch_base_path is required for sftp watch source")

    remote_source = posixpath.join(app_config.watch_base_path.rstrip("/") or "/", torrent_name)
    source_sftp, source_transport = _connect_source_sftp(app_config)
    try:
        required_bytes = _get_remote_path_size(source_sftp, remote_source)
        if destination.kind in {"sftp", "remote"}:
            try:
                return _transfer_remote_to_remote_direct(
                    source_sftp,
                    source_transport,
                    remote_source,
                    destination,
                    torrent_name,
                    transfer_mode,
                    required_bytes,
                    progress_callback,
                )
            except Exception:
                # Fallback to staged transfer path below.
                pass

        total_work_bytes = max(required_bytes, 1) * 2
        _ensure_local_free_space(Path(tempfile.gettempdir()), required_bytes, "temporary staging")

        if destination.kind == "local":
            _ensure_local_free_space(Path(destination.base_path), required_bytes, "local destination")
        elif destination.kind in {"sftp", "remote"}:
            base_remote = destination.base_path.rstrip("/") or "/"
            dest_sftp, dest_transport = _connect_sftp(destination)
            try:
                _ensure_remote_free_space(dest_transport, base_remote, required_bytes, "remote destination")
            finally:
                dest_sftp.close()
                dest_transport.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            local_cache = Path(temp_dir) / torrent_name
            _download_remote_source(
                source_sftp,
                remote_source,
                local_cache,
                required_bytes,
                (lambda done, _total: progress_callback(done, total_work_bytes)) if progress_callback else None,
            )
            _transfer_local_source(
                str(local_cache),
                destination,
                torrent_name,
                "copy",
                (lambda done, _total: progress_callback(required_bytes + done, total_work_bytes))
                if progress_callback
                else None,
            )
            if transfer_mode == "move":
                _remove_remote_path(source_sftp, remote_source)
            if progress_callback:
                progress_callback(total_work_bytes, total_work_bytes)
            return "staged-sftp"
    finally:
        source_sftp.close()
        source_transport.close()


def _copy_local_file_with_progress(
    source_file: Path,
    target_file: Path,
    total_bytes: int,
    progress_callback: ProgressCallback | None,
    transferred_bytes: list[int] | None = None,
) -> None:
    if transferred_bytes is None:
        transferred_bytes = [0]

    target_file.parent.mkdir(parents=True, exist_ok=True)
    chunk_size = 4 * 1024 * 1024

    with source_file.open("rb") as src, target_file.open("wb") as dst:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            dst.write(chunk)
            transferred_bytes[0] += len(chunk)
            if progress_callback:
                progress_callback(transferred_bytes[0], total_bytes)


def _copy_local_dir_with_progress(
    source_dir: Path,
    target_dir: Path,
    total_bytes: int,
    progress_callback: ProgressCallback | None,
) -> None:
    transferred = [0]
    target_dir.mkdir(parents=True, exist_ok=True)

    for root, dirs, files in os.walk(source_dir):
        rel_root = Path(root).relative_to(source_dir)
        target_root = target_dir / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        for dir_name in dirs:
            (target_root / dir_name).mkdir(parents=True, exist_ok=True)

        for file_name in files:
            src_file = Path(root) / file_name
            dst_file = target_root / file_name
            _copy_local_file_with_progress(src_file, dst_file, total_bytes, progress_callback, transferred)


def _get_local_path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size

    total = 0
    for root, _dirs, files in os.walk(path):
        root_path = Path(root)
        for file_name in files:
            file_path = root_path / file_name
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


def _is_remote_dir(sftp: paramiko.SFTPClient, remote_path: str) -> bool:
    attrs = sftp.stat(remote_path)
    return stat.S_ISDIR(attrs.st_mode)


def _get_remote_path_size(sftp: paramiko.SFTPClient, remote_path: str) -> int:
    if not _is_remote_dir(sftp, remote_path):
        return int(sftp.stat(remote_path).st_size)

    total = 0
    for entry in sftp.listdir_attr(remote_path):
        child_path = posixpath.join(remote_path, entry.filename)
        if stat.S_ISDIR(entry.st_mode):
            total += _get_remote_path_size(sftp, child_path)
        else:
            total += int(entry.st_size)
    return total


def _ensure_local_free_space(path: Path, required_bytes: int, context: str) -> None:
    if required_bytes <= 0:
        return

    path.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < required_bytes:
        raise InsufficientSpaceError(
            f"Insufficient free space on {context}: needs {required_bytes} bytes, has {free_bytes} bytes"
        )


def _ensure_remote_free_space(transport: paramiko.Transport, remote_path: str, required_bytes: int, context: str) -> None:
    if required_bytes <= 0:
        return

    free_bytes = _get_remote_free_bytes(transport, remote_path)
    if free_bytes is None:
        return

    if free_bytes < required_bytes:
        raise InsufficientSpaceError(
            f"Insufficient free space on {context}: needs {required_bytes} bytes, has {free_bytes} bytes"
        )


def _get_remote_free_bytes(transport: paramiko.Transport, remote_path: str) -> int | None:
    quoted_path = shlex.quote(remote_path or "/")
    command = f"df -Pk {quoted_path}"

    channel = transport.open_session()
    try:
        channel.exec_command(command)
        stdout = channel.makefile("r").read()
        _stderr = channel.makefile_stderr("r").read()
        exit_status = channel.recv_exit_status()
        if exit_status != 0:
            return None

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if len(lines) < 2:
            return None

        columns = lines[-1].split()
        if len(columns) < 4:
            return None

        # POSIX df -Pk reports 1K blocks in the 4th column (Available).
        available_kib = int(columns[3])
        return available_kib * 1024
    except Exception:
        return None
    finally:
        channel.close()


def _requires_local_capacity_check(source: Path, destination_dir: Path, transfer_mode: str) -> bool:
    if transfer_mode == "copy":
        return True
    try:
        source_dev = source.stat().st_dev
        destination_dev = destination_dir.stat().st_dev
    except OSError:
        return True
    return source_dev != destination_dev



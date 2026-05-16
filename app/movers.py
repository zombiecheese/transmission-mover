from __future__ import annotations

import logging
import os
import posixpath
import re
import shlex
import stat
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import paramiko

from app.models import AppConfig, Destination
from app.ssh_utils import exec_remote_command, parse_private_key, remote_has_cmd_with_retry, remote_is_directory
from app.settings import get_staging_path


class InsufficientSpaceError(RuntimeError):
    pass


ProgressCallback = Callable[[int, int], None]
MethodUpdateCallback = Callable[[str], None]

logger = logging.getLogger(__name__)


def _exec_remote_command_with_sudo_retry(
    transport: paramiko.Transport,
    command: str,
    attempt_sudo: bool,
) -> tuple[int, str, str, bool]:
    exit_code, stdout, stderr = exec_remote_command(transport, command)
    if exit_code == 0 or not attempt_sudo:
        return exit_code, stdout, stderr, False
    sudo_command = f"sudo -n sh -lc {shlex.quote(command)}"
    exit_code, stdout, stderr = exec_remote_command(transport, sudo_command)
    return exit_code, stdout, stderr, True


def transfer_to_destination(
    torrent_name: str,
    download_dir: str | None,
    destination: Destination,
    app_config: AppConfig | None,
    transfer_mode_override: str | None = None,
    transfer_method_preference_override: str | None = None,
    progress_callback: ProgressCallback | None = None,
    method_update_callback: MethodUpdateCallback | None = None,
) -> str:
    transfer_mode = (transfer_mode_override or (app_config.transfer_mode if app_config else "move")).lower()
    transfer_method_preference = (transfer_method_preference_override or "auto").lower()
    if transfer_mode not in {"move", "copy"}:
        raise ValueError(f"Unsupported transfer mode: {transfer_mode}")

    watch_source_kind = (app_config.watch_source_kind if app_config else "local").lower()
    # Validate source capabilities
    if watch_source_kind == "local":  # local location
        if app_config and app_config.watch_base_path:
            if not os.path.exists(app_config.watch_base_path):
                raise ValueError(f"Local source path does not exist: {app_config.watch_base_path}")
            if not os.access(app_config.watch_base_path, os.R_OK):
                raise ValueError(f"Local source path is not readable: {app_config.watch_base_path}")

    elif watch_source_kind == "ssh":
        if not app_config or not app_config.watch_base_path:
            raise ValueError("app_config and watch_base_path are required for remote SSH watch source")
        try:
            source_sftp, source_transport = _connect_source_sftp(app_config)
            if not remote_is_directory(
                source_transport,
                app_config.watch_base_path,
                attempt_sudo=bool(app_config.watch_attempt_sudo),
            ):
                raise ValueError(f"Remote source path is not a directory: {app_config.watch_base_path}")
            source_sftp.close()
            source_transport.close()
        except Exception as exc:
            raise ValueError(f"Failed to validate remote SSH source: {exc}")

    # Proceed with transfer logic
    if watch_source_kind == "local":
        source_path = _resolve_local_source_path(download_dir, torrent_name, app_config)
        if method_update_callback:
            method_update_callback("local filesystem" if destination.kind == "local" else "remote shell")
        method_used = _transfer_local_source(source_path, destination, torrent_name, transfer_mode, transfer_method_preference, progress_callback)
        if method_used == "local":
            return "Moved to destination" if transfer_mode == "move" else "Copied to destination"
        return (
            f"Moved to destination via {method_used}"
            if transfer_mode == "move"
            else f"Copied to destination via {method_used}"
        )

    if watch_source_kind == "ssh":
        method_used = _transfer_sftp_source(
            torrent_name,
            destination,
            app_config,
            transfer_mode,
            transfer_method_preference,
            progress_callback,
            method_update_callback,
        )
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
    transfer_method_preference: str = "auto",
    progress_callback: ProgressCallback | None = None,
) -> str:
    if destination.kind == "local":
        _transfer_local_to_local(source_path, destination.base_path, torrent_name, transfer_mode, progress_callback)
        return "local"

    if destination.kind in {"sftp", "remote"}:
        return _transfer_local_to_remote(source_path, destination, torrent_name, transfer_mode, transfer_method_preference, progress_callback)

    raise ValueError(f"Unsupported destination kind: {destination.kind}")


def _transfer_local_to_remote(
    source_path: str,
    destination: Destination,
    torrent_name: str,
    transfer_mode: str,
    transfer_method_preference: str = "auto",
    progress_callback: ProgressCallback | None = None,
) -> str:
    source = Path(source_path)
    required_bytes = _get_local_path_size(source)

    candidates = _get_remote_method_candidates_with_rule_preference(destination, transfer_method_preference)

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


def _get_remote_method_candidates_with_rule_preference(
    destination: Destination,
    rule_transfer_method_preference: str | None = None,
) -> list[str]:
    """Get ordered list of transfer methods, considering both destination and rule preferences."""
    rule_pref = (rule_transfer_method_preference or "auto").lower()
    dest_pref = (destination.transfer_method_preference or "auto").lower()
    detected = (destination.detected_preferred_method or "").lower()
    
    # Determine the primary preference
    if rule_pref != "auto":
        # Rule has an explicit preference
        primary = rule_pref
    elif dest_pref != "auto":
        # Destination has an explicit preference
        primary = dest_pref
    else:
        # Use detected preference, fallback to sftp
        primary = detected if detected in {"rsync", "scp", "sftp"} else "sftp"
    
    # Build candidates list with primary first, then fallbacks
    return [primary] + [m for m in ["rsync", "scp", "sftp"] if m != primary]


def _transfer_remote_to_remote_direct(
    source_sftp: paramiko.SFTPClient,
    source_transport: paramiko.Transport,
    remote_source: str,
    destination: Destination,
    torrent_name: str,
    transfer_mode: str,
    required_bytes: int,
    transfer_method_preference: str = "auto",
    progress_callback: ProgressCallback | None = None,
    source_attempt_sudo: bool = False,
) -> str:
    # Ensure destination target is reachable and has capacity from mover perspective.
    dest_sftp, dest_transport = _connect_sftp(destination)
    try:
        base_remote = destination.base_path.rstrip("/") or "/"
        try:
            _ensure_remote_dirs(dest_sftp, base_remote)
        except Exception:
            if not destination.attempt_sudo:
                raise
            mkdir_cmd = f"mkdir -p {shlex.quote(base_remote)}"
            exit_code, _stdout, stderr, _used_sudo = _exec_remote_command_with_sudo_retry(
                dest_transport,
                mkdir_cmd,
                attempt_sudo=True,
            )
            if exit_code != 0:
                err_text = (stderr or "").strip()
                raise RuntimeError(f"Failed to prepare destination path with sudo retry: {err_text or exit_code}")
        _ensure_remote_free_space(
            dest_transport,
            base_remote,
            required_bytes,
            "remote destination",
            attempt_sudo=bool(destination.attempt_sudo),
        )
    finally:
        dest_sftp.close()
        dest_transport.close()

    candidates = _get_remote_method_candidates_with_rule_preference(destination, transfer_method_preference)
    ssh_port = int(destination.detected_scp_port or destination.port or 22)
    target_remote = f"{destination.username}@{destination.host}:{destination.base_path.rstrip('/')}/{torrent_name}"
    attempt_reasons: list[str] = []

    for method in candidates:
        if method not in {"rsync", "scp"}:
            attempt_reasons.append(f"{method}: unsupported for direct remote-to-remote shell transfer")
            continue

        if not remote_has_cmd_with_retry(source_transport, method, attempt_sudo=source_attempt_sudo):
            attempt_reasons.append(f"{method}: command not available on source host")
            continue

        auth_prefix = ""
        if destination.password:
            if not remote_has_cmd_with_retry(source_transport, "sshpass", attempt_sudo=source_attempt_sudo):
                attempt_reasons.append(f"{method}: destination uses password auth but sshpass is missing on source host")
                continue
            auth_prefix = f"sshpass -p {shlex.quote(destination.password)} "

        if destination.private_key:
            # We cannot rely on this private key existing on source host filesystem.
            attempt_reasons.append(f"{method}: destination private-key auth cannot be used for source-host shell transfer")
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

        exit_code, _stdout, stderr, used_sudo = _exec_remote_command_with_sudo_retry(
            source_transport,
            cmd,
            attempt_sudo=source_attempt_sudo,
        )
        if exit_code != 0:
            err_text = (stderr or "").strip()
            sudo_note = " after sudo retry" if used_sudo else ""
            attempt_reasons.append(
                f"{method}: remote command failed with exit {exit_code}{sudo_note} ({err_text or 'no stderr'})"
            )
            continue

        if transfer_mode == "move":
            _remove_remote_path(source_sftp, remote_source)
        if progress_callback:
            progress_callback(required_bytes, required_bytes)
        return method

    reason_text = "; ".join(attempt_reasons) if attempt_reasons else "no supported direct method could be attempted"
    raise RuntimeError(f"No direct remote-to-remote method succeeded. {reason_text}")


def _transfer_remote_to_remote_reverse(
    source_sftp: paramiko.SFTPClient,
    app_config: AppConfig,
    remote_source: str,
    destination: Destination,
    torrent_name: str,
    transfer_mode: str,
    required_bytes: int,
    transfer_method_preference: str = "auto",
    progress_callback: ProgressCallback | None = None,
) -> str:
    if not app_config.watch_host or not app_config.watch_username:
        raise RuntimeError("Reverse transfer requires watch source host and username")

    dest_sftp, dest_transport = _connect_sftp(destination)
    try:
        base_remote = destination.base_path.rstrip("/") or "/"
        try:
            _ensure_remote_dirs(dest_sftp, base_remote)
        except Exception:
            if not destination.attempt_sudo:
                raise
            mkdir_cmd = f"mkdir -p {shlex.quote(base_remote)}"
            exit_code, _stdout, stderr, _used_sudo = _exec_remote_command_with_sudo_retry(
                dest_transport,
                mkdir_cmd,
                attempt_sudo=True,
            )
            if exit_code != 0:
                err_text = (stderr or "").strip()
                raise RuntimeError(f"Failed to prepare destination path with sudo retry: {err_text or exit_code}")
        _ensure_remote_free_space(
            dest_transport,
            base_remote,
            required_bytes,
            "remote destination",
            attempt_sudo=bool(destination.attempt_sudo),
        )

        candidates = _get_remote_method_candidates_with_rule_preference(destination, transfer_method_preference)
        source_port = int(app_config.watch_port or 22)
        source_endpoint = f"{app_config.watch_username}@{app_config.watch_host}:{remote_source}"
        target_remote = posixpath.join(base_remote, torrent_name)
        attempt_reasons: list[str] = []

        for method in candidates:
            if method not in {"rsync", "scp"}:
                attempt_reasons.append(f"{method}: unsupported for reverse remote-to-remote shell transfer")
                continue

            if not remote_has_cmd_with_retry(dest_transport, method, attempt_sudo=bool(destination.attempt_sudo)):
                attempt_reasons.append(f"{method}: command not available on destination host")
                continue

            auth_prefix = ""
            if app_config.watch_password:
                if not remote_has_cmd_with_retry(dest_transport, "sshpass", attempt_sudo=bool(destination.attempt_sudo)):
                    attempt_reasons.append(f"{method}: source uses password auth but sshpass is missing on destination host")
                    continue
                auth_prefix = f"sshpass -p {shlex.quote(app_config.watch_password)} "

            if app_config.watch_private_key:
                attempt_reasons.append(
                    f"{method}: source private-key auth cannot be used for destination-host shell transfer"
                )
                continue

            if method == "rsync":
                cmd = (
                    f"{auth_prefix}rsync -a --partial --inplace "
                    f"-e \"ssh -p {source_port} -o BatchMode=yes -o StrictHostKeyChecking=no\" "
                    f"{shlex.quote(source_endpoint)}/ {shlex.quote(target_remote)}/"
                )
            else:
                cmd = (
                    f"{auth_prefix}scp -r -P {source_port} -o StrictHostKeyChecking=no "
                    f"{shlex.quote(source_endpoint)} {shlex.quote(target_remote)}"
                )

            exit_code, _stdout, stderr, used_sudo = _exec_remote_command_with_sudo_retry(
                dest_transport,
                cmd,
                attempt_sudo=bool(destination.attempt_sudo),
            )
            if exit_code != 0:
                err_text = (stderr or "").strip()
                sudo_note = " after sudo retry" if used_sudo else ""
                attempt_reasons.append(
                    f"{method}: remote command failed with exit {exit_code}{sudo_note} ({err_text or 'no stderr'})"
                )
                continue

            if transfer_mode == "move":
                _remove_remote_path(source_sftp, remote_source)
            if progress_callback:
                progress_callback(required_bytes, required_bytes)
            return method

        reason_text = "; ".join(attempt_reasons) if attempt_reasons else "no supported reverse method could be attempted"
        raise RuntimeError(f"No reverse remote-to-remote method succeeded. {reason_text}")
    finally:
        dest_sftp.close()
        dest_transport.close()


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
        try:
            _ensure_remote_dirs(sftp, base_remote)
        except Exception:
            if not destination.attempt_sudo:
                raise
            mkdir_cmd = f"mkdir -p {shlex.quote(base_remote)}"
            exit_code, _stdout, stderr, _used_sudo = _exec_remote_command_with_sudo_retry(
                transport,
                mkdir_cmd,
                attempt_sudo=True,
            )
            if exit_code != 0:
                err_text = (stderr or "").strip()
                raise RuntimeError(f"Failed to prepare destination path with sudo retry: {err_text or exit_code}")
        _ensure_remote_free_space(
            transport,
            base_remote,
            required_bytes,
            "remote destination",
            attempt_sudo=bool(destination.attempt_sudo),
        )
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
            rsync_cmd = [tool, "-a", "--human-readable", "--partial", "--inplace", "--info=progress2"]
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

        if method == "rsync":
            completed = _run_rsync_with_progress(cmd, required_bytes, progress_callback)
        else:
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


def _run_rsync_with_progress(
    cmd: list[str],
    required_bytes: int,
    progress_callback: ProgressCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    progress_pattern = re.compile(r"(\d{1,3})%")
    last_reported = -1
    output_lines: list[str] = []

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert proc.stdout is not None
    for line in proc.stdout:
        output_lines.append(line)
        match = progress_pattern.search(line)
        if not match or not progress_callback:
            continue
        percent = max(0, min(int(match.group(1)), 100))
        if percent == last_reported:
            continue
        last_reported = percent
        transferred = int(required_bytes * (percent / 100.0))
        progress_callback(transferred, required_bytes)

    return_code = proc.wait()
    stderr_text = "".join(output_lines)
    return subprocess.CompletedProcess(cmd, return_code, "", stderr_text)


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
    if sftp is None:
        transport.close()
        raise RuntimeError("Failed to create SFTP client.")
    return sftp, transport


def _connect_source_sftp(app_config: AppConfig | None) -> tuple[paramiko.SFTPClient, paramiko.Transport]:
    if not app_config:
        raise ValueError("Remote watch source settings are missing")
    if not app_config.watch_host:
        raise ValueError("Remote SSH watch source requires host")
    if not app_config.watch_username:
        raise ValueError("Remote SSH watch source requires username")

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
    if sftp is None:
        transport.close()
        raise RuntimeError("Failed to create SFTP client.")
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
    transfer_method_preference: str = "auto",
    progress_callback: ProgressCallback | None = None,
    method_update_callback: MethodUpdateCallback | None = None,
) -> str:
    if not app_config or not app_config.watch_base_path:
        raise ValueError("watch_base_path is required for remote SSH watch source")

    remote_source = posixpath.join(app_config.watch_base_path.rstrip("/") or "/", torrent_name)
    source_sftp, source_transport = _connect_source_sftp(app_config)
    try:
        required_bytes = _get_remote_path_size(source_sftp, remote_source)

        if destination.kind == "local":
            if method_update_callback:
                method_update_callback("sftp")
            destination_dir = Path(destination.base_path)
            _ensure_local_free_space(destination_dir, required_bytes, "local destination")
            local_target = destination_dir / torrent_name
            _download_remote_source(
                source_sftp,
                remote_source,
                local_target,
                required_bytes,
                progress_callback,
            )
            if transfer_mode == "move":
                _remove_remote_path(source_sftp, remote_source)
            if progress_callback:
                progress_callback(required_bytes, required_bytes)
            return "sftp"

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
                    transfer_method_preference,
                    progress_callback,
                    source_attempt_sudo=bool(app_config.watch_attempt_sudo),
                )
            except Exception as forward_exc:
                logger.warning(
                    "Direct source->destination transfer failed for '%s'; trying reverse destination->source pull: %s",
                    torrent_name,
                    forward_exc,
                )
                try:
                    method_used = _transfer_remote_to_remote_reverse(
                        source_sftp,
                        app_config,
                        remote_source,
                        destination,
                        torrent_name,
                        transfer_mode,
                        required_bytes,
                        transfer_method_preference,
                        progress_callback,
                    )
                    logger.info(
                        "Reverse destination->source pull succeeded for '%s' via %s",
                        torrent_name,
                        method_used,
                    )
                    return method_used
                except Exception as reverse_exc:
                    # Fallback to staged transfer path below.
                    logger.warning(
                        "Falling back to staged transfer for '%s' after direct remote transfer failures. Forward: %s | Reverse: %s",
                        torrent_name,
                        forward_exc,
                        reverse_exc,
                    )
                logger.warning(
                    "Fallback path selected for '%s': staged-sftp",
                    torrent_name,
                )
                if method_update_callback:
                    method_update_callback("staged-sftp")


        total_work_bytes = max(required_bytes, 1) * 2
        staging_path = get_staging_path()
        _ensure_local_free_space(staging_path, required_bytes, "temporary staging")

        if destination.kind in {"sftp", "remote"}:
            base_remote = destination.base_path.rstrip("/") or "/"
            dest_sftp, dest_transport = _connect_sftp(destination)
            try:
                _ensure_remote_free_space(
                    dest_transport,
                    base_remote,
                    required_bytes,
                    "remote destination",
                    attempt_sudo=bool(destination.attempt_sudo),
                )
            finally:
                dest_sftp.close()
                dest_transport.close()

        # Use a subdirectory in the staging path for this transfer
        import uuid
        temp_dir = staging_path / f"staging_{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            local_cache = temp_dir / torrent_name
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
                "auto",  # Use auto method selection for staged transfers
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
            # Clean up the temp_dir and its contents
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
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
    if attrs.st_mode is None:
        return False
    return stat.S_ISDIR(attrs.st_mode)


def _get_remote_path_size(sftp: paramiko.SFTPClient, remote_path: str) -> int:
    if not _is_remote_dir(sftp, remote_path):
        st_size = sftp.stat(remote_path).st_size
        return int(st_size) if st_size is not None else 0

    total = 0
    for entry in sftp.listdir_attr(remote_path):
        child_path = posixpath.join(remote_path, entry.filename)
        if entry.st_mode is not None and stat.S_ISDIR(entry.st_mode):
            total += _get_remote_path_size(sftp, child_path)
        else:
            total += int(entry.st_size) if entry.st_size is not None else 0
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


def _ensure_remote_free_space(
    transport: paramiko.Transport,
    remote_path: str,
    required_bytes: int,
    context: str,
    attempt_sudo: bool = False,
) -> None:
    if required_bytes <= 0:
        return

    free_bytes = _get_remote_free_bytes(transport, remote_path, attempt_sudo=attempt_sudo)
    if free_bytes is None:
        return

    if free_bytes < required_bytes:
        raise InsufficientSpaceError(
            f"Insufficient free space on {context}: needs {required_bytes} bytes, has {free_bytes} bytes"
        )


def _get_remote_free_bytes(
    transport: paramiko.Transport,
    remote_path: str,
    attempt_sudo: bool = False,
) -> int | None:
    quoted_path = shlex.quote(remote_path or "/")
    command = f"df -Pk {quoted_path}"

    try:
        exit_status, stdout, _stderr, _used_sudo = _exec_remote_command_with_sudo_retry(
            transport,
            command,
            attempt_sudo=attempt_sudo,
        )
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


def _requires_local_capacity_check(source: Path, destination_dir: Path, transfer_mode: str) -> bool:
    if transfer_mode == "copy":
        return True
    try:
        source_dev = source.stat().st_dev
        destination_dev = destination_dir.stat().st_dev
    except OSError:
        return True
    return source_dev != destination_dev



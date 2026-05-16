from __future__ import annotations

import io
import socket
import shlex
import uuid
from typing import TypedDict

import paramiko


class ServicePorts(TypedDict):
    sftp: int | None
    scp: int | None
    rsync: int | None


class RemoteTransferCapabilities(TypedDict):
    available_methods: list[str]
    preferred_method: str | None
    service_ports: ServicePorts
    rsync_mode: str | None


class ValidationCheck(TypedDict):
    key: str
    label: str
    passed: bool
    detail: str
    hint: str | None


class RemoteValidationResult(TypedDict):
    ok: bool
    role: str
    base_path: str
    checks: list[ValidationCheck]
    failed_check: str | None
    message: str


def parse_private_key(private_key: str, passphrase: str | None):
    key_types = [
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.Ed25519Key
    ]
    # DSSKey is deprecated/optional in paramiko, add if present
    DSSKey = getattr(paramiko, "DSSKey", None)
    if DSSKey is not None:
        key_types.append(DSSKey)
    for key_cls in key_types:
        try:
            return key_cls.from_private_key(io.StringIO(private_key), password=passphrase)
        except Exception:
            continue
    raise RuntimeError("Unsupported or invalid private key format")


def exec_remote_command(transport: paramiko.Transport, command: str) -> tuple[int, str, str]:
    channel = transport.open_session()
    try:
        channel.exec_command(command)
        stdout = channel.makefile("rb").read()
        stderr = channel.makefile_stderr("rb").read()
        exit_code = channel.recv_exit_status()
        return exit_code, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    finally:
        channel.close()


def remote_has_cmd(transport: paramiko.Transport, command_name: str) -> bool:
    exit_code, _stdout, _stderr = exec_remote_command(
        transport,
        f"command -v {shlex.quote(command_name)} >/dev/null 2>&1",
    )
    return exit_code == 0


def exec_remote_sh(transport: paramiko.Transport, script: str, *args: str) -> tuple[int, str, str]:
    quoted_args = " ".join(shlex.quote(arg) for arg in args)
    command = f"sh -lc {shlex.quote(script)} sh"
    if quoted_args:
        command = f"{command} {quoted_args}"
    return exec_remote_command(transport, command)


def remote_is_directory(transport: paramiko.Transport, path: str) -> bool:
    exit_code, _stdout, _stderr = exec_remote_sh(transport, 'test -d "$1"', path)
    return exit_code == 0


def remote_can_list_directory(transport: paramiko.Transport, path: str) -> bool:
    exit_code, _stdout, _stderr = exec_remote_sh(
        transport,
        'ls -1A "$1" >/dev/null',
        path,
    )
    return exit_code == 0


def remote_can_write_directory(transport: paramiko.Transport, path: str) -> bool:
    probe_id = uuid.uuid4().hex
    script = 'probe="$1/.tm_probe_$2.tmp" && : > "$probe" && rm -f "$probe"'
    exit_code, _stdout, _stderr = exec_remote_sh(transport, script, path, probe_id)
    return exit_code == 0


def remote_can_create_subdirectory(transport: paramiko.Transport, path: str) -> bool:
    probe_id = uuid.uuid4().hex
    script = 'probe="$1/.tm_probe_dir_$2" && mkdir "$probe" && rmdir "$probe"'
    exit_code, _stdout, _stderr = exec_remote_sh(transport, script, path, probe_id)
    return exit_code == 0


def remote_can_traverse_parents(transport: paramiko.Transport, path: str) -> bool:
    script = r'''p="$1";
while [ "$p" != "/" ] && [ -n "$p" ]; do
    test -x "$p" || exit 1
    p=$(dirname "$p")
done
test -x /'''
    exit_code, _stdout, _stderr = exec_remote_sh(transport, script, path)
    return exit_code == 0


def remote_has_sftp(transport: paramiko.Transport) -> bool:
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
    except Exception:
        return False
    try:
        return True
    finally:
        if sftp is not None:
            sftp.close()


def connect_ssh_transport(
    host: str,
    port: int,
    username: str,
    password: str | None = None,
    private_key: str | None = None,
    key_passphrase: str | None = None,
) -> paramiko.Transport:
    sock = socket.create_connection((host, port), timeout=10)
    transport = paramiko.Transport(sock)
    pkey = parse_private_key(private_key, key_passphrase) if private_key else None
    transport.connect(username=username, password=password, pkey=pkey)
    return transport


def detect_remote_transfer_capabilities(
    transport: paramiko.Transport,
    host: str,
    ssh_port: int,
) -> RemoteTransferCapabilities:
    def _is_tcp_open(target_host: str, target_port: int, timeout: float = 2.0) -> bool:
        try:
            with socket.create_connection((target_host, target_port), timeout=timeout):
                return True
        except Exception:
            return False

    has_rsync = remote_has_cmd(transport, "rsync")
    has_scp = remote_has_cmd(transport, "scp")
    has_sftp = remote_has_sftp(transport)
    rsync_daemon_port = 873 if has_rsync and _is_tcp_open(host, 873) else None

    available_methods: list[str] = []
    if has_rsync:
        available_methods.append("rsync")
    if has_scp:
        available_methods.append("scp")
    if has_sftp:
        available_methods.append("sftp")

    preferred_method = available_methods[0] if available_methods else None
    service_ports = {
        "sftp": ssh_port if has_sftp else None,
        "scp": ssh_port if has_scp else None,
        "rsync": rsync_daemon_port if rsync_daemon_port is not None else (ssh_port if has_rsync else None),
    }
    rsync_mode = "daemon" if rsync_daemon_port is not None else ("ssh" if has_rsync else None)

    return {
        "available_methods": available_methods,
        "preferred_method": preferred_method,
        "service_ports": service_ports,
        "rsync_mode": rsync_mode,
    }


def validate_remote_base_path(
    transport: paramiko.Transport,
    path: str,
    role: str,
) -> RemoteValidationResult:
    checks: list[ValidationCheck] = []

    def add_check(key: str, label: str, passed: bool, detail: str, hint: str | None = None) -> None:
        checks.append(
            {
                "key": key,
                "label": label,
                "passed": passed,
                "detail": detail,
                "hint": hint,
            }
        )

    is_dir = remote_is_directory(transport, path)
    add_check(
        "path_is_directory",
        "Base path exists and is a directory",
        is_dir,
        f"Path checked: {path}",
        None if is_dir else "Set base path to an existing directory on the remote host.",
    )

    if not is_dir:
        return {
            "ok": False,
            "role": role,
            "base_path": path,
            "checks": checks,
            "failed_check": "path_is_directory",
            "message": f"Remote {role} validation failed: base path is not a directory",
        }

    can_traverse = remote_can_traverse_parents(transport, path)
    add_check(
        "parent_traverse",
        "Can traverse parent directories",
        can_traverse,
        f"Traverse check for {path}",
        None if can_traverse else "Grant execute (x) permission on parent directories to this SSH user.",
    )

    if role == "source":
        can_list = remote_can_list_directory(transport, path)
        add_check(
            "source_list",
            "Can list source directory",
            can_list,
            f"List/read check for {path}",
            None if can_list else "Grant read/list permission on the source directory.",
        )
    else:
        can_write = remote_can_write_directory(transport, path)
        add_check(
            "destination_write",
            "Can create and delete a probe file",
            can_write,
            f"Write probe in {path}",
            None if can_write else "Grant write permission to the destination base path.",
        )
        can_mkdir = remote_can_create_subdirectory(transport, path)
        add_check(
            "destination_mkdir",
            "Can create and remove a subdirectory",
            can_mkdir,
            f"Directory-create probe in {path}",
            None if can_mkdir else "Grant directory create/remove permission (write + execute) on the destination base path.",
        )

    failed = next((item for item in checks if not item["passed"]), None)
    ok = failed is None
    message = f"Remote {role} validation succeeded" if ok else f"Remote {role} validation failed at: {failed['label']}"
    return {
        "ok": ok,
        "role": role,
        "base_path": path,
        "checks": checks,
        "failed_check": failed["key"] if failed else None,
        "message": message,
    }

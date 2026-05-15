from __future__ import annotations

import io
import shlex
import uuid

import paramiko


def parse_private_key(private_key: str, passphrase: str | None):
    key_types = [
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.Ed25519Key,
        paramiko.DSSKey,
    ]
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
        stdout = channel.makefile("r").read()
        stderr = channel.makefile_stderr("r").read()
        exit_code = channel.recv_exit_status()
        return exit_code, stdout, stderr
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
    exit_code, _stdout, _stderr = exec_remote_sh(transport, 'ls -1A "$1" >/dev/null', path)
    return exit_code == 0


def remote_can_write_directory(transport: paramiko.Transport, path: str) -> bool:
    probe_id = uuid.uuid4().hex
    script = 'probe="$1/.tm_probe_$2.tmp" && : > "$probe" && rm -f "$probe"'
    exit_code, _stdout, _stderr = exec_remote_sh(transport, script, path, probe_id)
    return exit_code == 0


def remote_has_sftp(transport: paramiko.Transport) -> bool:
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
    except Exception:
        return False
    try:
        return True
    finally:
        sftp.close()

#!/usr/bin/env python3

import os
import select
import subprocess
import sys
from typing import Any, Optional

import config

BTRFS = "btrfs"

def polite_interrupt() -> bool:
    ready, _, _ = select.select([sys.stdin], [], [], 0)
    if ready:
        keyword = "stop"
        answer = input(f"'{keyword}' to interrupt: ")
        if answer == keyword:
            return True
        else:
            print(f"{answer!r} != {keyword!r}, continuing")
            return False


def choose_remote() -> str:
    existing_remotes = {
        remote for remote in config.btrfs_remotes if os.path.isdir(remote)
    }
    if len(existing_remotes) != 1:
        sys.stderr.write(f"Couldn't determine remote from {existing_remotes}\n")
        sys.exit(1)
    return existing_remotes.pop()


def start_sending_snap(parent_path: str, snap_path: str, receive_arg: str, extra_receive_params: Optional[dict[str, Any]] = None) -> tuple[subprocess.Popen, subprocess.Popen]:
    if extra_receive_params is None:
        extra_receive_params = {}

    cmd = [BTRFS, "send", "-p", parent_path, snap_path]
    print(cmd)

    btrfs_send = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
    )
    assert btrfs_send.stdout is not None

    cmd = [BTRFS, "receive", receive_arg]
    print(cmd)

    btrfs_receive = subprocess.Popen(
        cmd,
        stdin=btrfs_send.stdout,
        **extra_receive_params,
    )

    # This file descriptor is copied into the subprocess. Ensure that the
    # subprocess has the only copy of it, for SIGPIPE reasons.
    btrfs_send.stdout.close()

    return (btrfs_send, btrfs_receive)


def send_snap(parent_path: str, snap_path: str, receive_path: str) -> None:
    btrfs_send, btrfs_receive = start_sending_snap(
        parent_path=parent_path,
        snap_path=snap_path,
        receive_arg=receive_path,
    )

    btrfs_send.wait()
    btrfs_receive.wait()
    if btrfs_send.returncode != 0 or btrfs_receive.returncode != 0:
        sys.stderr.write(
            f"{BTRFS} send exited {btrfs_send.returncode},"
            f" receive {btrfs_receive.returncode}\n"
        )
        sys.exit(1)


def which_snaps_to_send(local_snaps: set[str], remote: str) -> tuple[str, list[str]]:
    remote_years = os.listdir(remote)
    remote_snaps = set(snap for year in remote_years for snap in os.listdir(f"{remote}/{year}"))
    assert remote_snaps

    last_remote = max(remote_snaps)
    assert last_remote in local_snaps, last_remote
    snaps_to_send = []
    for snap in sorted(local_snaps):
        if snap <= last_remote:
            assert snap in remote_snaps
        else:
            assert snap not in remote_snaps
            snaps_to_send.append(snap)
    return last_remote, snaps_to_send


def send_snaps(local_dirs: set[str], remote: str) -> None:
    local_snap_paths = {snap: f"{d}/{snap}" for d in local_dirs for snap in os.listdir(d)}
    parent, snaps_to_send = which_snaps_to_send(
        local_snaps=set(local_snap_paths.keys()),
        remote=remote,
    )

    for snap_to_send in snaps_to_send:
        parent_path = local_snap_paths[parent]
        assert os.path.isdir(parent_path)
        snap_path = local_snap_paths[snap_to_send]
        assert os.path.isdir(snap_path)
        year = snap_to_send[:4]
        receive_path = f"{remote}/{year}"
        assert os.path.isdir(receive_path)
        send_snap(parent_path=parent_path, snap_path=snap_path, receive_path=receive_path)
        if polite_interrupt():
            break
        parent = snap_to_send


if __name__ == "__main__":
    send_snaps(
        local_dirs=config.btrfs_srcs,
        remote=choose_remote(),
    )

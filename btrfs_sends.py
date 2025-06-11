#!/usr/bin/env python3

import datetime
import os
import select
import subprocess
import sys
import time
from typing import Any, Optional

import config

BTRFS = "btrfs"

def polite_interrupt() -> bool:
    ready, _, _ = select.select([sys.stdin], [], [], 0)

    if not ready:
        return False

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


def btrfs_subvolume_show(subvolume_path: str) -> dict[str, str]:
    ret: dict[str, str] = {}

    cmd = [BTRFS, "subvolume", "show", subvolume_path]
    print(cmd)
    show = subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        encoding="UTF-8",
    )

    lines = show.stdout.splitlines()
    # lines[0] is subvolume path
    for line in lines[1:]:
        try:
            key, v = line.split(":", maxsplit=1)
            ret[key.lstrip()] = v.lstrip()
        except ValueError:
            raise ValueError(line)

    return ret


def check_parent_was_finished(local_parent: str, remote_parent: str) -> None:
    local_show = btrfs_subvolume_show(subvolume_path=local_parent)
    remote_show = btrfs_subvolume_show(subvolume_path=remote_parent)

    try:
        local_uuid = local_show["UUID"]
        remote_received_uuid = remote_show["Received UUID"]
        remote_flags = remote_show["Flags"]
    except KeyError as k:
        raise ValueError(k, local_show, remote_show)
    if remote_received_uuid == "-":
        raise ValueError(f"{remote_parent!r} has no Received UUID")
    elif "readonly" not in remote_show["Flags"]:
        raise ValueError(f"{remote_parent!r} is not read-only")
    elif remote_received_uuid != local_uuid:
        raise ValueError(
            f"{remote_parent!r} Received UUID {remote_received_uuid!r}"
            f" does not match {local_parent!r} UUID {local_uuid!r}"
        )


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
    check_parent_was_finished(
        local_parent=local_snap_paths[parent],
        remote_parent=f"{remote}/{parent[:4]}/{parent}",
    )

    times = []
    num_remaining = len(snaps_to_send)
    for snap_to_send in snaps_to_send:
        print(f"{num_remaining} more to send")
        start_time = time.time()
        parent_path = local_snap_paths[parent]
        assert os.path.isdir(parent_path)
        snap_path = local_snap_paths[snap_to_send]
        assert os.path.isdir(snap_path)
        year = snap_to_send[:4]
        receive_path = f"{remote}/{year}"
        assert os.path.isdir(receive_path)
        send_snap(parent_path=parent_path, snap_path=snap_path, receive_path=receive_path)
        num_remaining -= 1
        end_time = time.time()
        elapsed = datetime.timedelta(seconds=end_time - start_time)
        print(f"Snapshot took {elapsed}")
        if not num_remaining:
            break
        times.append(elapsed)
        print("Estimated remaining time:")
        print(f"  {elapsed * num_remaining} (based on this send)")
        sample = times[-5:]
        if len(sample) > 1:
            avg = sum(sample, start=datetime.timedelta(0)) / len(sample)
            print(f"  {avg * num_remaining} (based on average of the last {len(sample)})")
        if polite_interrupt():
            break
        parent = snap_to_send


if __name__ == "__main__":
    send_snaps(
        local_dirs=config.btrfs_srcs,
        remote=choose_remote(),
    )

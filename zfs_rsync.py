#!/usr/bin/env python3
import subprocess
import sys
import os
from typing import Callable

import btrfs_receive
import config


def verbose_run(args: list[str], **kwargs):
    print(args)
    return subprocess.run(
        args=args,
        check=True,
        **kwargs,
    )


def stdout_lines(args: list[str]) -> list[str]:
    return verbose_run(
        args=args,
        stdout=subprocess.PIPE,
        encoding="UTF8",
    ).stdout.strip().split("\n")


def snaps_by_pool() -> dict[str, dict[str, set[str]]]:
    snaps_by_pool: dict[str, dict[str, set[str]]] = {}
    for line in stdout_lines(["zfs", "list", "-H", "-r", "-t", "snapshot"]):
        pool_fs, snap = line.split("\t")[0].split("@")
        pool, fs = pool_fs.split("/", maxsplit=1)
        snaps_by_pool.setdefault(pool, {}).setdefault(fs, set()).add(snap)

    unexpected_pools = snaps_by_pool.keys() - config.zfs_pools
    if unexpected_pools:
        sys.stderr.write(f"Pools {unexpected_pools} not expected, aborting\n")
        sys.exit(1)

    return snaps_by_pool


def srcs_by_ym_from(src_dirs: set[str]) -> dict[str, str]:
    """
    Look for all snapshots in src_dirs, which are assumed to be named
    YYYY-MM-*. Pick one for each unique YYYY-MM with minimal (in string
    ordering) full path *including the src dir*.
    """
    ret: dict[str, str] = {}
    for src_dir in src_dirs:
        for snap in os.listdir(src_dir):
            ym = snap[:len("YYYY-MM")]
            fullpath = f"{src_dir}/{snap}"
            try:
                if ret[ym] <= fullpath:
                    continue
            except KeyError:
                pass
            ret[ym] = fullpath
    return ret


def rsync_and_snap_all_yms(
        snap_dirs_by_fs: dict[str, set[str]],
        dry_run: bool = False,
        use_btrfs_to_detect_renames: bool = False,
) -> None:
    available_yms = {fs: srcs_by_ym_from(src_dirs=ds) for fs, ds in snap_dirs_by_fs.items()}

    for pool, existing_snaps_by_fs in snaps_by_pool().items():
        for fs, available in available_yms.items():
            (mountpoint_line,) = stdout_lines(["zfs", "get", "-H", "mountpoint", f"{pool}/{fs}"])
            mountpoint = mountpoint_line.split("\t")[2]

            todo = sorted(available.keys() - existing_snaps_by_fs[fs])
            print(f"{todo=}")
            if use_btrfs_to_detect_renames:
                parent_snap = available[max(existing_snaps_by_fs[fs])]
            for ym in todo:
                from_snap = available[ym]
                if use_btrfs_to_detect_renames:
                    if dry_run:
                        print(f"btrfs_receive.do_receive(parent_path={parent_snap!r}, snap_path={from_snap!r}, dest={mountpoint!r})")
                    else:
                        btrfs_receive.do_receive(
                            parent_path=parent_snap,
                            snap_path=from_snap,
                            dest=mountpoint,
                        )
                verbose_run_or_print = lambda args: print(args) if dry_run else verbose_run(args=args)
                verbose_run_or_print(
                    args=[
                        "rsync",

                        "--archive",
                        "--delete",
                        "--hard-links",

                        # see the README as to why we might be tempted to use
                        # these options, but shouldn't; --whole-file should be
                        # the default in this context but I'm specifying it
                        # anyway to make sure

                        # "--no-whole-file",
                        # "--inplace",
                        "--whole-file",

                        f"{from_snap}/",
                        mountpoint
                    ]
                )
                verbose_run_or_print(
                    args=[
                        "zfs",
                        "snapshot",
                        f"{pool}/{fs}@{ym}",
                    ],
                )
                verbose_run_or_print(args=["df", "-h", mountpoint])
                if use_btrfs_to_detect_renames:
                    parent_snap = from_snap
                if not dry_run:
                    with open(f"last-ym-rsynced-{pool}-{fs}", "w") as record_file:
                        record_file.write(f"{ym}\n")


if __name__ == "__main__":
    rsync_and_snap_all_yms(
        snap_dirs_by_fs={
            "root": config.btrfs_srcs,
        },
    )

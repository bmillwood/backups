#!/usr/bin/env python3

from dataclasses import dataclass
import os
import re
import subprocess
import sys

import btrfs_sends

def unbackslash(inp: str) -> str:
    bits: list[str] = []
    i = 0
    while i < len(inp):
        if inp[i] != '\\':
            c = inp[i]
            i += 1
        else:
            i += 1
            # https://github.com/kdave/btrfs-progs/blob/8859114eaee39c117ff95f5b60b4e81fc22f96e7/common/string-utils.c#L73
            special_cases = {
                'a': '\a',
                'b': '\b',
                'e': '\x1b',
                'f': '\f',
                'n': '\n',
                'r': '\r',
                't': '\t',
                'v': '\v',
            }
            try:
                c = special_cases[inp[i]]
                i += 1
            except KeyError:
                if inp[i].isdigit():
                    c = chr(int(inp[i:i + 3], base=8))
                    i += 3
                else:
                    c = inp[i]
                    i += 1
        bits.append(c)
    return "".join(bits)

@dataclass
class Line:
    command: str
    path: str
    args: dict[str, str]

    @classmethod
    def parse(cls, line: str):
        match_ = re.fullmatch(r"([^ ]+) +(([^\\ ]|\\.)+)(.*)\n", line)
        if match_ is None:
            raise ValueError(line)
        command = match_[1]
        path = unbackslash(match_[2])
        args = re.findall(r" ([a-z_]+)=((?:[^\\ ]|\\.)+)", match_[4])
        return cls(
            command=command,
            path=path,
            args={k: unbackslash(v) for k, v in args},
        )

def do_receive(parent_path: str, snap_path: str, dest: str) -> None:
    btrfs_send, btrfs_receive = btrfs_sends.start_sending_snap(
        parent_path=parent_path,
        snap_path=snap_path,
        receive_arg="--dump",
        extra_receive_params={
            "stdout": subprocess.PIPE,
            "encoding": "UTF-8",
        },
    )
    assert btrfs_receive.stdout
    snap_name = os.path.basename(snap_path)
    prefix = f"./{snap_name}/"

    def r(path):
        assert not os.path.isabs(path)
        assert ".." not in path.split("/")
        return os.path.join(dest, path)

    def p(path):
        assert path.startswith(prefix)
        return r(path.removeprefix(prefix))

    for line in btrfs_receive.stdout:
        parsed = Line.parse(line=line)

        match parsed.command:
            case "rename":
                os.rename(p(parsed.path), p(parsed.args["dest"]))
            case "unlink":
                os.unlink(p(parsed.path))
            case "rmdir":
                os.rmdir(p(parsed.path))
            case "link":
                os.link(r(parsed.args["dest"]), p(parsed.path))
            case "mkfile":
                open(p(parsed.path), "x").close()
            case "symlink":
                # leave target alone
                os.symlink(src=parsed.args["dest"], dst=p(parsed.path))
            case "mkdir":
                os.mkdir(p(parsed.path))
            case "mksock":
                # not exactly correct but should be close enough
                open(p(parsed.path), "x").close()
            case "snapshot" | "utimes" | "write" | "truncate" | "chown" | "chmod" | "set_xattr":
                continue
            # Took these from:
            # https://github.com/kdave/btrfs-progs/blob/8859114eaee39c117ff95f5b60b4e81fc22f96e7/cmds/receive-dump.c#L338
            # but haven't come across them in my snapshots yet
            case "mknod" | "mkfifo" | "clone" | "remove_xattr" | "update_extent" | "fallocate" | "fileattr" | "enable_verity":
                assert False, parsed

    btrfs_send.wait()
    btrfs_receive.wait()
    if btrfs_send.returncode != 0 or btrfs_receive.returncode != 0:
        sys.stderr.write(
            f"btrfs send exited {btrfs_send.returncode}, "
            f"receive {btrfs_receive.returncode}\n"
        )
        sys.exit(1)

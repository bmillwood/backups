#!/usr/bin/env python3

from dataclasses import dataclass
import os
import re
import subprocess
import sys

import btrfs_sends

def unbackslash(inp: str) -> bytes:
    bits: list[int] = []
    i = 0
    while i < len(inp):
        if inp[i] != '\\':
            c = ord(inp[i])
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
                c = ord(special_cases[inp[i]])
                i += 1
            except KeyError:
                if inp[i].isdigit():
                    c = int(inp[i:i + 3], base=8)
                    i += 3
                else:
                    c = ord(inp[i])
                    i += 1
        bits.append(c)
    return bytes(bits)

@dataclass
class Line:
    command: str
    path: bytes
    args: dict[str, bytes]

    line_regex = re.compile(r"([^ ]+) +(([^\\ ]|\\.)+)(.*)\n")
    args_regex = re.compile(r" ([a-z_]+)=((?:[^\\ ]|\\.)+)")

    @classmethod
    def parse(cls, line: str):
        match_ = cls.line_regex.fullmatch(line)
        if match_ is None:
            raise ValueError(line)
        args = cls.args_regex.findall(match_[4])
        return cls(
            command=match_[1],
            path=unbackslash(match_[2]),
            args={k: unbackslash(v) for k, v in args},
        )

def do_receive(parent_path: str, snap_path: str, dest: str) -> None:
    btrfs_send, btrfs_receive = btrfs_sends.start_sending_snap(
        parent_path=parent_path,
        snap_path=snap_path,
        receive_arg="--dump",
        extra_receive_params={
            "stdout": subprocess.PIPE,
        },
    )

    assert btrfs_receive.stdout
    snap_name = os.path.basename(snap_path)
    prefix = f"./{snap_name}/".encode("UTF-8")
    bdest = dest.encode("UTF-8")

    def r(path):
        assert not os.path.isabs(path)
        assert b".." not in path.split(b"/")
        fullpath = os.path.join(bdest, path)
        return fullpath

    def p(path):
        assert path.startswith(prefix)
        return r(path.removeprefix(prefix))

    i = 0
    for line in btrfs_receive.stdout:
        parsed = Line.parse(line=line.decode("UTF-8"))
        if i % 1000 == 0:
            sys.stdout.write(".")
            sys.stdout.flush()
        i += 1

        match parsed.command:
            case "rename":
                src = p(parsed.path)
                os.rename(src, p(parsed.args["dest"]))
            case "unlink":
                path = p(parsed.path)
                os.unlink(path)
            case "rmdir":
                path = p(parsed.path)
                os.rmdir(path)
            case "link":
                path = p(parsed.path)
                os.link(r(parsed.args["dest"]), path)
            case "mkfile" | "mksock" | "mkfifo":
                # for the special file cases this isn't correct but should at
                # least ensure future renames etc. work
                path = p(parsed.path)
                try:
                    open(path, "x").close()
                except FileExistsError:
                    print(parsed)
                    raise
            case "symlink":
                # target (= src) doesn't need to be re-relativized or anything
                path = p(parsed.path)
                os.symlink(
                    src=parsed.args["dest"],
                    dst=path,
                )
            case "mkdir":
                path = p(parsed.path)
                os.mkdir(path)
            case "snapshot" | "utimes" | "write" | "clone" | "truncate" | "chown" | "chmod" | "set_xattr":
                # I think clone is just like write, and doesn't create the
                # target file, but I'm not sure.
                continue
            # Took these from:
            # https://github.com/kdave/btrfs-progs/blob/8859114eaee39c117ff95f5b60b4e81fc22f96e7/cmds/receive-dump.c#L338
            # but haven't come across them in my snapshots yet
            case "mknod" | "remove_xattr" | "update_extent" | "fallocate" | "fileattr" | "enable_verity":
                assert False, parsed

    btrfs_send.wait()
    btrfs_receive.wait()
    if btrfs_send.returncode != 0 or btrfs_receive.returncode != 0:
        sys.stderr.write(
            f"btrfs send exited {btrfs_send.returncode}, "
            f"receive {btrfs_receive.returncode}\n"
        )
        # btrfs send gets SIGPIPE'd sometimes. I don't understand why. Just
        # keep going.
        if btrfs_send.returncode != -13 or btrfs_receive.returncode != 0:
            sys.exit(1)

"""INERT candidate. Read observations only; never qualifies ownership or admission.

No arguments, subprocesses, network, environment dump, cgroup writes or PID scans.
The caller must bound execution to 60 seconds and retained stdout to 64 KiB.
"""

import json
import os
import stat


LIMIT = 32768
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def read_text(path, *, dir_fd=None):
    fd = os.open(path, READ, dir_fd=dir_fd)
    try:
        data = b""
        while True:
            chunk = os.read(fd, LIMIT + 1 - len(data))
            if not chunk:
                break
            data += chunk
            if len(data) > LIMIT:
                raise ValueError("observation exceeds read bound")
        return data.decode("ascii")
    finally:
        os.close(fd)


def identity(info):
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def observe():
    result = {
        "kind": "read_only_observation",
        "ownership_proven": False,
        "write_operations_tested": False,
        "admission_granted": False,
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "limitations": [
            "Membership and UID do not prove job exclusivity.",
            "access() does not test successful create/join/kill.",
            "No runner-issued ownership/bootstrap interface supplied.",
        ],
    }
    group_lines = read_text("/proc/self/cgroup").splitlines()
    groups = [line[3:] for line in group_lines if line.startswith("0::")]
    if len(groups) != 1 or not groups[0].startswith("/"):
        raise ValueError("expected one unified cgroup membership")
    group = groups[0]
    parts = group.split("/")[1:] if group != "/" else []
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("unsupported membership path")
    mounts = []
    for line in read_text("/proc/self/mountinfo").splitlines():
        before, separator, after = line.partition(" - ")
        fields, tail = before.split(), after.split()
        if separator and len(fields) >= 6 and tail and tail[0] == "cgroup2":
            mounts.append((fields, tail))
    selected = [
        (fields, tail)
        for fields, tail in mounts
        if fields[3] == "/" and fields[4] == "/sys/fs/cgroup"
    ]
    if len(selected) != 1:
        raise ValueError("no unique root-mapped /sys/fs/cgroup mount; no guessing")
    fields, tail = selected[0]
    result.update(
        {
            "membership": group,
            "mount_options": fields[5],
            "super_options": tail[2] if len(tail) > 2 else "",
        }
    )
    fd = os.open("/", DIRECTORY)
    try:
        for part in ["sys", "fs", "cgroup", *parts]:
            child = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        result["membership_directory"] = identity(os.fstat(fd))
        result["directory_access_hint"] = {
            "write_and_search": os.access(".", os.W_OK | os.X_OK, dir_fd=fd, effective_ids=True)
        }
        interfaces = {}
        readable = {
            "cgroup.type",
            "cgroup.controllers",
            "cgroup.subtree_control",
            "cgroup.events",
            "cgroup.max.depth",
            "cgroup.max.descendants",
        }
        for name in sorted(readable | {"cgroup.procs", "cgroup.kill"}):
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("interface is not regular")
                entry = {
                    "identity": identity(info),
                    "write_access_hint": os.access(name, os.W_OK, dir_fd=fd, effective_ids=True),
                }
                if name in readable:
                    entry["text"] = read_text(name, dir_fd=fd)
                interfaces[name] = entry
            except (OSError, ValueError) as error:
                interfaces[name] = {
                    "unavailable": type(error).__name__,
                    "errno": getattr(error, "errno", None),
                }
        result["interfaces"] = interfaces
        # The proc file identifies only this diagnostic, never a borrowed process tree.
        if read_text("/proc/self/cgroup").splitlines() != group_lines:
            raise ValueError("self membership changed during observation")
        result["observation_complete"] = True
        return result
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        report = observe()
    except (OSError, ValueError) as error:
        report = {
            "kind": "read_only_observation",
            "observation_complete": False,
            "ownership_proven": False,
            "admission_granted": False,
            "error": type(error).__name__,
            "errno": getattr(error, "errno", None),
        }
    output = json.dumps(report, sort_keys=True)
    if len(output.encode("ascii")) + 1 > 65536:
        output = json.dumps(
            {
                "kind": "read_only_observation",
                "observation_complete": False,
                "ownership_proven": False,
                "admission_granted": False,
                "error": "serialized observation exceeds 64 KiB",
            }
        )
    print(output)
    # Always incomplete for capability qualification. Never use as a green gate.
    raise SystemExit(2)

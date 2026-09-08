#!/usr/bin/env python3
"""
向 4.9 内核源码注入 KernelSU-Next (legacy) 手动 hook。

用「函数定位 + 锚点替换」而非行号 patch，避免行号偏移导致 git apply 失败。

用法: python3 scripts/inject_hooks.py <内核源码根目录> [--revert]
"""
import re
import sys
import os

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
REVERT = "--revert" in sys.argv

# ---------------------------------------------------------------- 工具
def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8", errors="replace") as f:
        return f.read()

def write(p, s):
    with open(os.path.join(ROOT, p), "w", encoding="utf-8") as f:
        f.write(s)

def indent_of(line):
    return re.match(r"[\t ]*", line).group(0)

def find_func(src, sig_regex):
    """返回函数的 (起始下标, 左花括号下标)"""
    m = re.search(sig_regex, src)
    if not m:
        return None
    brace = src.index("{", m.end() - 1)
    return m.start(), brace

def already(src, marker):
    return marker in src

def inject(path, sig_regex, anchor_regex, decl, call, marker):
    """在函数体内、anchor 行之后插入 call；在函数体开头插入 decl"""
    src = read(path)
    if already(src, marker):
        print(f"  [skip] {path} 已注入 {marker}")
        return
    loc = find_func(src, sig_regex)
    if not loc:
        print(f"  [FAIL] {path} 找不到函数 {sig_regex}")
        sys.exit(1)
    start, brace = loc

    # 函数体起点（左花括号后第一个换行之后）
    body = brace + 1

    # 在 anchor 之后插入 call
    m = re.search(anchor_regex, src[body:body + 3000])
    if not m:
        print(f"  [FAIL] {path} 找不到锚点 {anchor_regex}")
        sys.exit(1)
    ins_pos = body + m.end()
    ind = indent_of(src[body + m.start():ins_pos].splitlines()[0])
    block = f"\n#ifdef CONFIG_KSU\n" + \
            "\n".join(ind + l for l in call.strip("\n").splitlines()) + \
            f"\n#endif\n"
    src = src[:ins_pos] + block + src[ins_pos:]

    # 重新定位（长度已变），在函数体最前面插入声明
    start2, brace2 = find_func(src, sig_regex)
    b2 = brace2 + 1
    line_end = src.index("\n", b2) + 1
    ind2 = "\t"
    decl_block = f"#ifdef CONFIG_KSU\n" + \
                 "\n".join(ind2 + l for l in decl.strip("\n").splitlines()) + \
                 f"\n#endif\n"
    src = src[:line_end] + decl_block + src[line_end:]

    write(path, src)
    print(f"  [ OK ] {path} <- {marker}")

# ---------------------------------------------------------------- 6 处 hook
JOBS = []

# 1. fs/exec.c -> do_execveat_common
JOBS.append(dict(
    path="fs/exec.c",
    sig=r"static int do_execveat_common\(int fd, struct filename \*filename,",
    anchor=r"\n\tif \(IS_ERR\(filename\)\)\n\t\treturn PTR_ERR\(filename\);",
    decl="""extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr,
			       void *argv, void *envp, int *flags);""",
    call="""	ksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);""",
    marker="ksu_handle_execveat",
))

# 2. fs/open.c -> SYSCALL_DEFINE3(faccessat)   4.9 无 do_faccessat
JOBS.append(dict(
    path="fs/open.c",
    sig=r"SYSCALL_DEFINE3\(faccessat, int, dfd, const char __user \*, filename, int, mode\)",
    anchor=r"\n\tunsigned int lookup_flags = LOOKUP_FOLLOW;",
    decl="""extern int ksu_handle_faccessat(int *dfd, const char __user **filename_user,
				int *mode, int *flags);""",
    call="""	ksu_handle_faccessat(&dfd, &filename, &mode, NULL);""",
    marker="ksu_handle_faccessat",
))

# 3. fs/stat.c -> vfs_fstatat   4.9 无 vfs_statx
JOBS.append(dict(
    path="fs/stat.c",
    sig=r"int vfs_fstatat\(int dfd, const char __user \*filename, struct kstat \*stat,",
    anchor=r"\n\tunsigned int lookup_flags = 0;",
    decl="""extern int ksu_handle_stat(int *dfd, const char __user **filename_user,
			   int *flags);""",
    call="""	ksu_handle_stat(&dfd, &filename, &flag);""",
    marker="ksu_handle_stat",
))

# 4. fs/read_write.c -> SYSCALL_DEFINE3(read)
#    Next legacy 实测: void ksu_handle_sys_read(unsigned int fd)  —— 单参数
JOBS.append(dict(
    path="fs/read_write.c",
    sig=r"SYSCALL_DEFINE3\(read, unsigned int, fd, char __user \*, buf, size_t, count\)",
    anchor=r"\n\tssize_t ret = -EBADF;",
    decl="""extern bool ksu_vfs_read_hook __read_mostly;
extern void ksu_handle_sys_read(unsigned int fd);""",
    call="""	if (unlikely(ksu_vfs_read_hook))
		ksu_handle_sys_read(fd);""",
    marker="ksu_handle_sys_read",
))

# 5. drivers/input/input.c -> input_handle_event  (音量键安全模式)
JOBS.append(dict(
    path="drivers/input/input.c",
    sig=r"static void input_handle_event\(struct input_dev \*dev,",
    anchor=r"\n\tint disposition = input_get_disposition\(dev, type, code, &value\);",
    decl="""extern int ksu_handle_input_handle_event(unsigned int *type,
					 unsigned int *code, int *value);""",
    call="""	ksu_handle_input_handle_event(&type, &code, &value);""",
    marker="ksu_handle_input_handle_event",
))

# 6. kernel/reboot.c -> SYSCALL_DEFINE4(reboot)
JOBS.append(dict(
    path="kernel/reboot.c",
    sig=r"SYSCALL_DEFINE4\(reboot, int, magic1, int, magic2, unsigned int, cmd,",
    anchor=r"\n\t\t\tmagic2 != LINUX_REBOOT_MAGIC2C\)\)\n\t\treturn -EINVAL;\n",
    decl="""extern int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd,
				 void __user **arg);""",
    call="""	ksu_handle_sys_reboot(magic1, magic2, cmd, &arg);""",
    marker="ksu_handle_sys_reboot",
))

# ---------------------------------------------------------------- main
def main():
    if REVERT:
        print("revert 模式：请直接用 git checkout 恢复源码，不建议反向替换")
        sys.exit(0)
    print(f"[*] 注入 KernelSU-Next hooks -> {os.path.abspath(ROOT)}")
    for j in JOBS:
        inject(j["path"], j["sig"], j["anchor"], j["decl"], j["call"], j["marker"])
    print("[*] 完成")

if __name__ == "__main__":
    main()


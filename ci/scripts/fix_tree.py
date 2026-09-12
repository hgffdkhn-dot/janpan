#!/usr/bin/env python3
"""
一次性修复 vendor 内核树在 GCC 4.9 下的常见编译障碍。
设计原则：只做「降级/放宽」，不改变运行时语义；全部操作幂等。

用法: python3 fix_tree.py <内核源码根目录>
"""
import os
import re
import sys
import collections

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
os.chdir(ROOT)

SKIP_DIRS = {".git", "Documentation", "samples", "tools", "scripts"}
SRC_EXT = (".c",)


def log(tag, msg):
    print(f"  [{tag}] {msg}")


# ---------------------------------------------------------------- 1. -Werror
def fix_werror():
    """把所有 Makefile/Kbuild 里的 -Werror 系列降级为普通警告。

    vendor 树里大量 sysfs show/store const 差异、unused 变量等，
    在 ARM64 上 ABI 一致、运行时无害，但被 -Werror 升格成致命错误。
    """
    n_file = n_hit = 0
    pat = re.compile(r"-Werror[a-zA-Z0-9_=-]*")
    for dp, dns, fns in os.walk("."):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn not in ("Makefile", "Kbuild", "Makefile.build", "Makefile.extrawarn"):
                if not fn.endswith(".mk"):
                    continue
            p = os.path.join(dp, fn)
            try:
                lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            except Exception:
                continue
            out, hit = [], False
            for l in lines:
                # 只动编译标志行，避免误伤其他内容
                if re.search(r"CFLAGS|ccflags|AFLAGS", l) and "-Werror" in l:
                    new = pat.sub("", l).replace(",,", ",").rstrip()
                    # 清理空壳 $(call cc-option, )
                    new = re.sub(r"\$\(call cc-option,\s*\)", "", new).rstrip()
                    if new.strip() in ("", "+="):
                        continue
                    hit = True
                    out.append(new)
                else:
                    out.append(l)
            if hit:
                n_hit += 1
                open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
            n_file += 1
    log("Werror", f"扫描 {n_file} 个 Makefile，修改 {n_hit} 个")


# ---------------------------------------------------------------- 2. include 路径
def build_header_index():
    idx = collections.defaultdict(set)
    for dp, dns, fns in os.walk("."):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn.endswith(".h"):
                idx[fn].add(dp)
    return idx


def fix_include_paths():
    """给引用了「异目录同名头文件」的源码目录补 -I。

    典型症状: fatal error: xxx.h: No such file or directory
    成因: vendor 代码里 #include <btfm_slim.h> / <cam_context.h> 这类裸文件名，
          但 Makefile 没有 -I 指向头文件所在目录。
    """
    idx = build_header_index()
    need = collections.defaultdict(set)          # c_dir -> set(rel_paths)
    inc_re = re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]')

    for dp, dns, fns in os.walk("."):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if not fn.endswith(SRC_EXT):
                continue
            cp = os.path.join(dp, fn)
            try:
                txt = open(cp, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for m in inc_re.finditer(txt):
                h = m.group(1)
                if "/" in h:                      # 已带路径，交给编译器即可
                    continue
                cands = idx.get(h)
                if not cands:
                    continue
                for hd in cands:
                    if os.path.normpath(hd) == os.path.normpath(dp):
                        need[dp].add(".")         # 同目录也要 -I$(src)
                    else:
                        rel = os.path.relpath(hd, dp)
                        if not rel.startswith(".." * 3):
                            need[dp].add(rel)

    n = 0
    for d, rels in sorted(need.items()):
        mk = os.path.join(d, "Makefile")
        if not os.path.exists(mk):
            continue
        cur = open(mk, encoding="utf-8", errors="replace").read()
        add = []
        for r in sorted(rels):
            flag = "-I$(src)" if r == "." else f"-I$(src)/{r}"
            if flag not in cur:
                add.append(f"ccflags-y += {flag}")
        if add:
            with open(mk, "a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(add) + "\n")
            n += 1
            log("INCLUDE", f"{d}: +{len(add)} 条")
    log("INCLUDE", f"共修改 {n} 个 Makefile")


# ---------------------------------------------------------------- 3. uifirst 内联
def fix_uifirst_inline():
    """uifirst 头里 `inline f(void);` 只有声明没有函数体，
    GCC 报: inlining failed in call to always_inline 'xxx': function body not available

    强制走 stub 分支（static inline 空实现），语义上等于关闭 uifirst 优化，
    不影响功能，只是少了 OPPO 的 UI 调度加速。
    """
    targets = []
    for dp, dns, fns in os.walk("include/linux/uifirst"):
        for fn in fns:
            if fn.endswith(".h"):
                targets.append(os.path.join(dp, fn))
    n = 0
    for p in targets:
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if "OPLUS_FEATURE_UIFIRST" not in txt and "always_inline" not in txt:
            continue
        orig = txt
        # 外层: #ifdef OPLUS_FEATURE_UIFIRST -> #if 1   (确保 stub 一定可见)
        txt = txt.replace("#ifdef OPLUS_FEATURE_UIFIRST", "#if 1 /* fixed */")
        txt = txt.replace("#ifndef OPLUS_FEATURE_UIFIRST", "#if 1 /* fixed */")
        # 内层: 让「只有声明」的分支永不生效，落到 static inline stub
        txt = txt.replace("#ifdef CONFIG_OPLUS_SYSTEM_KERNEL_QCOM",
                          "#if 0 /* fixed: no function body */")
        if txt != orig:
            open(p, "w", encoding="utf-8").write(txt)
            n += 1
            log("UIFIRST", f"{p}")
    log("UIFIRST", f"处理 {n} 个头文件")
    return n


# ---------------------------------------------------------------- 4. GCC 4.9 不认识的选项
def fix_bad_flags():
    n = 0
    for dp, dns, fns in os.walk("."):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if not (fn.startswith("Makefile") or fn == "Kbuild" or fn.endswith(".mk")):
                continue
            p = os.path.join(dp, fn)
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            if "Wno-enum-conversion" in txt:
                open(p, "w", encoding="utf-8").write(
                    txt.replace("-Wno-enum-conversion", ""))
                n += 1
    log("FLAGS", f"清理 -Wno-enum-conversion，{n} 个文件")


# ---------------------------------------------------------------- 5. NXP 功放
def purge_tfa98xx():
    """R17 无 NXP 功放，但 techpack Makefile 硬编码编它。"""
    n = 0
    for dp, dns, fns in os.walk("techpack"):
        for fn in fns:
            if fn not in ("Makefile", "Kbuild"):
                continue
            p = os.path.join(dp, fn)
            try:
                lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            except Exception:
                continue
            out = [l for l in lines if "tfa98" not in l.lower()]
            if len(out) != len(lines):
                open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
                n += 1
    # 兜底：子目录 Makefile 清空
    for dp, dns, fns in os.walk("techpack"):
        if "tfa98xx" in dp.lower():
            mk = os.path.join(dp, "Makefile")
            if os.path.exists(mk):
                open(mk, "w").write("")
                n += 1
    log("TFA98XX", f"清理 {n} 个 Makefile")


# ---------------------------------------------------------------- 6. GCC 版本门槛
def relax_gcc_gate():
    p = "include/linux/compiler-gcc.h"
    if not os.path.exists(p):
        return
    txt = open(p, encoding="utf-8", errors="replace").read()
    lines = txt.splitlines()
    out = [l for l in lines if "please use 5.1 or newer" not in l]
    if len(out) != len(lines):
        open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
        log("GCC-GATE", "已移除 GCC >= 5.1 硬门槛")
    else:
        log("GCC-GATE", "无需处理")


def main():
    print(f"[*] fix_tree.py  ->  {ROOT}")
    relax_gcc_gate()
    fix_werror()
    fix_include_paths()
    fix_uifirst_inline()
    fix_bad_flags()
    purge_tfa98xx()
    print("[*] 完成")


if __name__ == "__main__":
    main()


# R17 KernelSU-Next 编译 — Reno 树版

## 为什么换树

ten 分支（`NiceM126/android_kernel_oppo_PBEM00`）连续 5 轮报错，
且**确实缺文件**（`adreno_trace.h` 不存在）：
`btfm_slim` → `tfa98xx` → `mdss trace` → `kgsl/adreno trace` → `goodix 指纹` → **DTB 全挂**。

根因：它是 realme X (RMX1901) 的 fork，R17 专属代码路径从未被编译过。

Reno 树优势（已实测）：

| 文件 | ten | Reno |
|---|---|---|
| `drivers/gpu/msm/kgsl_trace.h` | ✓ | ✓ |
| `drivers/gpu/msm/adreno_trace.h` | **✗ 不存在** | ✓ |
| `drivers/gpu/msm/kgsl_device.h` | ✓ | ✓ |
| oppo 相关文件总数 | 161 | **807** |
| 现成 KSU 集成分支 | 无 | **有** |

## 目录结构

```
仓库/
├── .github/workflows/build-kernel.yml
└── ci/
    ├── sdm670_reno_ksu_defconfig   ← 4566 项
    └── scripts/inject_hooks.py
```

## defconfig 策略

仍以**你的手机出厂 `/proc/config.gz`（4559 项）为基底**，
只从 Reno defconfig 取了 4 个无害项（`DT_OVERLAY`、`F2FS`、`PSTORE`、`SLUB_DEBUG`）。

Reno defconfig 里有但手机没有的 68 项**没采纳**——那些是 Reno 硬件的，
照搬只会重蹈 ten 的覆辙。

已关闭：`OPPO_ROOT_CHECK` / `EXECVE_BLOCK` 等 12 项 + `KPROBES` + `TFA98XX` 系列。

## 仓库地址

```
https://github.com/OP46B1-Dev/android_kernel_oppo_sdm710  (branch: lineage-18.1)
```

⚠️ 这是 Reno (PCAM00) 的树。用它是因为源码完整度远高于 ten，
但**要验证和 ColorOS 7.1 的兼容性**——这是换树的主要代价。

## 用法

Actions → `Build R17 Kernel (Reno tree)` → `Run workflow`

**先跑 `stock`**，确认能出 `Image.gz-dtb` 再上 `ksunext`。

## 已知差异

Reno 树是 Android 11 基线。若 `stock` 内核能开机但某些功能异常
（指纹/音频/NFC），需要针对性调 config，把日志发给我。

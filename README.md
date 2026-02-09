> [!CAUTION]
> 本项目使用 TRAE IDE 生成与迭代

> [!WARNING]
> 请注意：由 AI 生成的代码可能有：不可预知的风险和错误！  
> 如您需要直接使用本项目，请**审查并测试后再使用**；  
> 如您要将本项目引用到其他项目，请**重构后再使用**。

> [!IMPORTANT]
> 本项目用于自动更新 M9A CLI，无需手动操作  
> 且临时解决 [#689](https://github.com/MAA1999/M9A/issues/689) 问题

# M9A Update Assistant

M9A CLI 更新小助手

## 如何使用

1. 从 [Release](https://github.com/NEANC/M9A-Update-Assistant/releases/latest) 下载
2. 首次运行会生成 `config.ini` 配置文件
3. 编辑 `config.ini` 文件，配置 M9A 文件夹路径、临时文件夹路径和代理服务器信息后
4. 再次运行，则开始更新

## 项目结构

```bash
M9A-Update-Assistant/
├── m9a_update_assistant.py  # 主程序文件
├── config.ini               # 配置文件（自动生成）
├── m9a_update_assistant.spec  # PyInstaller 打包配置
└── README.md                # 项目说明
```

## 更新流程

1. **配置加载**：加载配置文件，如不存在则自动生成
2. **从 GitHub API 获取最新版本**：从 GitHub API 获取最新版本号
3. **文件检查**：检查临时文件夹中是否存在最新版本的压缩包，若不存在则下载
4. **从 GitHub 下载最新版本**：从 GitHub 下载最新版本的 Lite 和 Full ZIP 文件
5. **检查 deps**：检查 Lite 版本是否包含 deps 文件夹，若无则从 Full 版本提取
6. **配置备份**：备份 M9A 文件夹中的 config 文件夹（如果存在）
7. **清理文件夹**：清理 M9A 文件夹中的所有文件
8. **解压文件**：解压 Lite ZIP 文件到 M9A 文件夹
9. **配置恢复**：将备份的 config 文件夹回写到 M9A 文件夹
10. **清理临时文件**：清理临时文件夹中的文件

---

## License

[WTFPL](./LICENSE)

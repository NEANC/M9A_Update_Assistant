> [!WARNING]
> 本项目使用 TRAE IDE 生成与迭代

> [!CAUTION]
> 请注意：由 AI 生成的代码可能有：不可预知的风险和错误！  
> 如您需要直接使用本项目，请**审查并测试后再使用**；  
> 如您要将本项目引用到其他项目，请**重构后再使用**。

---

# M9A Update Assistant

M9A CLI 更新小助手，一次部署解放双手！

- 解决 [#689](https://github.com/MAA1999/M9A/issues/689) 问题
  - 已知 M9A 未来不太可能将 Deps 文件夹打包到 CLI 版本中
    ![最小原则](IMG/最小原则.jpg)

---

## 功能特性

- 🚀 **自动更新** - 自动从 GitHub 获取最新版本并更新 M9A 与自身
- 📦 **多路径支持** - 支持同时更新多个 M9A 实例
- 💾 **配置备份** - 更新前自动备份配置文件
- 🔧 **Deps 自动处理** - 自动从 GUI 版本提取 deps 文件夹

---

## 如何使用

1. 从 [Release](https://github.com/NEANC/M9A_Update_Assistant/releases/latest) 下载
2. 首次运行会生成 `config.ini` 配置文件
3. 编辑 `config.ini` 文件，配置 M9A 文件夹路径、临时文件夹路径和代理服务器等信息
4. 再次运行程序，开始更新 M9A

---

## License

[WTFPL](./LICENSE)
